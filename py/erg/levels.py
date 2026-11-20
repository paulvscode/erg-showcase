"""The gym's 10 levels: step-test pace, watts, and training targets per format.

From the "Niveaux" sheet. The step-test pace is per 500 m on the RowErg and
SkiErg, per 1000 m on the BikeErg: the same number means the same watts, so
one table serves all three machines. Each member has one level per machine.
"""

from dataclasses import dataclass, field
from itertools import pairwise

# Concept2's conversion between pace and power: watts = 2.8 / (seconds per metre)^3.
WATTS_PER_SPEED_CUBED = 2.8
LEVEL_MACHINES = ("bike", "row", "ski")
FORMAT_NAMES = {"endurance": "Endurance", "intervals": "Intervalles 30/30", "threshold": "Seuils"}


def watts_for_pace(pace_s: float) -> float:
    """Watts for a pace in seconds per 500 m (RowErg, SkiErg) or per 1000 m (BikeErg)."""
    return WATTS_PER_SPEED_CUBED / (pace_s / 500) ** 3


def pace_for_watts(watts: float) -> float:
    return 500 * (WATTS_PER_SPEED_CUBED / watts) ** (1 / 3)


def bike_kmh(pace_s: float) -> float:
    """BikeErg speed for a pace per 1000 m."""
    return 3600 / pace_s


@dataclass(frozen=True)
class Level:
    number: int
    name: str
    pace_s: float
    percent: dict[str, float] = field(default_factory=dict)  # this level's share per format

    @property
    def watts(self) -> float:
        return watts_for_pace(self.pace_s)

    def target_watts(self, format_id: str) -> float:
        return self.watts * self.percent[format_id] / 100


@dataclass(frozen=True)
class LevelTable:
    levels: list[Level]
    test_minutes: float
    window_days: float  # a level is the best test of this many days

    @property
    def window_s(self) -> float:
        return self.window_days * 86_400

    def level_for(self, test_watts: float) -> Level:
        """The highest level whose step-test watts the test reached.

        Below level 1, a member still starts at level 1 (Découverte).
        """
        reached = [lvl for lvl in self.levels if lvl.watts <= test_watts]
        return reached[-1] if reached else self.levels[0]

    def get(self, number: int) -> Level:
        return self.levels[number - 1]

    def next_level(self, level: Level) -> Level | None:
        return self.levels[level.number] if level.number < len(self.levels) else None

    def validate(self) -> None:
        numbers = [lvl.number for lvl in self.levels]
        if numbers != list(range(1, len(self.levels) + 1)):
            raise ValueError("levels must be numbered 1, 2, 3… in order")
        paces = [lvl.pace_s for lvl in self.levels]
        if any(a <= b for a, b in pairwise(paces)):
            raise ValueError("levels: each pace must be faster (smaller) than the one before")
        for lvl in self.levels:
            missing = set(FORMAT_NAMES) - set(lvl.percent)
            if missing:
                raise ValueError(f"level {lvl.number}: missing format {', '.join(missing)}")
