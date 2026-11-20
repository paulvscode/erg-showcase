"""The dashboard's server, run inside the browser for the static showcase.

Same room, demo machines and session desk as `erg.server --demo`; only the
transport differs: the page calls `step()` every second and `request()`
instead of HTTP and WebSocket.
"""

import json
import random
import time
from pathlib import Path

from erg.config import load_config
from erg.demo import Demo
from erg.desk import RiderEntry, SessionDesk, SessionError
from erg.room import Room


class Showcase:
    def __init__(self, config_path: str) -> None:
        self.config = load_config(Path(config_path))
        self.room = Room(self.config)
        self.demo = Demo(self.room, random.Random(), time.time())
        self.desk = SessionDesk(self.config, self.room, {}, lambda workout, view: None)

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
        if result is None:
            return json.dumps({"status": 404, "body": {"detail": "Not found"}})
        return json.dumps({"status": 200, "body": result})

    def _route(self, method: str, path: str, body: dict) -> dict | None:
        now = time.time()
        if (method, path) == ("GET", "/api/state"):
            return self.state(now)
        if (method, path) == ("GET", "/api/session/setup"):
            return self.desk.setup(now)
        if (method, path) == ("POST", "/api/session/start"):
            entries = [
                RiderEntry(r["erg_id"], r["name"], r.get("weight_kg")) for r in body["riders"]
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
