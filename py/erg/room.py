"""Everything the server knows about the room: ergs, assignments, energy, objectives.

All methods take `now` explicitly so the same code serves live traffic and
the replay of the SQLite log after a restart.
"""

import logging
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field

from erg import text
from erg.config import Config, Participant
from erg.energy import EnergyMeter, estimate_running_watts
from erg.milestones import Session, distance_gain, ladder_status
from erg.objectives import PERIODS, Objective, RollingBest, period_key, progress

log = logging.getLogger("erg.room")

CALENDAR_PERIODS = tuple(p for p in PERIODS if p != "all")
PERIOD_GOAL = {
    "day": "Objectif du jour",
    "week": "Objectif de la semaine",
    "month": "Objectif du mois",
    "year": "Objectif de l'année",
    "all": "Objectif de la salle",
}


@dataclass(frozen=True)
class Record:
    """One persisted input: a power reading or an admin action."""

    ts: float
    kind: str  # "reading" | "assign" | "unassign" | "reset"
    erg_id: str | None = None
    participant_id: str | None = None
    watts: float = 0.0
    cadence: int = 0
    distance_m: float | None = None
    estimated: bool = False  # watts were estimated (a runner), not measured


@dataclass(frozen=True)
class ErgDetails:
    """Monitor values shown on the dashboard; energy and objectives don't use them."""

    distance_m: float | None = None
    speed_mps: float | None = None
    heart_rate: int | None = None
    calories: int | None = None


@dataclass
class ErgState:
    erg_id: str
    meter: EnergyMeter
    energy_wh: float = 0.0
    cadence: int = 0
    machine: str = "bike"
    details: ErgDetails = field(default_factory=ErgDetails)
    last_seen: float | None = None
    participant_id: str | None = None
    session: Session | None = None
    last_active_ts: float | None = None
    last_distance_m: float | None = None
    total_distance_m: float = 0.0  # since the server started, across sessions
    rider_weight_kg: float | None = None  # set by a group session, for runners' watts
    measured_power: bool = False  # a runner that sends real watts is never estimated
    power_estimated: bool = False


@dataclass
class ParticipantState:
    info: Participant
    best_power: dict[str, RollingBest]
    energy_wh: float = 0.0
    erg_id: str | None = None
    reached: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class Highlight:
    text: str
    ts: float
    erg_id: str | None = None  # celebrated on that erg's tile instead of the whole screen


class Room:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.timing = config.timing
        self.ergs: dict[str, ErgState] = {}
        for erg in config.all_ergs():
            self._erg(erg.id).machine = erg.machine
        self.teams = {t.id: t for t in config.teams}
        self.team_wh = {t.id: 0.0 for t in config.teams}
        self.total_wh = 0.0
        self.period_wh: dict[str, tuple[str, float]] = {}  # period -> (period key, Wh)
        self.participants = {p.id: self._new_participant(p) for p in config.participants}
        # (objective id, team id or period key), so calendar goals can be reached again.
        self.reached: set[tuple[str, str]] = set()
        self.highlights: list[Highlight] = []
        self.announce = True
        self.room_day = ""
        self._restart_room_ladder()

    def objectives(self, scope: str) -> list[Objective]:
        return [o for o in self.config.objectives if o.scope == scope]

    def _new_participant(self, info: Participant) -> ParticipantState:
        best = {
            o.id: RollingBest(max(1, round(o.window_s / self.timing.tick_s)))
            for o in self.objectives("personal")
            if o.type == "avg_power_w"
        }
        return ParticipantState(info=info, best_power=best)

    def _erg(self, erg_id: str) -> ErgState:
        if erg_id not in self.ergs:
            meter = EnergyMeter(stale_after_s=self.timing.stale_power_after_s)
            self.ergs[erg_id] = ErgState(erg_id=erg_id, meter=meter)
        return self.ergs[erg_id]

    def _participant(self, participant_id: str) -> ParticipantState:
        if participant_id not in self.participants:
            raise ValueError(f"Participant inconnu{text.NBSP}: {participant_id}")
        return self.participants[participant_id]

    def period_energy(self, period: str, now: float) -> float:
        """Room energy in the calendar period containing `now`."""
        if period == "all":
            return self.total_wh
        key, wh = self.period_wh.get(period, ("", 0.0))
        return wh if key == period_key(period, now) else 0.0

    def _settle(self, erg: ErgState, now: float) -> None:
        self._credit(erg, erg.meter.advance(now), now)

    def _credit(self, erg: ErgState, wh: float, now: float) -> None:
        if wh <= 0:
            return
        erg.energy_wh += wh
        self.total_wh += wh
        for period in CALENDAR_PERIODS:
            self.period_wh[period] = (period_key(period, now), self.period_energy(period, now) + wh)
        if erg.participant_id is None:
            return
        person = self.participants[erg.participant_id]
        person.energy_wh += wh
        self.team_wh[person.info.team] += wh

    # --- inputs -------------------------------------------------------------

    def apply(self, record: Record) -> None:
        if record.kind == "reading":
            self.on_reading(
                record.erg_id,
                record.watts,
                record.cadence,
                record.ts,
                record.distance_m,
                estimated=record.estimated,
            )
        elif record.kind == "assign":
            self.assign(record.participant_id, record.erg_id, record.ts)
        elif record.kind == "unassign":
            self.unassign(record.participant_id, record.ts)
        elif record.kind == "reset":
            self.reset(record.participant_id, record.ts)

    def on_reading(
        self,
        erg_id: str,
        watts: float | None,
        cadence: int,
        now: float,
        distance_m: float | None = None,
        speed_mps: float | None = None,
        estimated: bool = False,
    ) -> float | None:
        """Take one reading and return the watts used for energy, if any.

        `watts` is None when the reading carries no new power value. A runner
        that never reports power gets watts estimated from its speed.
        `estimated` marks watts that were already estimated (replayed from the log).
        """
        erg = self._erg(erg_id)
        if watts is not None and not estimated:
            erg.measured_power = True
        needs_estimate = (
            erg.machine == "run"
            and not erg.measured_power
            and watts is None
            and speed_mps is not None
        )
        if needs_estimate:
            weight = erg.rider_weight_kg or self.config.runner_weight_kg
            watts = estimate_running_watts(speed_mps, weight)
        erg.power_estimated = needs_estimate or (estimated and watts is not None)
        if (watts or 0) > 0 or (speed_mps or 0) > 0:
            last = erg.last_active_ts
            idle = last is None or now - last > self.config.milestones.session_idle_s
            if erg.session is None or idle:
                self._start_session(erg, now)
            erg.last_active_ts = now
        if watts is not None:
            self._credit(erg, erg.meter.sample(watts, now), now)
        erg.cadence = cadence
        erg.last_seen = now
        if distance_m is not None:
            gain = distance_gain(erg.last_distance_m, distance_m)
            erg.total_distance_m += gain
            if erg.session is not None:
                erg.session.distance_m += gain
            erg.last_distance_m = distance_m
        return watts

    def start_machine_session(self, erg_id: str, now: float) -> None:
        """Restart one machine's ladders, e.g. when a group session begins."""
        erg = self.ergs[erg_id]
        self._settle(erg, now)
        self._start_session(erg, now)
        erg.last_active_ts = now

    def set_rider_weight(self, erg_id: str, weight_kg: float | None) -> None:
        self.ergs[erg_id].rider_weight_kg = weight_kg

    def _power_window(self) -> RollingBest:
        window = self.config.milestones.power_window_s
        return RollingBest(max(1, round(window / self.timing.tick_s)))

    def _start_session(self, erg: ErgState, now: float) -> None:
        erg.session = Session(started_at=now, best_power=self._power_window())

    def _restart_room_ladder(self) -> None:
        self.room_best = self._power_window()
        self.room_level = 0

    def set_details(self, erg_id: str, details: ErgDetails) -> None:
        self._erg(erg_id).details = details

    def on_heartbeat(self, connected: Iterable[str], now: float) -> None:
        for erg_id in connected:
            self._erg(erg_id).last_seen = now

    def assign(self, participant_id: str, erg_id: str, now: float) -> None:
        person = self._participant(participant_id)
        if erg_id not in self.ergs:
            raise ValueError(f"Machine inconnue{text.NBSP}: {erg_id}")
        self.unassign(participant_id, now)
        erg = self.ergs[erg_id]
        if erg.participant_id is not None:
            self.unassign(erg.participant_id, now)
        # Settle energy so far before the new person starts collecting it.
        self._settle(erg, now)
        erg.participant_id = participant_id
        person.erg_id = erg_id
        self._start_session(erg, now)

    def unassign(self, participant_id: str, now: float) -> None:
        person = self._participant(participant_id)
        if person.erg_id is None:
            return
        erg = self.ergs[person.erg_id]
        self._settle(erg, now)
        erg.participant_id = None
        person.erg_id = None
        self._start_session(erg, now)

    def reset(self, participant_id: str, now: float) -> None:
        """Start a fresh personal session. Team and room totals keep what was produced."""
        person = self._participant(participant_id)
        if person.erg_id is not None:
            erg = self.ergs[person.erg_id]
            self._settle(erg, now)
            self._start_session(erg, now)
        fresh = self._new_participant(person.info)
        fresh.erg_id = person.erg_id
        self.participants[participant_id] = fresh

    def restart_objectives(self, now: float) -> None:
        """Every machine starts a new session and every rider a fresh personal session.

        Energy totals and gym goals keep what was produced.
        """
        for person_id in self.participants:
            self.reset(person_id, now)
        for erg in self.ergs.values():
            self._settle(erg, now)
            self._start_session(erg, now)
        self._restart_room_ladder()

    def clear(self) -> None:
        """Forget everything produced so far, as if the server just started on an empty log."""
        self.__init__(self.config)

    def tick(self, now: float) -> None:
        room_watts = 0.0
        for erg in self.ergs.values():
            self._settle(erg, now)
            watts = erg.meter.power_at(now)
            room_watts += watts
            if erg.session is not None:
                erg.session.best_power.push(watts)
            if erg.participant_id is None:
                continue
            for best in self.participants[erg.participant_id].best_power.values():
                best.push(watts)
        self._check_objectives(now)
        self._check_milestones(now)
        self._track_room_power(room_watts, now)

    def _track_room_power(self, watts: float, now: float) -> None:
        day = period_key("day", now)
        if day != self.room_day:
            self.room_day = day
            self._restart_room_ladder()
        self.room_best.push(watts)
        steps = self.config.milestones.room_power_w
        reached = ladder_status(steps, self.room_best.best).reached
        if reached > self.room_level:
            self.room_level = reached
            self.highlight(
                text.exclaim(f"⚡ Salle{text.NBSP}: {text.number(steps[reached - 1])} W"), now
            )

    # --- objectives ---------------------------------------------------------

    def personal_value(self, person: ParticipantState, objective: Objective) -> float:
        if objective.type == "avg_power_w":
            return person.best_power[objective.id].best
        return person.energy_wh

    def _check_objectives(self, now: float) -> None:
        for person in self.participants.values():
            for o in self.objectives("personal"):
                if o.id not in person.reached and progress(o, self.personal_value(person, o)) >= 1:
                    person.reached.add(o.id)
                    message = text.exclaim(f"★ {person.info.name}{text.NBSP}: {o.describe()}")
                    self.highlight(message, now, person.erg_id)
        for o in self.objectives("team"):
            for team_id, wh in self.team_wh.items():
                if (o.id, team_id) not in self.reached and progress(o, wh) >= 1:
                    self.reached.add((o.id, team_id))
                    team = self.teams[team_id].name
                    self.highlight(text.exclaim(f"Équipe {team}{text.NBSP}: {o.describe()}"), now)
        for o in self.objectives("overall"):
            key = (o.id, period_key(o.period, now))
            if key not in self.reached and progress(o, self.period_energy(o.period, now)) >= 1:
                self.reached.add(key)
                message = f"{PERIOD_GOAL[o.period]} atteint{text.NBSP}: {o.describe()}"
                self.highlight(text.exclaim(message), now)

    def _ladders(self, erg: ErgState) -> list[tuple[str, list[float], float]]:
        """(name, steps, session value) for each ladder of this erg's machine."""
        ladders = self.config.milestones.machines[erg.machine]
        result = [("distance", ladders.distance_m, erg.session.distance_m)]
        if ladders.power_w:
            result.insert(0, ("power", ladders.power_w, erg.session.best_power.best))
        return result

    def _check_milestones(self, now: float) -> None:
        for erg in self.ergs.values():
            if erg.session is None:
                continue
            for name, steps, value in self._ladders(erg):
                reached = ladder_status(steps, value).reached
                if reached <= erg.session.levels[name]:
                    continue
                erg.session.levels[name] = reached
                # Several steps at once (e.g. a fast start) get one celebration, for the top one.
                self.highlight(_step_text(name, steps[reached - 1]), now, erg.erg_id)

    def highlight(self, text: str, now: float, erg_id: str | None = None) -> None:
        self.highlights.append(Highlight(text, now, erg_id))
        if self.announce:
            log.info("reached: %s%s", text, f" on {erg_id}" if erg_id else "")

    # --- output -------------------------------------------------------------

    def is_online(self, erg: ErgState, now: float) -> bool:
        return erg.last_seen is not None and now - erg.last_seen <= self.timing.offline_after_s

    def snapshot(self, now: float) -> dict:
        self.highlights = [h for h in self.highlights if now - h.ts <= self.timing.highlight_s]
        ergs = [self._erg_view(erg, now) for erg in self.ergs.values()]
        teams = [
            {
                "id": team_id,
                "name": self.teams[team_id].name,
                "energy_wh": wh,
                "objectives": [_objective_view(o, wh) for o in self.objectives("team")],
            }
            for team_id, wh in self.team_wh.items()
        ]
        teams.sort(key=lambda t: t["energy_wh"], reverse=True)
        return {
            "ts": now,
            "total_watts": sum(e["watts"] for e in ergs),
            "total_wh": self.total_wh,
            "today_wh": self.period_energy("day", now),
            "room_ladder": self._room_ladder_view(),
            "overall": [
                _objective_view(o, self.period_energy(o.period, now))
                for o in self.objectives("overall")
            ],
            "teams": teams,
            "ergs": ergs,
            "participants": [self._participant_view(p) for p in self.participants.values()],
            "highlights": [asdict(h) for h in self.highlights],
        }

    def _erg_view(self, erg: ErgState, now: float) -> dict:
        online = self.is_online(erg, now)
        person = self.participants.get(erg.participant_id) if erg.participant_id else None
        return {
            "id": erg.erg_id,
            "machine": erg.machine,
            "has_power": erg.meter.sample_ts is not None,
            "estimated": erg.power_estimated,
            "online": online,
            "watts": erg.meter.power_at(now) if online else 0.0,
            "cadence": erg.cadence if online else 0,
            "details": asdict(erg.details) if online else None,
            "session": self._session_view(erg) if erg.session else None,
            "energy_wh": erg.energy_wh,
            "participant": self._participant_view(person) if person else None,
        }

    def _session_view(self, erg: ErgState) -> dict:
        view = {name: _ladder_view(steps, value) for name, steps, value in self._ladders(erg)}
        if "power" in view:
            view["power"]["measuring"] = not erg.session.best_power.is_full
        return view

    def _room_ladder_view(self) -> dict | None:
        steps = self.config.milestones.room_power_w
        if not steps:
            return None
        view = _ladder_view(steps, self.room_best.best)
        view["measuring"] = not self.room_best.is_full
        return view

    def _participant_view(self, person: ParticipantState) -> dict:
        return {
            "id": person.info.id,
            "name": person.info.name,
            "team": person.info.team,
            "team_name": self.teams[person.info.team].name,
            "erg_id": person.erg_id,
            "energy_wh": person.energy_wh,
            "objectives": [
                _objective_view(o, self.personal_value(person, o))
                for o in self.objectives("personal")
            ],
        }


def _ladder_view(steps: list[float], value: float) -> dict:
    status = ladder_status(steps, value)
    return {
        "steps": steps,
        "value": value,
        "reached": status.reached,
        "next": status.next_step,
        "progress": status.progress,
    }


def _step_text(ladder: str, step: float) -> str:
    if ladder == "power":
        return text.exclaim(f"⚡ {text.number(step)} W")
    return text.exclaim(f"🏁 {text.distance(step)}")


def _objective_view(objective: Objective, value: float) -> dict:
    return {
        "id": objective.id,
        "type": objective.type,
        "period": objective.period,
        "window_s": objective.window_s,
        "target": objective.target,
        "value": value,
        "progress": progress(objective, value),
    }


def replay(room: Room, records: Iterable[Record]) -> int:
    """Rebuild state from the log, ticking at the same rate as the live server."""
    room.announce = False
    count = 0
    next_tick: float | None = None
    for record in records:
        if next_tick is None:
            next_tick = record.ts
        while next_tick <= record.ts:
            room.tick(next_tick)
            next_tick += room.timing.tick_s
        room.apply(record)
        count += 1
    room.announce = True
    return count
