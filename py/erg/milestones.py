"""Step-by-step goals for one session on one machine: power and distance ladders."""

from dataclasses import dataclass, field

from erg.objectives import RollingBest


@dataclass(frozen=True)
class LadderStatus:
    reached: int
    next_step: float | None
    progress: float  # 0..1 from the last reached step to the next one


def ladder_status(steps: list[float], value: float) -> LadderStatus:
    reached = sum(1 for step in steps if value >= step)
    if reached == len(steps):
        return LadderStatus(reached, None, 1.0)
    floor = steps[reached - 1] if reached else 0.0
    ceiling = steps[reached]
    return LadderStatus(reached, ceiling, max(0.0, (value - floor) / (ceiling - floor)))


def distance_gain(previous: float | None, current: float) -> float:
    """Metres covered between two monitor distance readings.

    The PM5 counts from the start of its current workout and drops back to
    zero when a new one starts, so a lower reading means "restarted".
    """
    if previous is None:
        return 0.0
    if current < previous:
        return current
    return current - previous


@dataclass
class Session:
    started_at: float
    best_power: RollingBest
    distance_m: float = 0.0
    levels: dict[str, int] = field(default_factory=lambda: {"power": 0, "distance": 0})
