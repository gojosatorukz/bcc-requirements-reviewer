"""Bounded experiment on two manually formalized interpretations of the case.

Run with Python 3.10+. These hypotheses belong to evaluation, outside the agent.
"""

from datetime import datetime, timedelta, timezone
from itertools import product


def month_after_posting(purchased_at, posting_delay):
    posted_at = purchased_at + posting_delay
    return purchased_at.strftime("%Y-%m"), posted_at.strftime("%Y-%m")


def notifications(old_threshold, first_purchase, new_threshold, next_purchase):
    """Start at zero, opt in, make a purchase, raise the threshold, buy again.

    Both purchases are posted on the same card in the same month.
    """
    first_push = first_purchase >= old_threshold
    next_crossing = first_purchase < new_threshold <= first_purchase + next_purchase
    monthly_cap = int(first_push) + int(next_crossing and not first_push)
    rearm_on_change = int(first_push) + int(next_crossing)
    return monthly_cap, rearm_on_change


def main():
    local_zone = timezone(timedelta(hours=5))
    purchases = [datetime(2026, 1, day, 23, 58, tzinfo=local_zone) for day in (15, 30, 31)]
    delays = [timedelta(minutes=minutes) for minutes in (0, 1, 5, 1440)]
    calendar = [month_after_posting(purchase, delay) for purchase, delay in product(purchases, delays)]
    assert month_after_posting(purchases[-1], delays[2]) == ("2026-01", "2026-02")
    assert month_after_posting(purchases[0], delays[2]) == ("2026-01", "2026-01")

    # Restrict this experiment to a threshold increase, as in the witness.
    thresholds = [(10_000, 20_000), (20_000, 50_000), (50_000, 80_000)]
    amounts = (5_000, 10_000, 20_000, 30_000, 50_000, 80_000)
    push_counts = [
        notifications(old, first, new, following)
        for (old, new), first, following in product(thresholds, amounts, amounts)
    ]
    assert notifications(50_000, 50_000, 80_000, 30_000) == (1, 2)
    assert notifications(50_000, 20_000, 80_000, 80_000) == (1, 1)
    assert notifications(50_000, 50_000, 80_000, 10_000) == (1, 1)
    for name, rows in (("calendar month", calendar), ("push count", push_counts)):
        different = sum(first != second for first, second in rows)
        print(f"{name}: {different} different, {len(rows) - different} equal, {len(rows)} total")
    print("Official calendar example: 2026-01 vs 2026-02")
    print("50,000 -> 80,000 threshold after a first push: 1 vs 2 pushes")


if __name__ == "__main__":
    main()
