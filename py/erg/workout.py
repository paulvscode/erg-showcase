"""Group sessions started from the /session page: Training, Blitz and Squad.

Training and Blitz give the room one list of objectives, cleared together.
Training runs for a fixed time and shows each rider's share of the energy;
Blitz finishes as soon as the list is done, against a record time. Squad gives
each rider their own list for their machine; the group objective is won when
every rider has finished theirs.
"""

from dataclasses import dataclass, field

from erg import text
from erg.presets import Preset, Step
from erg.room import Room


@dataclass
class Track:
    """Progress through a list of objectives, for the whole room or for one rider."""

    steps: list[Step]
    index: int = 0
    energy_start: float = 0.0
    distance_start: float = 0.0
    held_s: float = 0.0
    below_s: float = 0.0  # how long the level has been under target, for the grace period
    splits: list[float] = field(default_factory=list)  # elapsed time when each step was cleared

    @property
    def done(self) -> bool:
        return self.index == len(self.steps)

    @property
    def current(self) -> Step | None:
        return None if self.done else self.steps[self.index]

    def restart_step(self, energy: float, distance: float) -> None:
        self.energy_start = energy
        self.distance_start = distance
        self.held_s = 0.0
        self.below_s = 0.0

    def value(self, energy: float, distance: float) -> float:
        step = self.current
        if step.kind == "energy":
            return energy - self.energy_start
        if step.kind == "distance":
            return distance - self.distance_start
        return self.held_s

    def progress(self, energy: float, distance: float) -> float:
        return min(1.0, max(0.0, self.value(energy, distance) / self.current.goal))

    def advance(
        self,
        energy: float,
        distance: float,
        level: float,
        tick_s: float,
        elapsed: float,
        grace_s: float = 0.0,
    ) -> Step | None:
        """Take the latest totals; return the step this cleared, if any.

        `level` is what a timed objective compares with its target (watts, or
        km/h for speed). A dip below target pauses the timer; only a dip longer
        than `grace_s` starts it over, so stroke-to-stroke wobble isn't punished.
        """
        step = self.current
        if step is None:
            return None
        if step.timed:
            if level >= step.target:
                self.held_s += tick_s
                self.below_s = 0.0
            else:
                self.below_s += tick_s
                if self.below_s > grace_s:
                    self.held_s = 0.0
        if self.progress(energy, distance) < 1:
            return None
        self.splits.append(elapsed)
        self.index += 1
        self.restart_step(energy, distance)
        return step

    def view(self, energy: float, distance: float, started: bool, label: str) -> dict | None:
        step = self.current
        if step is None:
            return None
        # Before GO nothing counts yet, so the first objective shows at zero.
        return {
            "label": label,
            "kind": step.kind,
            "progress": self.progress(energy, distance) if started else 0.0,
            "value": self.value(energy, distance) if started else 0.0,
            "goal": step.goal,
        }


@dataclass
class Rider:
    erg_id: str
    name: str
    weight_kg: float | None = None
    track: Track | None = None  # Squad only: this rider's own objectives
    energy_start: float = 0.0
    distance_start: float = 0.0


@dataclass
class Workout:
    preset: Preset
    riders: list[Rider]
    starts_at: float
    ends_at: float
    tick_s: float
    hold_grace_s: float = 0.0
    record_s: float | None = None  # best time before this run (Blitz, Squad)
    phase: str = "countdown"  # countdown -> running -> finished
    group: Track | None = None  # Training and Blitz: the room's objectives
    finished_at: float | None = None
    final_riders: list[dict] | None = None
    saved: bool = False

    def __post_init__(self) -> None:
        if self.preset.type != "squad":
            self.group = Track(self.preset.steps)

    @property
    def squad(self) -> bool:
        return self.preset.type == "squad"

    @property
    def completed(self) -> bool:
        if self.squad:
            return all(r.track.done for r in self.riders)
        return self.group.done

    @property
    def steps_done(self) -> int:
        if self.squad:
            return sum(r.track.index for r in self.riders)
        return self.group.index

    @property
    def step_count(self) -> int:
        if self.squad:
            return sum(len(r.track.steps) for r in self.riders)
        return len(self.group.steps)

    def elapsed(self, now: float) -> float:
        end = self.finished_at if self.finished_at is not None else now
        return max(0.0, end - self.starts_at)

    # --- progress -----------------------------------------------------------

    def tick(self, room: Room, now: float) -> None:
        if self.phase == "countdown" and now >= self.starts_at:
            self._go(room)
        if self.phase != "running":
            return
        if self.squad:
            self._advance_riders(room, now)
        else:
            self._advance_group(room, now)
        if (self.preset.is_race and self.completed) or now >= self.ends_at:
            self._finish(room, now)

    def _go(self, room: Room) -> None:
        for rider in self.riders:
            erg = room.ergs[rider.erg_id]
            rider.energy_start = erg.energy_wh
            rider.distance_start = erg.total_distance_m
            room.start_machine_session(rider.erg_id, self.starts_at)
            if rider.track:
                rider.track.restart_step(0.0, 0.0)
        if self.group:
            self.group.restart_step(0.0, 0.0)
        self.phase = "running"

    def _advance_group(self, room: Room, now: float) -> None:
        step = self.group.current
        if step is None:
            return
        powers = [room.ergs[r.erg_id].meter.power_at(now) for r in self.riders]
        level = min(powers) if step.kind == "all_in" else sum(powers)
        cleared = self.group.advance(
            self._energy(room),
            self._distance(room),
            level,
            self.tick_s,
            self.elapsed(now),
            self.hold_grace_s,
        )
        if cleared:
            room.highlight(f"✓ {cleared.label()}", now)

    def _advance_riders(self, room: Room, now: float) -> None:
        for rider in self.riders:
            track = rider.track
            if track.done:
                continue
            erg = room.ergs[rider.erg_id]
            if track.current.kind == "speed":
                level = (erg.details.speed_mps or 0.0) * 3.6 if room.is_online(erg, now) else 0.0
            else:
                level = erg.meter.power_at(now)
            energy, distance = self._rider_energy(room, rider), self._rider_distance(room, rider)
            cleared = track.advance(
                energy, distance, level, self.tick_s, self.elapsed(now), self.hold_grace_s
            )
            if cleared is None:
                continue
            room.highlight(f"✓ {cleared.rider_label()}", now, rider.erg_id)
            if track.done:
                done = text.exclaim(f"★ {rider.name}{text.NBSP}: objectifs terminés")
                room.highlight(done, now, rider.erg_id)
        if self.completed:
            room.highlight(text.exclaim("★ Objectif collectif atteint"), now)

    def _finish(self, room: Room, now: float) -> None:
        self.final_riders = self._rider_views(room, now)
        self.finished_at = now
        self.phase = "finished"

    def _rider_energy(self, room: Room, rider: Rider) -> float:
        return room.ergs[rider.erg_id].energy_wh - rider.energy_start

    def _rider_distance(self, room: Room, rider: Rider) -> float:
        return room.ergs[rider.erg_id].total_distance_m - rider.distance_start

    def _energy(self, room: Room) -> float:
        return sum(self._rider_energy(room, r) for r in self.riders)

    def _distance(self, room: Room) -> float:
        return sum(self._rider_distance(room, r) for r in self.riders)

    # --- output -------------------------------------------------------------

    def is_new_record(self) -> bool:
        if not self.preset.is_race or not self.completed or self.finished_at is None:
            return False
        return self.record_s is None or self.elapsed(self.finished_at) < self.record_s

    def _rider_views(self, room: Room, now: float) -> list[dict]:
        started = self.phase != "countdown"
        elapsed = self.elapsed(now)
        energies = [self._rider_energy(room, r) if started else 0.0 for r in self.riders]
        total = sum(energies)
        views = []
        for rider, energy in zip(self.riders, energies, strict=True):
            distance = self._rider_distance(room, rider) if started else 0.0
            view = {
                "erg_id": rider.erg_id,
                "name": rider.name,
                "energy_wh": energy,
                "share": energy / total if total > 0 else 0.0,
                "avg_w": energy * 3600 / elapsed if elapsed > 0 else 0.0,
                "distance_m": distance,
            }
            if rider.track:
                track = rider.track
                label = track.current.rider_label() if track.current else ""
                view |= {
                    "steps_done": track.index,
                    "step_count": len(track.steps),
                    "objective": track.view(energy, distance, started, label),
                    "finished_s": track.splits[-1] if track.done else None,
                }
            views.append(view)
        return views

    def view(self, room: Room, now: float) -> dict:
        started = self.phase == "running"
        riders = self.final_riders
        if riders is None:
            riders = self._rider_views(room, now)
        state = {
            "preset": self.preset.name,
            "type": self.preset.type,
            "phase": self.phase,
            "countdown_s": max(0.0, self.starts_at - now),
            "elapsed_s": self.elapsed(now),
            "remaining_s": max(0.0, self.ends_at - now),
            "steps_done": self.steps_done,
            "step_count": self.step_count,
            "objective": None,
            "splits": [],
            "group": None,
            "completed": self.completed,
            "record_s": self.record_s,
            "new_record": self.is_new_record(),
            "riders": riders,
            "total_wh": sum(r["energy_wh"] for r in riders),
        }
        if self.squad:
            state["group"] = {
                "progress": self.steps_done / self.step_count,
                "riders_done": sum(1 for r in self.riders if r.track.done),
                "rider_count": len(self.riders),
            }
        else:
            group = self.group
            state["splits"] = [
                {"label": self.preset.steps[i].label(), "at_s": at}
                for i, at in enumerate(group.splits)
            ]
            if self.phase != "finished":
                label = group.current.label() if group.current else ""
                state["objective"] = group.view(
                    self._energy(room), self._distance(room), started, label
                )
        return state
