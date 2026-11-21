"""What each format measures, from one rider's power samples (one per second).

Endurance: average W per block and its regularity (spread of the 1-minute
averages). Intervalles: average W per effort, success rate and the drop from
the first to the last effort. Seuils: average W per effort and their regularity.
"""

from dataclasses import dataclass

# Intervalles: a block is won when at least this share of its efforts hit the target.
INTERVALS_SUCCESS_SHARE = 0.9
# Intervalles: losing more than this between the first and last effort means the
# target is too high for the rider.
INTERVALS_MAX_DROP = 0.10
MINUTE_SAMPLES = 60


def average(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def spread(values: list[float]) -> float | None:
    """(max - min) / average: 0 is perfectly steady."""
    mean = average(values)
    if len(values) < 2 or mean <= 0:
        return None
    return (max(values) - min(values)) / mean


def minute_averages(samples: list[float]) -> list[float]:
    """Averages of each full minute (a trailing partial minute is left out)."""
    full = len(samples) // MINUTE_SAMPLES
    return [average(samples[i * MINUTE_SAMPLES : (i + 1) * MINUTE_SAMPLES]) for i in range(full)]


def drop(first: float, last: float) -> float | None:
    """Share of power lost from the first effort to the last one."""
    return (first - last) / first if first > 0 else None


@dataclass(frozen=True)
class BlockResult:
    average_w: float
    efforts_w: list[float]  # average of each effort
    succeeded: int  # efforts at or above target
    rewarded: bool  # the format's reward for this block
    regularity: float | None = None  # spread, Endurance and Seuils
    drop: float | None = None  # Intervalles
    too_hard: bool = False  # Intervalles: drop over the limit


def block_result(kind: str, efforts: list[list[float]], target_w: float | None) -> BlockResult:
    """Measure one block for one rider. `efforts` holds each effort's samples."""
    efforts_w = [average(samples) for samples in efforts]
    all_samples = [w for samples in efforts for w in samples]
    succeeded = 0 if target_w is None else sum(1 for w in efforts_w if w >= target_w)
    result = dict(average_w=average(all_samples), efforts_w=efforts_w, succeeded=succeeded)
    if kind == "intervals":
        lost = drop(efforts_w[0], efforts_w[-1]) if len(efforts_w) > 1 else None
        rewarded = target_w is not None and succeeded >= INTERVALS_SUCCESS_SHARE * len(efforts_w)
        too_hard = lost is not None and lost > INTERVALS_MAX_DROP
        return BlockResult(**result, rewarded=rewarded, drop=lost, too_hard=too_hard)
    if kind == "threshold":
        rewarded = target_w is not None and succeeded == len(efforts_w)
        return BlockResult(**result, rewarded=rewarded, regularity=spread(efforts_w))
    if kind == "endurance":
        rewarded = target_w is not None and result["average_w"] >= target_w
        steadiness = spread(minute_averages(all_samples))
        return BlockResult(**result, rewarded=rewarded, regularity=steadiness)
    return BlockResult(**result, rewarded=False)  # the step test only sets a level
