"""Demo members with a few months of history, so profile pages have something to show.

History goes through the same ProfileBook calls as real sessions, so levels,
records and badges come out exactly as they would for real members.
"""

import random

from erg.fake import WATTS_PER_SPEED_CUBED
from erg.members import Performance
from erg.profiles import ProfileBook

MEMBERS = [
    ("A-0412", "Marie D."),
    ("A-0388", "Léa B."),
    ("A-0217", "Hugo M."),
    ("A-0503", "Inès K."),
    ("A-0129", "Karim S."),
    ("A-0461", "Nora T."),
    ("A-0274", "Paul R."),
    ("A-0336", "Chloé V."),
]
WEEKS = 12
DAY_S = 86_400
# (name, type, format, minutes, share of the member's test watts they average)
SESSIONS = [
    ("Entraînement 30 min", "training", "endurance", 30, (0.70, 0.85)),
    ("Entraînement 15 min", "training", "endurance", 15, (0.72, 0.88)),
    ("Blitz 5 étapes", "blitz", None, 9, (0.85, 1.00)),
    ("Squad 10 × 10", "squad", None, 22, (0.75, 0.90)),
    ("Intervalles 30 s", "squad", None, 18, (0.80, 0.95)),
]


def seed_members(book: ProfileBook, rng: random.Random, now: float) -> None:
    start = now - WEEKS * 7 * DAY_S
    for member_id, name in MEMBERS:
        book.add_member(member_id, name, start)
        # The first member trains on all three machines, so "Polyvalent" shows up too.
        count = 3 if member_id == MEMBERS[0][0] else rng.choice([1, 2, 3])
        machines = rng.sample(["row", "bike", "ski"], k=count)
        test_watts = {machine: rng.uniform(110, 330) for machine in machines}
        for week in range(WEEKS):
            week_start = start + week * 7 * DAY_S
            if week % 4 == 0:
                for machine in machines:
                    if week:  # members improve between tests
                        test_watts[machine] *= rng.uniform(1.02, 1.09)
                    ts = week_start + rng.uniform(0, 2) * DAY_S
                    book.record_test(member_id, machine, round(test_watts[machine]), ts)
            for _ in range(rng.choice([1, 2, 2, 3])):
                ts = week_start + rng.uniform(2, 7) * DAY_S
                machine = rng.choice(machines + ["run"])
                base = test_watts.get(machine, rng.uniform(150, 260))
                book.record_performance(_session(book, rng, member_id, machine, base, ts))


def _session(
    book: ProfileBook, rng: random.Random, member_id: str, machine: str, base: float, ts: float
) -> Performance:
    preset, session_type, format_id, minutes, share = rng.choice(SESSIONS)
    avg_w = base * rng.uniform(*share)
    speed = 3.0 if machine == "run" else (avg_w / WATTS_PER_SPEED_CUBED) ** (1 / 3)
    if machine == "bike":
        speed *= 2  # a BikeErg covers about twice a rower's distance for the same power
    step_count = 10 if session_type == "squad" else 5 if session_type == "blitz" else 8
    steps_done = step_count if rng.random() < 0.6 else rng.randint(step_count // 2, step_count - 1)
    return Performance(
        member_id=member_id,
        ts=ts,
        preset=preset,
        session_type=session_type,
        machine=machine,
        minutes=minutes,
        energy_wh=avg_w * minutes / 60,
        avg_w=avg_w,
        distance_m=speed * minutes * 60,
        steps_done=steps_done,
        step_count=step_count,
        completed=steps_done == step_count,
        score=book.score(member_id, machine, format_id, avg_w, ts),
    )
