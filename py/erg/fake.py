"""Simulated machines, for testing objectives and the dashboard without real hardware."""

import random
from dataclasses import dataclass

from erg.reading import Reading

# Concept2's published conversions: watts = 2.8 * speed^3, and
# calories/hour = 4 * 0.8604 * watts + 300.
WATTS_PER_SPEED_CUBED = 2.8
CALORIES_PER_HOUR_PER_WATT = 4 * 0.8604
RESTING_CALORIES_PER_HOUR = 300

# Rough running cost for a 70 kg runner: about 1 kcal per kg per km.
RUNNING_KCAL_PER_M = 0.07


@dataclass
class FakeErg:
    """Power that wanders like a person exercising: drifting effort plus stroke noise."""

    min_watts: float
    max_watts: float
    rng: random.Random
    min_cadence: float = 60
    max_cadence: float = 95
    effort: float = 0.0
    distance_m: float = 0.0
    calories: float = 0.0

    def __post_init__(self) -> None:
        self.effort = self.rng.uniform(self.min_watts, self.max_watts)

    def next_reading(self, elapsed_s: float) -> Reading:
        self.effort += self.rng.gauss(0, 8)
        self.effort = min(self.max_watts, max(self.min_watts, self.effort))
        stroke = self.effort + self.rng.gauss(0, 12)
        watts = round(min(self.max_watts, max(self.min_watts, stroke)))

        speed = (watts / WATTS_PER_SPEED_CUBED) ** (1 / 3)
        self.distance_m += speed * elapsed_s
        calories_per_hour = CALORIES_PER_HOUR_PER_WATT * watts + RESTING_CALORIES_PER_HOUR
        self.calories += calories_per_hour * elapsed_s / 3600
        intensity = (self.effort - self.min_watts) / (self.max_watts - self.min_watts)
        cadence = self.min_cadence + intensity * (self.max_cadence - self.min_cadence)
        return Reading(
            watts=watts,
            cadence=round(cadence + self.rng.gauss(0, 1.5)),
            distance_m=self.distance_m,
            speed_mps=speed,
            heart_rate=round(90 + self.effort / 4 + self.rng.gauss(0, 2)),
            calories=int(self.calories),
        )


@dataclass
class FakeRunner:
    """A treadmill that reports speed and distance but no power."""

    rng: random.Random
    min_speed_mps: float = 2.2  # about 8 km/h
    max_speed_mps: float = 4.5  # about 16 km/h
    speed: float = 0.0
    distance_m: float = 0.0

    def __post_init__(self) -> None:
        self.speed = self.rng.uniform(self.min_speed_mps, self.max_speed_mps)

    def next_reading(self, elapsed_s: float) -> Reading:
        self.speed += self.rng.gauss(0, 0.08)
        self.speed = min(self.max_speed_mps, max(self.min_speed_mps, self.speed))
        self.distance_m += self.speed * elapsed_s
        intensity = (self.speed - self.min_speed_mps) / (self.max_speed_mps - self.min_speed_mps)
        return Reading(
            distance_m=self.distance_m,
            speed_mps=self.speed,
            heart_rate=round(120 + intensity * 55 + self.rng.gauss(0, 2)),
            calories=int(self.distance_m * RUNNING_KCAL_PER_M),
        )


def fake_erg_ids(count: int) -> list[str]:
    return [f"FAKE-{i:02d}" for i in range(1, count + 1)]
