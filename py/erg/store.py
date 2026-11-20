"""Append-only SQLite log of raw samples and admin actions, replayed on startup."""

import json
import sqlite3
from collections.abc import Iterator

from erg.members import Award, Performance, StepTest
from erg.room import Record

SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    ts REAL NOT NULL,
    erg_id TEXT NOT NULL,
    watts REAL NOT NULL,
    cadence INTEGER NOT NULL,
    distance_m REAL,
    gateway_ts REAL
);
CREATE TABLE IF NOT EXISTS actions (
    ts REAL NOT NULL,
    kind TEXT NOT NULL,
    participant_id TEXT NOT NULL,
    erg_id TEXT
);
CREATE TABLE IF NOT EXISTS workouts (
    started_at REAL NOT NULL,
    preset_id TEXT NOT NULL,
    type TEXT NOT NULL,
    seconds REAL NOT NULL,
    completed INTEGER NOT NULL,
    steps_done INTEGER NOT NULL,
    total_wh REAL NOT NULL,
    riders TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS members (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS performances (
    member_id TEXT NOT NULL,
    ts REAL NOT NULL,
    preset TEXT NOT NULL,
    session_type TEXT NOT NULL,
    machine TEXT NOT NULL,
    minutes REAL NOT NULL,
    energy_wh REAL NOT NULL,
    avg_w REAL NOT NULL,
    distance_m REAL NOT NULL,
    steps_done INTEGER NOT NULL,
    step_count INTEGER NOT NULL,
    completed INTEGER NOT NULL,
    score REAL
);
CREATE TABLE IF NOT EXISTS tests (
    member_id TEXT NOT NULL,
    ts REAL NOT NULL,
    machine TEXT NOT NULL,
    watts REAL NOT NULL,
    level INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS awards (
    member_id TEXT NOT NULL,
    ts REAL NOT NULL,
    badge TEXT NOT NULL,
    detail TEXT NOT NULL
);
"""


class Store:
    def __init__(self, path: str) -> None:
        self.db = sqlite3.connect(path, isolation_level=None)
        self.db.executescript(SCHEMA)
        # Logs written before runners' watts were estimated lack this column.
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(samples)")}
        if "estimated" not in columns:
            self.db.execute("ALTER TABLE samples ADD COLUMN estimated INTEGER NOT NULL DEFAULT 0")

    def add_sample(
        self,
        ts: float,
        erg_id: str,
        watts: float,
        cadence: int,
        distance_m: float | None,
        gateway_ts: float | None,
        estimated: bool,
    ) -> None:
        self.db.execute(
            "INSERT INTO samples (ts, erg_id, watts, cadence, distance_m, gateway_ts, estimated)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ts, erg_id, watts, cadence, distance_m, gateway_ts, int(estimated)),
        )

    def add_action(self, ts: float, kind: str, participant_id: str, erg_id: str | None) -> None:
        self.db.execute(
            "INSERT INTO actions VALUES (?, ?, ?, ?)", (ts, kind, participant_id, erg_id)
        )

    def add_workout(
        self,
        started_at: float,
        preset_id: str,
        workout_type: str,
        seconds: float,
        completed: bool,
        steps_done: int,
        total_wh: float,
        riders: list[dict],
    ) -> None:
        self.db.execute(
            "INSERT INTO workouts VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                started_at,
                preset_id,
                workout_type,
                seconds,
                int(completed),
                steps_done,
                total_wh,
                json.dumps(riders),
            ),
        )

    def race_records(self) -> dict[str, float]:
        """Fastest completed time per race preset (Blitz, Squad), in seconds."""
        rows = self.db.execute(
            "SELECT preset_id, MIN(seconds) FROM workouts"
            " WHERE type != 'training' AND completed = 1 GROUP BY preset_id"
        )
        return dict(rows.fetchall())

    # --- members --------------------------------------------------------------

    def add_member(self, member_id: str, name: str, created: float) -> None:
        self.db.execute("INSERT INTO members VALUES (?, ?, ?)", (member_id, name, created))

    def members(self) -> list[tuple[str, str]]:
        return self.db.execute("SELECT id, name FROM members ORDER BY name").fetchall()

    def member_name(self, member_id: str) -> str | None:
        row = self.db.execute("SELECT name FROM members WHERE id = ?", (member_id,)).fetchone()
        return row[0] if row else None

    def add_performance(self, p: Performance) -> None:
        self.db.execute(
            "INSERT INTO performances VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                p.member_id,
                p.ts,
                p.preset,
                p.session_type,
                p.machine,
                p.minutes,
                p.energy_wh,
                p.avg_w,
                p.distance_m,
                p.steps_done,
                p.step_count,
                int(p.completed),
                p.score,
            ),
        )

    def performances(self, member_id: str) -> list[Performance]:
        rows = self.db.execute(
            "SELECT * FROM performances WHERE member_id = ? ORDER BY ts", (member_id,)
        )
        return [
            Performance(*row[:11], completed=bool(row[11]), score=row[12])
            for row in rows.fetchall()
        ]

    def add_test(self, t: StepTest) -> None:
        self.db.execute(
            "INSERT INTO tests VALUES (?, ?, ?, ?, ?)",
            (t.member_id, t.ts, t.machine, t.watts, t.level),
        )

    def tests(self, member_id: str) -> list[StepTest]:
        rows = self.db.execute("SELECT * FROM tests WHERE member_id = ? ORDER BY ts", (member_id,))
        return [StepTest(*row) for row in rows.fetchall()]

    def add_awards(self, member_id: str, ts: float, awards: list[Award]) -> None:
        self.db.executemany(
            "INSERT INTO awards VALUES (?, ?, ?, ?)",
            [(member_id, ts, a.badge, a.detail) for a in awards],
        )

    def awards(self, member_id: str) -> list[tuple[float, Award]]:
        rows = self.db.execute(
            "SELECT ts, badge, detail FROM awards WHERE member_id = ? ORDER BY ts", (member_id,)
        )
        return [(ts, Award(badge, detail)) for ts, badge, detail in rows.fetchall()]

    def award_counts(self) -> dict[str, int]:
        """How many different badges each member has."""
        rows = self.db.execute(
            "SELECT member_id, COUNT(DISTINCT badge) FROM awards GROUP BY member_id"
        )
        return dict(rows.fetchall())

    def session_counts(self) -> dict[str, int]:
        rows = self.db.execute("SELECT member_id, COUNT(*) FROM performances GROUP BY member_id")
        return dict(rows.fetchall())

    def records(self) -> Iterator[Record]:
        rows = self.db.execute(
            """
            SELECT ts, 'reading', erg_id, NULL, watts, cadence, distance_m, estimated
            FROM samples
            UNION ALL
            SELECT ts, kind, erg_id, participant_id, 0, 0, NULL, 0 FROM actions
            ORDER BY 1
            """
        )
        for *fields, estimated in rows:
            yield Record(*fields, estimated=bool(estimated))

    def close(self) -> None:
        self.db.close()
