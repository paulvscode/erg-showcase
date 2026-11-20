"""Turn sparse power samples into watt-hours."""

from dataclasses import dataclass

SECONDS_PER_HOUR = 3600.0


@dataclass
class EnergyMeter:
    """Integrates power for one erg.

    A rower reports power once per stroke, so the last value is held until
    the next one arrives. If nothing new arrives within `stale_after_s`, the
    erg counts as producing 0 W from that point on.
    """

    stale_after_s: float
    watts: float = 0.0
    sample_ts: float | None = None
    accounted_until: float | None = None

    def power_at(self, now: float) -> float:
        if self.sample_ts is None or now - self.sample_ts > self.stale_after_s:
            return 0.0
        return self.watts

    def advance(self, now: float) -> float:
        """Account energy up to `now`; return the Wh produced since the last call."""
        start = self.accounted_until
        self.accounted_until = now if start is None else max(start, now)
        if start is None or self.sample_ts is None or now <= start:
            return 0.0
        hold_end = min(now, self.sample_ts + self.stale_after_s)
        seconds = max(0.0, hold_end - start)
        return self.watts * seconds / SECONDS_PER_HOUR

    def sample(self, watts: float, ts: float) -> float:
        """Record a new power value; return the Wh produced before it arrived."""
        produced = self.advance(ts)
        self.watts = watts
        self.sample_ts = ts
        return produced


# Running power meters find about 1 W per kg for each m/s on the flat.
RUNNING_WATTS_PER_KG_PER_MPS = 1.0


def estimate_running_watts(speed_mps: float, weight_kg: float) -> float:
    """Mechanical running power, so runners compare with the measured ergs."""
    return max(0.0, speed_mps) * weight_kg * RUNNING_WATTS_PER_KG_PER_MPS
