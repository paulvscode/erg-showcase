"""French wording helpers for what riders read on screen."""

# No-break space: French thousands separator, and the space before ! and :
# (not the narrow one, which the club's display font has no glyph for).
NBSP = "\u00a0"


def number(x: float) -> str:
    """1000 -> "1 000", 2.5 -> "2,5"."""
    if float(x).is_integer():
        return f"{int(x):,}".replace(",", NBSP)
    return f"{x:g}".replace(".", ",")


def distance(metres: float) -> str:
    return f"{number(metres)} m" if metres < 1000 else f"{number(metres / 1000)} km"


def energy(wh: float) -> str:
    return f"{number(round(wh))} Wh" if wh < 1000 else f"{number(round(wh / 1000, 2))} kWh"


def exclaim(text: str) -> str:
    return f"{text}{NBSP}!"
