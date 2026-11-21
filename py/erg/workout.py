"""A group session: blocks of timed efforts with rests, and rotations between blocks.

Nothing is recorded during rests and rotations, only during efforts: each
rider's power, once per tick, on the machine they use for that block.
"""

from dataclasses import dataclass, field

from erg.measures import BlockResult, average, block_result
from erg.presets import Format
from erg.room import Room

# Each rider moves along their lane in this order: Row-n -> Bike-n -> Ski-n.
ROTATION = ("row", "bike", "ski")


@dataclass(frozen=True)
class Phase:
    kind: str  # "effort", "rest" or "transition" (changing machine)
    block: int
    effort: int  # -1 during a transition
    start: float  # seconds after GO
    duration: float

    @property
    def end(self) -> float:
        return self.start + self.duration


def timeline(fmt: Format, transition_s: float) -> list[Phase]:
    phases: list[Phase] = []
    t = 0.0
    for block in range(fmt.blocks):
        if block:
            phases.append(Phase("transition", block, -1, t, transition_s))
            t += transition_s
        for effort in range(fmt.efforts):
            phases.append(Phase("effort", block, effort, t, fmt.effort_s))
            t += fmt.effort_s
            if effort < fmt.efforts - 1 and fmt.rest_s:
                phases.append(Phase("rest", block, effort, t, fmt.rest_s))
                t += fmt.rest_s
    return phases


def lanes(ergs: list[tuple[str, str]]) -> dict[str, list[str]]:
    """Each erg's lane: the n-th RowErg, BikeErg and SkiErg, in rotation order.

    With unequal numbers per machine type, some lanes are shorter and their
    riders rotate between the machines the lane has.
    """
    by_machine = {machine: [e for e, m in ergs if m == machine] for machine in ROTATION}
    lane_of: dict[str, list[str]] = {}
    for n in range(max((len(v) for v in by_machine.values()), default=0)):
        lane = [by_machine[m][n] for m in ROTATION if n < len(by_machine[m])]
        for erg_id in lane:
            lane_of[erg_id] = lane
    return lane_of


def erg_for_block(start: str, block: int, lane_of: dict[str, list[str]], rotate: bool) -> str:
    if not rotate:
        return start
    lane = lane_of.get(start, [start])
    return lane[(lane.index(start) + block) % len(lane)]


@dataclass
class Rider:
    name: str
    ergs: list[str]  # the machine used in each block
    machines: list[str]  # machine type in each block
    targets: list[float | None]  # target watts in each block (None: no level on that machine)
    member_id: str | None = None
    weight_kg: float | None = None
    samples: dict[tuple[int, int], list[float]] = field(default_factory=dict)  # (block, effort)
    distance: dict[int, list[float]] = field(default_factory=dict)  # block -> [start, end] metres


@dataclass
class Workout:
    fmt: Format
    riders: list[Rider]
    starts_at: float
    phases: list[Phase]
    tick_s: float
    phase: str = "countdown"  # countdown -> running -> finished
    finished_at: float | None = None
    saved: bool = False
    awards: dict[int, list[str]] = field(default_factory=dict)  # badges won, per rider index

    @property
    def ends_at(self) -> float:
        return self.starts_at + self.phases[-1].end

    def elapsed(self, now: float) -> float:
        end = self.finished_at if self.finished_at is not None else now
        return max(0.0, end - self.starts_at)

    def current(self, now: float) -> Phase | None:
        offset = now - self.starts_at
        return next((p for p in self.phases if p.start <= offset < p.end), None)

    def tick(self, room: Room, now: float) -> None:
        if self.phase == "countdown" and now >= self.starts_at:
            self.phase = "running"
        if self.phase != "running":
            return
        phase = self.current(now)
        if phase is not None and phase.kind == "effort":
            for rider in self.riders:
                erg = room.ergs[rider.ergs[phase.block]]
                rider.samples.setdefault((phase.block, phase.effort), []).append(
                    erg.meter.power_at(now)
                )
                span = rider.distance.setdefault(phase.block, [erg.total_distance_m] * 2)
                span[1] = erg.total_distance_m
        if now >= self.ends_at:
            self.finished_at = now
            self.phase = "finished"

    # --- results ----------------------------------------------------------------

    def block_results(self, rider: Rider) -> list[BlockResult]:
        """Results of the blocks that have started, in order."""
        results = []
        for block in range(self.fmt.blocks):
            # Only efforts that have happened: a block in progress isn't judged on the rest.
            efforts = [s for e in range(self.fmt.efforts) if (s := rider.samples.get((block, e)))]
            if not efforts:
                break
            results.append(block_result(self.fmt.kind, efforts, rider.targets[block]))
        return results

    def block_energy_wh(self, rider: Rider, block: int) -> float:
        samples = [rider.samples.get((block, e), []) for e in range(self.fmt.efforts)]
        return sum(sum(s) for s in samples) * self.tick_s / 3600

    def energy_wh(self, rider: Rider) -> float:
        return sum(sum(s) for s in rider.samples.values()) * self.tick_s / 3600

    def average_w(self, rider: Rider) -> float:
        return average([w for s in rider.samples.values() for w in s])

    # --- output -----------------------------------------------------------------

    def view(self, now: float) -> dict:
        phase = self.current(now) if self.phase == "running" else None
        # During a rotation, riders are shown on the machine they're heading to.
        block = phase.block if phase else (self.fmt.blocks - 1 if self.phase == "finished" else 0)
        step = None
        if phase is not None:
            step = {
                "kind": phase.kind,
                "block": phase.block + 1,
                "blocks": self.fmt.blocks,
                "effort": phase.effort + 1,
                "efforts": self.fmt.efforts,
                "remaining_s": max(0.0, phase.end - (now - self.starts_at)),
                "duration_s": phase.duration,
            }
        riders = []
        for index, rider in enumerate(self.riders):
            current = rider.samples.get((phase.block, phase.effort), []) if phase else []
            riders.append(
                {
                    "index": index,
                    "name": rider.name,
                    "member_id": rider.member_id,
                    "erg_id": rider.ergs[block],
                    "machine": rider.machines[block],
                    "target_w": rider.targets[block],
                    "effort_w": average(current) if phase and phase.kind == "effort" else None,
                    "average_w": self.average_w(rider),
                    "energy_wh": self.energy_wh(rider),
                    "blocks": [
                        {
                            "machine": rider.machines[b],
                            "target_w": rider.targets[b],
                            "average_w": r.average_w,
                            "efforts_w": r.efforts_w,
                            "succeeded": r.succeeded,
                            "rewarded": r.rewarded,
                            "regularity": r.regularity,
                            "drop": r.drop,
                            "too_hard": r.too_hard,
                        }
                        for b, r in enumerate(self.block_results(rider))
                    ],
                    "awards": self.awards.get(index, []),
                }
            )
        return {
            "preset": self.fmt.name,
            "type": self.fmt.kind,
            "phase": self.phase,
            "countdown_s": max(0.0, self.starts_at - now),
            "elapsed_s": self.elapsed(now),
            "remaining_s": max(0.0, self.ends_at - now),
            "step": step,
            "riders": riders,
            "total_wh": sum(r["energy_wh"] for r in riders),
        }
