"""Members' profiles: record what they do, award badges, and assemble their page."""

import re

from erg import members as m
from erg.config import Config
from erg.levels import FORMAT_NAMES, LEVEL_MACHINES, bike_kmh, pace_for_watts
from erg.store import Store
from erg.workout import Workout

MEMBER_ID = re.compile(r"^[A-Za-z0-9-]{1,20}$")
NAME_MAX = 40
WATTS_RANGE = (30, 1000)


class ProfileBook:
    def __init__(self, config: Config, store: Store) -> None:
        self.config = config
        self.store = store
        self.levels = config.levels

    # --- members --------------------------------------------------------------

    def add_member(self, member_id: str, name: str, now: float) -> None:
        member_id, name = member_id.strip(), name.strip()
        if not MEMBER_ID.match(member_id):
            raise ValueError("Identifiant : lettres, chiffres et tirets (ex. A-0412).")
        if not name or len(name) > NAME_MAX:
            raise ValueError("Saisissez un nom (40 caractères maximum).")
        if self.store.member_name(member_id) is not None:
            raise ValueError(f"L’identifiant {member_id} existe déjà.")
        self.store.add_member(member_id, name, now)

    def name(self, member_id: str) -> str | None:
        return self.store.member_name(member_id)

    # --- recording --------------------------------------------------------------

    def record_test(self, member_id: str, machine: str, watts: float, ts: float) -> list[m.Award]:
        """Save a 4-minute test and the level it gives on that machine."""
        if self.name(member_id) is None:
            raise ValueError(f"Adhérent inconnu : {member_id}.")
        if machine not in LEVEL_MACHINES:
            raise ValueError("Les niveaux existent pour BikeErg, RowErg et SkiErg.")
        if not WATTS_RANGE[0] <= watts <= WATTS_RANGE[1]:
            raise ValueError("Puissance moyenne attendue entre 30 et 1 000 W.")
        level = self.levels.level_for(watts)
        result = m.StepTest(member_id, ts, machine, watts, level.number)
        history = self.store.tests(member_id)
        awards = m.step_test_awards(history, result, level.name, self.levels.window_s)
        self.store.add_test(result)
        self.store.add_awards(member_id, ts, awards)
        return awards

    def record_blocks(self, blocks: list[m.Performance]) -> list[m.Award]:
        """Save one member's session (one Performance per block) and award its badges."""
        if not blocks:
            return []
        member_id = blocks[0].member_id
        awards = m.session_awards(self.store.performances(member_id), blocks)
        for block in blocks:
            self.store.add_performance(block)
        self.store.add_awards(member_id, blocks[0].ts, awards)
        return awards

    def record_session(self, workout: Workout, view: dict) -> dict[int, list[str]]:
        """Save every member's result from a finished session; return badges per rider."""
        fmt = workout.fmt
        ts = workout.starts_at
        won: dict[int, list[str]] = {}
        for index, rider in enumerate(workout.riders):
            if rider.member_id is None:
                continue
            blocks, efforts = [], []
            for b, result in enumerate(workout.block_results(rider)):
                target = rider.targets[b]
                start, end = rider.distance.get(b, [0.0, 0.0])
                blocks.append(
                    m.Performance(
                        member_id=rider.member_id,
                        ts=ts,
                        preset=fmt.name,
                        session_type=fmt.kind,
                        machine=rider.machines[b],
                        minutes=fmt.efforts * fmt.effort_s / 60,
                        energy_wh=workout.block_energy_wh(rider, b),
                        avg_w=result.average_w,
                        distance_m=end - start,
                        steps_done=result.succeeded,
                        step_count=fmt.efforts,
                        completed=result.rewarded,
                        score=result.average_w / target if target else None,
                    )
                )
                efforts += [
                    (rider.member_id, ts, fmt.id, b, e, rider.machines[b], watts, target)
                    for e, watts in enumerate(result.efforts_w)
                ]
            awards = self.record_blocks(blocks)
            self.store.add_efforts(efforts)
            # A rider who barely moved didn't take the test: no level from it.
            machine, average_w = rider.machines[0], workout.average_w(rider)
            valid_test = WATTS_RANGE[0] <= average_w <= WATTS_RANGE[1]
            if fmt.is_test and machine in LEVEL_MACHINES and valid_test:
                awards += self.record_test(rider.member_id, machine, average_w, ts)
            if awards:
                won[index] = [a.label() for a in awards]
        return won

    def target(self, member_id: str, machine: str, kind: str, now: float) -> float | None:
        """The member's target watts for a format kind on a machine, or None without a level."""
        if machine not in LEVEL_MACHINES or kind not in FORMAT_NAMES:
            return None
        test = m.current_levels(self.store.tests(member_id), now, self.levels.window_s).get(machine)
        return self.levels.get(test.level).target_watts(kind) if test else None

    # --- pages ------------------------------------------------------------------

    def directory(self, now: float) -> list[dict]:
        badges = self.store.award_counts()
        sessions = self.store.session_counts()
        entries = []
        for member_id, name in self.store.members():
            current = m.current_levels(self.store.tests(member_id), now, self.levels.window_s)
            entries.append(
                {
                    "id": member_id,
                    "name": name,
                    "levels": {
                        machine: self._level_view(test.level) for machine, test in current.items()
                    },
                    "sessions": sessions.get(member_id, 0),
                    "badges": badges.get(member_id, 0),
                }
            )
        return entries

    def _level_view(self, number: int) -> dict:
        level = self.levels.get(number)
        return {"number": level.number, "name": level.name}

    def profile(self, member_id: str, now: float) -> dict | None:
        name = self.name(member_id)
        if name is None:
            return None
        history = self.store.performances(member_id)
        tests = self.store.tests(member_id)
        awards = self.store.awards(member_id)
        return {
            "id": member_id,
            "name": name,
            "levels": self._levels_view(tests, now),
            "tests": [
                {
                    "ts": t.ts,
                    "machine": t.machine,
                    "watts": t.watts,
                    "level": self._level_view(t.level),
                }
                for t in reversed(tests)
            ],
            "records": m.records(history),
            "totals": {
                "sessions": len({p.ts for p in history}),  # a session has one row per block
                "energy_kj": sum(p.energy_wh for p in history) * m.KJ_PER_WH,
                "distance_m": sum(p.distance_m for p in history),
            },
            "badges": self._badges_view(awards),
            "history": [
                {
                    "ts": p.ts,
                    "preset": p.preset,
                    "machine": p.machine,
                    "minutes": p.minutes,
                    "avg_w": p.avg_w,
                    "energy_kj": p.energy_wh * m.KJ_PER_WH,
                    "distance_m": p.distance_m,
                    "score": p.score,
                }
                for p in reversed(history[-30:])
            ],
            "formats": FORMAT_NAMES,
        }

    def _levels_view(self, tests: list[m.StepTest], now: float) -> dict:
        """Per machine: current level, targets per format, and the way to the next level."""
        views = {}
        for machine, test in m.current_levels(tests, now, self.levels.window_s).items():
            level = self.levels.get(test.level)
            nxt = self.levels.next_level(level)
            targets = {}
            for format_id in FORMAT_NAMES:
                watts = level.target_watts(format_id)
                pace = pace_for_watts(watts)
                targets[format_id] = {
                    "watts": watts,
                    "pace_s": pace,
                    "kmh": bike_kmh(pace) if machine == "bike" else None,
                }
            views[machine] = {
                "number": level.number,
                "name": level.name,
                "test_watts": test.watts,
                "test_ts": test.ts,
                "level_watts": level.watts,
                "next": (
                    {"number": nxt.number, "name": nxt.name, "watts": nxt.watts} if nxt else None
                ),
                "targets": targets,
            }
        return views

    def _badges_view(self, awards: list[tuple[float, m.Award]]) -> list[dict]:
        """Every badge, earned or not, with how many times and the latest details."""
        earned: dict[str, list[tuple[float, m.Award]]] = {}
        for ts, award in awards:
            earned.setdefault(award.badge, []).append((ts, award))
        views = []
        for badge_id, badge in m.BADGES.items():
            got = earned.get(badge_id, [])
            views.append(
                {
                    "id": badge_id,
                    "icon": badge.icon,
                    "name": badge.name,
                    "how": badge.how,
                    "count": len(got),
                    "last_ts": got[-1][0] if got else None,
                    "details": [a.detail for _, a in reversed(got[-3:]) if a.detail],
                }
            )
        views.sort(key=lambda b: (b["count"] == 0, -(b["last_ts"] or 0)))
        return views
