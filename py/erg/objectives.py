"""Objective definitions and progress calculation."""

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime

from erg import text

SCOPES = ("personal", "team", "overall")
TYPES = ("energy_wh", "avg_power_w")
PERIODS = ("all", "day", "week", "month", "year")


@dataclass(frozen=True)
class Objective:
    id: str
    scope: str
    type: str
    target: float
    window_s: float | None = None
    period: str = "all"

    def describe(self) -> str:
        """What reaching it means, in French: "50 Wh", "200 W de moyenne sur 2 min"."""
        if self.type == "avg_power_w":
            window = text.unit(self.window_s / 60, "min")
            return f"{text.unit(self.target, 'W')} de moyenne sur {window}"
        return text.energy(self.target)

    def validate(self) -> None:
        if self.scope not in SCOPES:
            raise ValueError(f"objective {self.id}: unknown scope {self.scope!r}")
        if self.type not in TYPES:
            raise ValueError(f"objective {self.id}: unknown type {self.type!r}")
        if self.target <= 0:
            raise ValueError(f"objective {self.id}: target must be positive")
        if self.period not in PERIODS:
            raise ValueError(f"objective {self.id}: unknown period {self.period!r}")
        if self.period != "all" and self.scope != "overall":
            raise ValueError(f"objective {self.id}: only overall objectives can have a period")
        if self.type == "avg_power_w":
            # A rolling power average is a property of one person on one erg.
            if self.scope != "personal":
                raise ValueError(f"objective {self.id}: avg_power_w must be personal")
            if not self.window_s or self.window_s <= 0:
                raise ValueError(f"objective {self.id}: avg_power_w needs window_s > 0")


def period_key(period: str, ts: float) -> str:
    """Name of the calendar period containing `ts`, in the server's local time.

    Energy is counted per key, so a new key means the period started over.
    Weeks are ISO weeks, starting on Monday.
    """
    moment = datetime.fromtimestamp(ts)
    if period == "day":
        return moment.strftime("%Y-%m-%d")
    if period == "week":
        year, week, _ = moment.isocalendar()
        return f"{year}-W{week:02d}"
    if period == "month":
        return moment.strftime("%Y-%m")
    if period == "year":
        return str(moment.year)
    return "all"


def progress(objective: Objective, value: float) -> float:
    """Fraction of the target reached, clamped to [0, 1]."""
    return max(0.0, min(1.0, value / objective.target))


@dataclass
class RollingBest:
    """Best average over the last `size` evenly spaced power samples.

    The average only counts once a full window has been seen, so a
    two-second sprint can't satisfy a two-minute objective.
    """

    size: int
    best: float = 0.0
    _window: deque[float] = field(default_factory=deque)
    _sum: float = 0.0

    @property
    def is_full(self) -> bool:
        return len(self._window) == self.size

    def push(self, watts: float) -> None:
        self._window.append(watts)
        self._sum += watts
        if len(self._window) > self.size:
            self._sum -= self._window.popleft()
        if len(self._window) == self.size:
            self.best = max(self.best, self._sum / self.size)
