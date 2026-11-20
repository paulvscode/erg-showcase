"""Demo mode: the configured machines simulated inside the server, no Bluetooth needed."""

import random

from erg.fake import FakeErg, FakeRunner
from erg.room import ErgDetails, Room

# Rowers and SkiErgs take far fewer strokes per minute than a bike turns.
STROKE_RATE = {"bike": (60, 95), "row": (20, 32), "ski": (25, 40)}
# A BikeErg covers about twice a rower's distance for the same power.
SPEED_FACTOR = {"bike": 2.0, "row": 1.0, "ski": 1.0}


class Demo:
    def __init__(self, room: Room, rng: random.Random, now: float) -> None:
        self.room = room
        self.rng = rng
        self.running = True
        self.machines: dict[str, FakeErg | FakeRunner] = {}
        self.restart(now)

    def restart(self, started_at: float) -> None:
        """New simulated riders, each assigned to a machine so names show on the tiles."""
        fake = self.room.config.fake
        self.machines = {}
        for erg in self.room.config.all_ergs():
            if erg.machine == "run":
                self.machines[erg.id] = FakeRunner(self.rng)
            else:
                low, high = STROKE_RATE[erg.machine]
                self.machines[erg.id] = FakeErg(
                    fake.min_watts,
                    fake.max_watts,
                    self.rng,
                    min_cadence=low,
                    max_cadence=high,
                    speed_factor=SPEED_FACTOR[erg.machine],
                )
        for erg_id, person in zip(self.machines, self.room.config.participants, strict=False):
            self.room.assign(person.id, erg_id, started_at)

    def step(self, now: float, elapsed_s: float) -> None:
        """Feed one round of readings. Paused machines stay online at 0 W."""
        self.room.on_heartbeat(self.machines, now)
        if not self.running:
            return
        for erg_id, machine in self.machines.items():
            reading = machine.next_reading(elapsed_s)
            watts = None if isinstance(machine, FakeRunner) else reading.watts
            self.room.on_reading(
                erg_id, watts, reading.cadence, now, reading.distance_m, reading.speed_mps
            )
            self.room.set_details(
                erg_id,
                ErgDetails(
                    distance_m=reading.distance_m,
                    speed_mps=reading.speed_mps,
                    heart_rate=reading.heart_rate,
                    calories=reading.calories,
                ),
            )
