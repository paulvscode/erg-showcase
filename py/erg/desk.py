"""Starts, runs and ends group sessions.

Shared by the web server and the in-browser showcase, so both behave the same.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass

from erg import text
from erg.config import Config
from erg.levels import LEVEL_MACHINES
from erg.presets import Format
from erg.room import Room
from erg.workout import Rider, Workout, erg_for_block, lanes, timeline

log = logging.getLogger("erg.desk")

NAME_MAX = 24
WEIGHT_RANGE_KG = (30, 250)


class SessionError(Exception):
    """A request the desk refuses, with the HTTP status it maps to."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class RiderEntry:
    erg_id: str  # the machine the rider starts on
    name: str
    weight_kg: float | None = None
    member_id: str | None = None


class SessionDesk:
    def __init__(
        self,
        config: Config,
        room: Room,
        on_finished: Callable[[Workout, dict], dict[int, list[str]] | None],
        find_member: Callable[[str], str | None] = lambda member_id: None,
        target_for: Callable[[str, str, str], float | None] = lambda member, machine, kind: None,
    ) -> None:
        self.config = config
        self.room = room
        self.formats = {f.id: f for f in config.sessions.formats}
        self.on_finished = on_finished  # returns the badges won per rider index, if any
        self.find_member = find_member  # member id -> name, or None if unknown
        self.target_for = target_for  # (member id, machine, format kind) -> target watts
        self.lane_of = lanes([(e.id, e.machine) for e in config.all_ergs()])
        self.workout: Workout | None = None

    def setup(self, now: float) -> dict:
        """What the /session page needs to offer a choice."""
        formats = [
            {"id": f.id, "name": f.name, "kind": f.kind, "describe": f.describe()}
            for f in self.formats.values()
        ]
        ergs = [
            {"id": e.erg_id, "machine": e.machine, "online": self.room.is_online(e, now)}
            for e in self.room.ergs.values()
        ]
        return {"formats": formats, "ergs": ergs, "running": self.workout is not None}

    def start(self, format_id: str, entries: list[RiderEntry], now: float) -> None:
        if self.workout is not None:
            raise SessionError(409, "Une séance est déjà en cours.")
        fmt = self.formats.get(format_id)
        if fmt is None:
            raise SessionError(400, "Type de séance inconnu.")
        entries = [e for e in entries if e.name.strip() or e.member_id]
        if not entries:
            raise SessionError(400, "Saisissez au moins un prénom.")
        if len({e.erg_id for e in entries}) != len(entries):
            raise SessionError(400, "Un seul prénom par machine.")
        members = [e.member_id for e in entries if e.member_id]
        if len(set(members)) != len(members):
            raise SessionError(400, "Un adhérent ne peut être que sur une machine.")
        riders = [self._rider(fmt, e) for e in entries]

        for rider in riders:
            self.room.set_rider_weight(rider.ergs[0], rider.weight_kg)
        starts_at = now + self.config.sessions.countdown_s
        self.workout = Workout(
            fmt=fmt,
            riders=riders,
            starts_at=starts_at,
            phases=timeline(fmt, self.config.sessions.transition_s),
            tick_s=self.config.timing.tick_s,
        )
        log.info("session %s starting with %s", fmt.id, ", ".join(r.name for r in riders))

    def _rider(self, fmt: Format, entry: RiderEntry) -> Rider:
        if entry.erg_id not in self.room.ergs:
            raise SessionError(400, f"Machine inconnue{text.NBSP}: {entry.erg_id}.")
        weight = entry.weight_kg
        if weight is not None and not WEIGHT_RANGE_KG[0] <= weight <= WEIGHT_RANGE_KG[1]:
            raise SessionError(400, f"Le poids de {entry.name} semble incorrect.")
        name = entry.name.strip()[:NAME_MAX]
        if entry.member_id:
            name = self.find_member(entry.member_id)
            if name is None:
                raise SessionError(400, f"Adhérent inconnu{text.NBSP}: {entry.member_id}.")
        ergs = [
            erg_for_block(entry.erg_id, block, self.lane_of, fmt.rotate)
            for block in range(fmt.blocks)
        ]
        machines = [self.room.ergs[e].machine for e in ergs]
        targets = [
            self.target_for(entry.member_id, machine, fmt.kind)
            if entry.member_id and not fmt.is_test
            else None
            for machine in machines
        ]
        return Rider(name, ergs, machines, targets, entry.member_id, weight)

    def stop(self) -> None:
        if self.workout is None:
            raise SessionError(409, "Aucune séance en cours.")
        log.info("session %s stopped", self.workout.fmt.id)
        self.cancel()

    def cancel(self) -> None:
        if self.workout is None:
            return
        for rider in self.workout.riders:
            self.room.set_rider_weight(rider.ergs[0], None)
        self.workout = None

    def tick(self, now: float) -> None:
        workout = self.workout
        if workout is None:
            return
        workout.tick(self.room, now)
        if workout.phase != "finished":
            return
        if not workout.saved:
            workout.awards = self.on_finished(workout, workout.view(now)) or {}
            workout.saved = True
            log.info("session %s finished", workout.fmt.id)
        if now - workout.finished_at > self.config.sessions.results_s:
            self.cancel()

    def view(self, now: float) -> dict | None:
        if self.workout is None:
            return None
        state = self.workout.view(now)
        if self.workout.fmt.is_test:
            # Show each rider the level their average so far would give.
            levels = self.config.levels
            for rider in state["riders"]:
                if rider["machine"] in LEVEL_MACHINES and rider["average_w"] > 0:
                    level = levels.level_for(rider["average_w"])
                    rider["level"] = {"number": level.number, "name": level.name}
        return state
