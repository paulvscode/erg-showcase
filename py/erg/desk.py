"""Starts, runs and ends group sessions.

Shared by the web server and the in-browser showcase, so both behave the same.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass

from erg.config import Config
from erg.presets import Preset
from erg.room import Room
from erg.workout import Rider, Track, Workout

log = logging.getLogger("erg.desk")

NAME_MAX = 24
WEIGHT_RANGE_KG = (30, 250)
DURATION_RANGE_MIN = (1, 180)
MACHINE_NAMES = {"bike": "BikeErg", "row": "RowErg", "ski": "SkiErg", "run": "AssaultRunner"}


class SessionError(Exception):
    """A request the desk refuses, with the HTTP status it maps to."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class RiderEntry:
    erg_id: str
    name: str
    weight_kg: float | None = None


class SessionDesk:
    def __init__(
        self,
        config: Config,
        room: Room,
        records: dict[str, float],
        on_finished: Callable[[Workout, dict], None],
    ) -> None:
        self.config = config
        self.room = room
        self.presets = {p.id: p for p in config.sessions.presets}
        self.records = records  # best time per race preset (Blitz, Squad)
        self.on_finished = on_finished
        self.workout: Workout | None = None

    def setup(self, now: float) -> dict:
        """What the /session page needs to offer a choice."""
        presets = []
        for p in self.presets.values():
            entry = {
                "id": p.id,
                "name": p.name,
                "type": p.type,
                "duration_min": p.duration_min,
                "steps": [s.label() for s in p.steps],
                "machines": {
                    m: [s.rider_label() for s in steps] for m, steps in p.machines.items()
                },
                "record_s": self.records.get(p.id),
            }
            presets.append(entry)
        ergs = [
            {"id": e.erg_id, "machine": e.machine, "online": self.room.is_online(e, now)}
            for e in self.room.ergs.values()
        ]
        return {"presets": presets, "ergs": ergs, "running": self.workout is not None}

    def start(
        self, preset_id: str, duration_min: float | None, entries: list[RiderEntry], now: float
    ) -> None:
        if self.workout is not None:
            raise SessionError(409, "A session is already running.")
        preset = self.presets.get(preset_id)
        if preset is None:
            raise SessionError(400, "Unknown session type.")
        entries = [e for e in entries if e.name.strip()]
        if not entries:
            raise SessionError(400, "Enter at least one name.")
        if len({e.erg_id for e in entries}) != len(entries):
            raise SessionError(400, "One name per machine.")
        riders = [self._rider(preset, e) for e in entries]
        duration = preset.duration_min
        if preset.type == "training" and duration_min:
            duration = duration_min
        if not DURATION_RANGE_MIN[0] <= duration <= DURATION_RANGE_MIN[1]:
            raise SessionError(400, "Duration must be 1 to 180 minutes.")

        for rider in riders:
            self.room.set_rider_weight(rider.erg_id, rider.weight_kg)
        starts_at = now + self.config.sessions.countdown_s
        self.workout = Workout(
            preset=preset,
            riders=riders,
            starts_at=starts_at,
            ends_at=starts_at + duration * 60,
            tick_s=self.config.timing.tick_s,
            record_s=self.records.get(preset.id),
        )
        log.info("session %s starting with %s", preset.id, ", ".join(r.name for r in riders))

    def _rider(self, preset: Preset, entry: RiderEntry) -> Rider:
        erg = self.room.ergs.get(entry.erg_id)
        if erg is None:
            raise SessionError(400, f"Unknown machine {entry.erg_id}.")
        weight = entry.weight_kg
        if weight is not None and not WEIGHT_RANGE_KG[0] <= weight <= WEIGHT_RANGE_KG[1]:
            raise SessionError(400, f"Weight for {entry.name} looks wrong.")
        track = None
        if preset.type == "squad":
            steps = preset.machines.get(erg.machine)
            if not steps:
                machine = MACHINE_NAMES.get(erg.machine, erg.machine)
                raise SessionError(400, f"This session has no objectives for the {machine}.")
            track = Track(steps)
        return Rider(entry.erg_id, entry.name.strip()[:NAME_MAX], weight, track=track)

    def stop(self) -> None:
        if self.workout is None:
            raise SessionError(409, "No session is running.")
        log.info("session %s stopped", self.workout.preset.id)
        self.cancel()

    def cancel(self) -> None:
        if self.workout is None:
            return
        for rider in self.workout.riders:
            self.room.set_rider_weight(rider.erg_id, None)
        self.workout = None

    def tick(self, now: float) -> None:
        workout = self.workout
        if workout is None:
            return
        workout.tick(self.room, now)
        if workout.phase != "finished":
            return
        if not workout.saved:
            self._finished(workout, now)
        if now - workout.finished_at > self.config.sessions.results_s:
            self.cancel()

    def _finished(self, workout: Workout, now: float) -> None:
        preset = workout.preset
        if preset.is_race and workout.completed:
            seconds = workout.elapsed(now)
            self.records[preset.id] = min(seconds, self.records.get(preset.id, seconds))
        self.on_finished(workout, workout.view(self.room, now))
        workout.saved = True
        log.info(
            "session %s finished: %d/%d objectives",
            preset.id,
            workout.steps_done,
            workout.step_count,
        )

    def view(self, now: float) -> dict | None:
        return self.workout.view(self.room, now) if self.workout else None
