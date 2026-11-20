"""Load config.yaml into typed dataclasses."""

from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

import yaml

from erg.levels import Level, LevelTable
from erg.objectives import Objective
from erg.presets import Preset, Step

DEFAULT_PATH = Path("config.yaml")


@dataclass(frozen=True)
class ServerConfig:
    host: str
    port: int
    db_path: str
    gateway_url: str


@dataclass(frozen=True)
class Timing:
    send_interval_s: float
    tick_s: float
    stale_power_after_s: float
    offline_after_s: float
    reconnect_max_backoff_s: float
    scan_timeout_s: float
    highlight_s: float


@dataclass(frozen=True)
class FakeConfig:
    min_watts: float
    max_watts: float


MACHINES = ("bike", "row", "ski", "run")


@dataclass(frozen=True)
class ErgConfig:
    id: str
    name: str
    machine: str


@dataclass(frozen=True)
class Ladders:
    distance_m: list[float]
    power_w: list[float]  # empty for machines that don't report power


@dataclass(frozen=True)
class Milestones:
    session_idle_s: float
    power_window_s: float
    room_power_w: list[float]  # steps for the whole room, reset every day
    machines: dict[str, Ladders]


@dataclass(frozen=True)
class Sessions:
    countdown_s: float
    results_s: float
    presets: list[Preset]
    hold_grace_s: float = 0.0  # a dip this short pauses a hold's timer instead of resetting it


@dataclass(frozen=True)
class Team:
    id: str
    name: str


@dataclass(frozen=True)
class Participant:
    id: str
    name: str
    team: str


@dataclass(frozen=True)
class Config:
    server: ServerConfig
    timing: Timing
    fake: FakeConfig
    gateways: dict[str, list[ErgConfig]]
    teams: list[Team]
    participants: list[Participant]
    objectives: list[Objective]
    milestones: Milestones
    runner_weight_kg: float  # default weight for runners' estimated watts
    sessions: Sessions
    levels: LevelTable

    def all_ergs(self) -> list[ErgConfig]:
        return [erg for ergs in self.gateways.values() for erg in ergs]


def load_config(path: Path = DEFAULT_PATH) -> Config:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    config = Config(
        server=ServerConfig(**raw["server"]),
        timing=Timing(**raw["timing"]),
        fake=FakeConfig(**raw["fake"]),
        gateways={
            gw_id: [ErgConfig(**erg) for erg in gw["ergs"]] for gw_id, gw in raw["gateways"].items()
        },
        teams=[Team(**t) for t in raw["teams"]],
        participants=[Participant(**p) for p in raw["participants"]],
        objectives=[Objective(**o) for o in raw["objectives"]],
        milestones=_load_milestones(raw["milestones"]),
        runner_weight_kg=raw["runner_weight_kg"],
        sessions=Sessions(
            countdown_s=raw["sessions"]["countdown_s"],
            results_s=raw["sessions"]["results_s"],
            presets=[_load_preset(p) for p in raw["sessions"]["presets"]],
            hold_grace_s=raw["sessions"]["hold_grace_s"],
        ),
        levels=_load_levels(raw["levels"]),
    )
    _validate(config)
    return config


def _load_levels(raw: dict) -> LevelTable:
    levels = [
        Level(
            number=row["level"],
            name=row["name"],
            pace_s=row["pace_s"],
            percent={**raw["formats"], **row.get("formats", {})},
        )
        for row in raw["steps"]
    ]
    return LevelTable(
        levels=levels, test_minutes=raw["test_minutes"], window_days=raw["window_days"]
    )


def _load_preset(raw: dict) -> Preset:
    steps = [Step(**step) for step in raw.get("steps", [])]
    machines = {
        machine: [Step(**step) for step in machine_steps]
        for machine, machine_steps in raw.get("machines", {}).items()
    }
    return Preset(**{**raw, "steps": steps, "machines": machines})


def _load_milestones(raw: dict) -> Milestones:
    return Milestones(
        session_idle_s=raw["session_idle_s"],
        power_window_s=raw["power_window_s"],
        room_power_w=raw["room_power_w"],
        machines={machine: Ladders(**raw[machine]) for machine in MACHINES},
    )


def _validate(config: Config) -> None:
    for erg in config.all_ergs():
        if erg.machine not in MACHINES:
            raise ValueError(f"erg {erg.id}: machine must be one of {', '.join(MACHINES)}")
    team_ids = {t.id for t in config.teams}
    for p in config.participants:
        if p.team not in team_ids:
            raise ValueError(f"participant {p.id} has unknown team {p.team!r}")
    for o in config.objectives:
        o.validate()
    _check_steps("milestones.room_power_w", config.milestones.room_power_w)
    for machine, ladders in config.milestones.machines.items():
        if not ladders.distance_m:
            raise ValueError(f"milestones.{machine}.distance_m needs at least one step")
        _check_steps(f"milestones.{machine}.power_w", ladders.power_w)
        _check_steps(f"milestones.{machine}.distance_m", ladders.distance_m)
    if config.runner_weight_kg <= 0:
        raise ValueError("runner_weight_kg must be positive")
    preset_ids = [p.id for p in config.sessions.presets]
    if len(preset_ids) != len(set(preset_ids)):
        raise ValueError("sessions.presets: ids must be unique")
    config.levels.validate()
    for preset in config.sessions.presets:
        preset.validate()
        unknown = set(preset.machines) - set(MACHINES)
        if unknown:
            raise ValueError(f"preset {preset.id}: unknown machines {', '.join(sorted(unknown))}")


def _check_steps(name: str, steps: list[float]) -> None:
    if steps and (steps[0] <= 0 or any(a >= b for a, b in pairwise(steps))):
        raise ValueError(f"{name} must be positive and increasing")
