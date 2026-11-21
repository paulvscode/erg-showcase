"""Member progress: performances, step tests, records and badges.

Pure logic: given what a member did before and what they just did, which
badges did they earn? The store keeps the history; this module decides.
Energy is shown to members in kJ (= Wh × 3.6), as the gym counts it.
"""

from dataclasses import dataclass

from erg import text
from erg.levels import LEVEL_MACHINES

MACHINE_NAMES = {"bike": "BikeErg", "row": "RowErg", "ski": "SkiErg", "run": "AssaultRunner"}
KJ_PER_WH = 3.6
# A 30-second sprint shouldn't count as a power record over a real session.
RECORD_MIN_MINUTES = 3
SESSION_MILESTONES = (10, 25, 50, 100)
ENERGY_MILESTONES_KJ = (1_000, 10_000, 50_000)


@dataclass(frozen=True)
class Badge:
    icon: str
    name: str
    how: str  # what earns it, shown on locked badges


BADGES: dict[str, Badge] = {
    "first-session": Badge("🎉", "Première séance", "Terminer sa première séance"),
    **{
        f"sessions-{n}": Badge("🔁", f"{n} séances", f"Terminer {n} séances")
        for n in SESSION_MILESTONES
    },
    "record-power": Badge(
        "⚡", "Record de puissance", "Battre sa meilleure puissance moyenne sur une machine"
    ),
    "record-distance": Badge(
        "🏁", "Record de distance", "Aller plus loin que jamais sur une machine en une séance"
    ),
    "record-energy": Badge("🔋", "Record d’énergie", "Produire plus qu’à sa meilleure séance"),
    "level-up": Badge("📈", "Niveau supérieur", "Monter d’un niveau à un test de palier"),
    "polyvalent": Badge("🔱", "Polyvalent", "Avoir un niveau sur BikeErg, RowErg et SkiErg"),
    "above-target": Badge(
        "🎯", "Cible tenue", "Tenir sa cible sur un bloc (90 % des efforts en Intervalles)"
    ),
    "perfect-session": Badge("🏆", "Séance parfaite", "Tenir sa cible sur tous les blocs"),
    **{
        f"energy-{kj}": Badge(
            "⚡", f"{text.unit(kj, 'kJ')} cumulés", f"Cumuler {text.unit(kj, 'kJ')}"
        )
        for kj in ENERGY_MILESTONES_KJ
    },
}


@dataclass(frozen=True)
class Performance:
    """One member's result on one block of a session (one machine)."""

    member_id: str
    ts: float
    preset: str
    session_type: str
    machine: str
    minutes: float
    energy_wh: float
    avg_w: float
    distance_m: float
    steps_done: int = 0  # efforts at or above target
    step_count: int = 0  # efforts in the block
    completed: bool = False  # the format's reward for the block was won
    score: float | None = None  # average watts ÷ the member's target for the format


@dataclass(frozen=True)
class StepTest:
    member_id: str
    ts: float
    machine: str
    watts: float
    level: int


@dataclass(frozen=True)
class Award:
    badge: str
    detail: str = ""

    def label(self) -> str:
        badge = BADGES[self.badge]
        return f"{badge.icon} {badge.name}"


def counts_for_records(p: Performance) -> bool:
    return p.minutes >= RECORD_MIN_MINUTES


def session_awards(history: list[Performance], new: list[Performance]) -> list[Award]:
    """Badges for one session, given as its blocks (one Performance per block)."""
    if not new:
        return []
    awards = []
    sessions_before = {p.ts for p in history}
    if new[0].ts not in sessions_before:
        count = len(sessions_before) + 1
        if count == 1:
            awards.append(Award("first-session"))
        if count in SESSION_MILESTONES:
            awards.append(Award(f"sessions-{count}"))

    # A record is beating one's own best on that machine: the first time only sets the bar.
    for machine in sorted({p.machine for p in new}):
        earlier = [p for p in history if p.machine == machine and counts_for_records(p)]
        mine = [p for p in new if p.machine == machine and counts_for_records(p)]
        if not earlier or not mine:
            continue
        name = MACHINE_NAMES.get(machine, machine)
        strongest = max(p.avg_w for p in mine)
        if strongest > max(p.avg_w for p in earlier):
            awards.append(Award("record-power", f"{name} : {text.unit(round(strongest), 'W')}"))
        farthest = max(p.distance_m for p in mine)
        if farthest > max(p.distance_m for p in earlier):
            awards.append(Award("record-distance", f"{name} : {text.distance(farthest)}"))

    session_wh = sum(p.energy_wh for p in new)
    earlier_sessions: dict[float, float] = {}
    for p in history:
        earlier_sessions[p.ts] = earlier_sessions.get(p.ts, 0.0) + p.energy_wh
    if earlier_sessions and session_wh > max(earlier_sessions.values()):
        awards.append(Award("record-energy", text.unit(round(session_wh * KJ_PER_WH), "kJ")))

    for p in new:
        if p.completed:
            machine = MACHINE_NAMES.get(p.machine, p.machine)
            awards.append(Award("above-target", f"{p.preset} · {machine}"))
    if len(new) > 1 and all(p.completed for p in new):
        awards.append(Award("perfect-session", new[0].preset))

    before_kj = sum(p.energy_wh for p in history) * KJ_PER_WH
    after_kj = before_kj + session_wh * KJ_PER_WH
    for kj in ENERGY_MILESTONES_KJ:
        if before_kj < kj <= after_kj:
            awards.append(Award(f"energy-{kj}"))
    return awards


def step_test_awards(
    history: list[StepTest], new: StepTest, level_name: str, window_s: float
) -> list[Award]:
    awards = []
    before = current_levels(history, new.ts, window_s).get(new.machine)
    if before is not None and new.level > before.level:
        machine = MACHINE_NAMES[new.machine]
        awards.append(Award("level-up", f"{machine} : niveau {new.level} · {level_name}"))
    tested_before = {t.machine for t in history}
    tested_now = tested_before | {new.machine}
    if set(LEVEL_MACHINES) <= tested_now and not set(LEVEL_MACHINES) <= tested_before:
        awards.append(Award("polyvalent"))
    return awards


def current_levels(tests: list[StepTest], now: float, window_s: float) -> dict[str, StepTest]:
    """The test that sets the member's level on each machine.

    It's the best test of the recent window, so a bad day never lowers a level.
    With no test in the window, the latest one counts.
    """
    by_machine: dict[str, list[StepTest]] = {}
    for t in tests:
        if t.ts <= now:
            by_machine.setdefault(t.machine, []).append(t)
    levels = {}
    for machine, done in by_machine.items():
        recent = [t for t in done if now - t.ts <= window_s]
        pool = recent or [max(done, key=lambda t: t.ts)]
        levels[machine] = max(pool, key=lambda t: (t.level, t.watts))
    return levels


def records(history: list[Performance]) -> dict:
    """Best average power and distance per machine, and best energy in one session."""
    by_machine: dict[str, dict] = {}
    for p in history:
        if not counts_for_records(p):
            continue
        best = by_machine.setdefault(p.machine, {"avg_w": 0.0, "distance_m": 0.0})
        best["avg_w"] = max(best["avg_w"], p.avg_w)
        best["distance_m"] = max(best["distance_m"], p.distance_m)
    best_energy = max((p.energy_wh for p in history), default=0.0)
    return {"machines": by_machine, "energy_kj": best_energy * KJ_PER_WH}
