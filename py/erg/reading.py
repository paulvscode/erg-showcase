"""What a gateway knows about one machine, whatever protocol it speaks."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Reading:
    watts: int = 0
    cadence: int = 0
    distance_m: float | None = None
    speed_mps: float | None = None
    heart_rate: int | None = None
    calories: int | None = None


class MalformedPacket(ValueError):
    pass
