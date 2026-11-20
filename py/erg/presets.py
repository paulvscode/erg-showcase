"""Session presets from config.yaml: a type and its objectives.

Training and Blitz give the whole room one list of objectives. Squad gives
each rider a list for their machine type; the group wins when everyone is done.
"""

from dataclasses import dataclass, field

from erg import text

TYPES = ("training", "blitz", "squad")
KINDS = ("energy", "distance", "hold", "all_in")
# All in compares riders with each other, so it makes no sense for one rider.
# Speed only compares within one machine type (a bike's km/h is not a runner's).
RIDER_KINDS = ("energy", "distance", "hold", "speed")


@dataclass(frozen=True)
class Step:
    """One objective. `target` is Wh, metres, watts or km/h depending on `kind`."""

    kind: str
    target: float
    seconds: float = 0  # how long hold / all_in / speed must last

    @property
    def timed(self) -> bool:
        """Hold, all_in and speed are won by lasting, not by accumulating."""
        return self.kind in ("hold", "all_in", "speed")

    @property
    def goal(self) -> float:
        return self.seconds if self.timed else self.target

    def validate(self, where: str, kinds: tuple[str, ...] = KINDS) -> None:
        if self.kind not in kinds:
            raise ValueError(f"{where}: kind must be one of {', '.join(kinds)}")
        if self.target <= 0:
            raise ValueError(f"{where}: target must be positive")
        if self.timed and self.seconds <= 0:
            raise ValueError(f"{where}: {self.kind} needs seconds > 0")

    def label(self) -> str:
        """Wording for an objective the whole room shares."""
        watts, seconds = text.unit(self.target, "W"), text.unit(self.seconds, "s")
        if self.kind == "energy":
            return f"Produire {text.unit(self.target, 'Wh')} ensemble"
        if self.kind == "distance":
            return f"Parcourir {text.distance(self.target)} ensemble"
        if self.kind == "hold":
            return f"Tenir {watts} ensemble pendant {seconds}"
        return f"Tous au-dessus de {watts} pendant {seconds}"

    def rider_label(self) -> str:
        """Wording for an objective one rider does alone (Squad)."""
        seconds = text.unit(self.seconds, "s")
        if self.kind == "energy":
            return f"Produire {text.unit(self.target, 'Wh')}"
        if self.kind == "distance":
            return f"Parcourir {text.distance(self.target)}"
        if self.kind == "speed":
            return f"Tenir {text.unit(self.target, 'km/h')} pendant {seconds}"
        return f"Tenir {text.unit(self.target, 'W')} pendant {seconds}"


@dataclass(frozen=True)
class Preset:
    id: str
    name: str
    type: str
    duration_min: float  # Training length, or the time limit for Blitz and Squad
    steps: list[Step] = field(default_factory=list)  # Training and Blitz
    machines: dict[str, list[Step]] = field(default_factory=dict)  # Squad, per machine type

    @property
    def is_race(self) -> bool:
        """Blitz and Squad finish as soon as everything is done, against a record time."""
        return self.type != "training"

    def validate(self) -> None:
        if self.type not in TYPES:
            raise ValueError(f"preset {self.id}: type must be one of {', '.join(TYPES)}")
        if self.duration_min <= 0:
            raise ValueError(f"preset {self.id}: needs a duration")
        if self.type == "squad":
            if self.steps or not self.machines:
                raise ValueError(f"preset {self.id}: a squad lists steps under machines")
            for machine, steps in self.machines.items():
                if not steps:
                    raise ValueError(f"preset {self.id}: {machine} has no steps")
                for i, step in enumerate(steps, 1):
                    step.validate(f"preset {self.id} {machine} step {i}", RIDER_KINDS)
            return
        if self.machines or not self.steps:
            raise ValueError(f"preset {self.id}: needs steps (and no machines)")
        for i, step in enumerate(self.steps, 1):
            step.validate(f"preset {self.id} step {i}")
