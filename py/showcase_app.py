"""The dashboard's server, run inside the browser for the static showcase.

Same room, demo machines and session desk as `erg.server --demo`; only the
transport differs: the page calls `step()` every second and `request()`
instead of HTTP and WebSocket.
"""

import json
import random
import re
import time
from pathlib import Path

from erg.config import load_config
from erg.demo import Demo
from erg.demo_members import seed_members
from erg.desk import RiderEntry, SessionDesk, SessionError
from erg.profiles import ProfileBook
from erg.room import Room
from erg.store import Store

MEMBER_PATH = re.compile(r"^/api/members/([^/]+)(/tests)?$")


class Showcase:
    def __init__(self, config_path: str) -> None:
        self.config = load_config(Path(config_path))
        self.room = Room(self.config)
        self.demo = Demo(self.room, random.Random(), time.time())
        self.profiles = ProfileBook(self.config, Store(":memory:"))
        seed_members(self.profiles, random.Random(7), time.time())
        self.desk = SessionDesk(
            self.config, self.room, {}, self.profiles.record_session, self.profiles.name
        )

    def state(self, now: float) -> dict:
        state = self.room.snapshot(now)
        state["demo"] = {"running": self.demo.running}
        state["workout"] = self.desk.view(now)
        return state

    def step(self) -> str:
        """One second of simulation: readings, tick, then the dashboard state as JSON."""
        now = time.time()
        self.demo.step(now, self.config.timing.send_interval_s)
        self.room.tick(now)
        self.desk.tick(now)
        return json.dumps(self.state(now))

    def request(self, method: str, path: str, body: str) -> str:
        """Answer what the pages would send to the server, as {status, body} JSON."""
        try:
            result = self._route(method, path, json.loads(body) if body else {})
        except SessionError as exc:
            return json.dumps({"status": exc.status, "body": {"detail": str(exc)}})
        except ValueError as exc:
            return json.dumps({"status": 400, "body": {"detail": str(exc)}})
        if result is None:
            return json.dumps({"status": 404, "body": {"detail": "Introuvable"}})
        return json.dumps({"status": 200, "body": result})

    def _route(self, method: str, path: str, body: dict) -> dict | None:
        now = time.time()
        if (method, path) == ("GET", "/api/state"):
            return self.state(now)
        if (method, path) == ("GET", "/api/session/setup"):
            setup = self.desk.setup(now)
            setup["members"] = [{"id": i, "name": n} for i, n in self.profiles.store.members()]
            return setup
        if (method, path) == ("GET", "/api/members"):
            return self.profiles.directory(now)
        if (method, path) == ("POST", "/api/members"):
            self.profiles.add_member(body["id"], body["name"], now)
            return {"ok": True}
        member = MEMBER_PATH.match(path)
        if member and method == "GET" and not member.group(2):
            return self.profiles.profile(member.group(1), now)
        if member and method == "POST" and member.group(2):
            ts = body.get("ts") or now
            awards = self.profiles.record_test(member.group(1), body["machine"], body["watts"], ts)
            return {"ok": True, "awards": [a.label() for a in awards]}
        if (method, path) == ("POST", "/api/session/start"):
            entries = [
                RiderEntry(r["erg_id"], r["name"], r.get("weight_kg"), r.get("member_id"))
                for r in body["riders"]
            ]
            self.desk.start(body["preset_id"], body.get("duration_min"), entries, now)
            return {"ok": True}
        if (method, path) == ("POST", "/api/session/stop"):
            self.desk.stop()
            return {"ok": True}
        if method == "POST" and path.startswith("/api/demo/"):
            return self._demo_action(path.removeprefix("/api/demo/"), now)
        return None

    def _demo_action(self, action: str, now: float) -> dict | None:
        if action in ("start", "pause"):
            self.demo.running = action == "start"
        elif action == "reset-objectives":
            self.room.restart_objectives(now)
        elif action == "reset":
            self.desk.cancel()
            self.room.clear()
            self.demo.restart(now)
        else:
            return None
        return {"ok": True}
