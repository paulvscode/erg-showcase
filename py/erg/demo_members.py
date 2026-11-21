"""Demo members with a few months of history, so profile pages have something to show.

History goes through the same ProfileBook calls as real sessions, so levels,
records and badges come out exactly as they would for real members.
"""

import random

from erg.fake import WATTS_PER_SPEED_CUBED
from erg.members import Performance
from erg.presets import Format
from erg.profiles import ProfileBook
from erg.workout import ROTATION

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
# Share of the member's test watts each format aims at, roughly.
FORMAT_SHARE = {"endurance": 0.75, "intervals": 1.15, "threshold": 0.9}


def seed_members(book: ProfileBook, rng: random.Random, now: float) -> None:
    start = now - WEEKS * 7 * DAY_S
    formats = [f for f in book.config.sessions.formats if not f.is_test]
    for member_id, name in MEMBERS:
        book.add_member(member_id, name, start)
        # The first member trains on all three machines, so "Polyvalent" shows up too.
        count = 3 if member_id == MEMBERS[0][0] else rng.choice([1, 2, 3])
        machines = rng.sample(list(ROTATION), k=count)
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
                fmt = rng.choice(formats)
                book.record_blocks(_blocks(book, rng, member_id, fmt, test_watts, ts))


def _blocks(
    book: ProfileBook,
    rng: random.Random,
    member_id: str,
    fmt: Format,
    test_watts: dict[str, float],
    ts: float,
) -> list[Performance]:
    first = rng.randrange(len(ROTATION))
    blocks = []
    for block in range(fmt.blocks):
        machine = ROTATION[(first + block) % len(ROTATION)] if fmt.rotate else ROTATION[first]
        base = test_watts.get(machine, rng.uniform(140, 260))
        avg_w = base * FORMAT_SHARE[fmt.kind] * rng.uniform(0.92, 1.08)
        target = book.target(member_id, machine, fmt.kind, ts)
        minutes = fmt.efforts * fmt.effort_s / 60
        if target:
            hit = min(1.0, max(0.0, (avg_w / target - 0.9) / 0.15))
            succeeded = round(fmt.efforts * hit)
        else:
            succeeded = 0
        if fmt.kind == "intervals":
            rewarded = target is not None and succeeded >= 0.9 * fmt.efforts
        elif fmt.kind == "threshold":
            rewarded = target is not None and succeeded == fmt.efforts
        else:
            rewarded = target is not None and avg_w >= target
        speed = (avg_w / WATTS_PER_SPEED_CUBED) ** (1 / 3) * (2 if machine == "bike" else 1)
        blocks.append(
            Performance(
                member_id=member_id,
                ts=ts,
                preset=fmt.name,
                session_type=fmt.kind,
                machine=machine,
                minutes=minutes,
                energy_wh=avg_w * minutes / 60,
                avg_w=avg_w,
                distance_m=speed * minutes * 60,
                steps_done=succeeded,
                step_count=fmt.efforts,
                completed=rewarded,
                score=avg_w / target if target else None,
            )
        )
    return blocks
