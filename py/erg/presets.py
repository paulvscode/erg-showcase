"""Session formats from config.yaml: Endurance, Intervalles, Seuils, and the step test.

A format is blocks of timed efforts with rests in between. Formats that
rotate move each rider to the next machine of their lane between blocks.
"""

from dataclasses import dataclass

from erg import text

# The kind sets the level target (same names as the level formats) and what is measured.
KINDS = ("endurance", "intervals", "threshold", "test")


@dataclass(frozen=True)
class Format:
    id: str
    name: str
    kind: str
    blocks: int
    efforts: int  # efforts per block
    effort_s: float
    rest_s: float  # between efforts of a block
    rotate: bool  # move to the next machine of the lane between blocks

    @property
    def is_test(self) -> bool:
        return self.kind == "test"

    def validate(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"format {self.id}: kind must be one of {', '.join(KINDS)}")
        if self.blocks < 1 or self.efforts < 1 or self.effort_s <= 0 or self.rest_s < 0:
            raise ValueError(f"format {self.id}: needs blocks, efforts and a positive effort time")
        if self.is_test and (self.blocks, self.efforts, self.rotate) != (1, 1, False):
            raise ValueError(f"format {self.id}: a test is one effort on one machine")

    def describe(self) -> str:
        """The structure in French, e.g. "3 blocs de 10 × (30 s / 30 s récup) · rotation"."""
        effort = _duration(self.effort_s)
        if self.efforts == 1:
            shape = effort
        elif self.rest_s:
            shape = f"{self.efforts} × ({effort} / {_duration(self.rest_s)} récup)"
        else:
            shape = f"{self.efforts} × {effort}"
        if self.blocks > 1:
            shape = f"{self.blocks} blocs de {shape}"
        if self.rotate:
            shape += " · rotation Row → Bike → Ski"
        elif not self.is_test:
            shape += " · même machine"
        return shape


def _duration(seconds: float) -> str:
    if seconds >= 60 and seconds % 60 == 0:
        return text.unit(seconds / 60, "min")
    return text.unit(seconds, "s")
