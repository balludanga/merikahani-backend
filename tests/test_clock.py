"""The single clock: naive UTC, and the same naive UTC everywhere the app runs.

These tests are written so they hold on a machine in any time zone — that is the point of the
module, and a test that only passes in one zone would be asserting the bug rather than the fix.
"""

import time
from datetime import datetime, timedelta

from app.core.clock import epoch_seconds, from_timestamp, utcnow


def test_utcnow_has_the_shape_the_columns_are_in():
    now = utcnow()
    assert isinstance(now, datetime)
    # Naive, so it can be stored and subtracted from what is already stored.
    assert now.tzinfo is None
    assert utcnow() - datetime(2026, 1, 1) > timedelta(0)


def test_utcnow_reads_the_same_clock_as_the_operating_system():
    # time.gmtime() is the system's own UTC wall clock; whole seconds on both sides so the
    # comparison tolerates a slow machine instead of testing how fast the app runs.
    assert abs(utcnow() - datetime(*time.gmtime()[:6])) < timedelta(seconds=2)


def test_utcnow_is_utc_even_though_it_carries_no_zone():
    # Local "now" minus this clock is exactly the machine's offset from UTC. That single property
    # separates a naive-UTC value from a naive-local one, which is the bug being replaced.
    offset = datetime.now().astimezone().utcoffset() or timedelta(0)
    assert abs((datetime.now() - utcnow()) - offset) < timedelta(seconds=2)


def test_from_timestamp_matches_the_same_instant():
    stamp = 1767225600.0  # 2026-01-01T00:00:00Z, whatever zone this machine is in
    assert from_timestamp(stamp) == datetime(2026, 1, 1)
    assert from_timestamp(stamp).tzinfo is None
    # Round trip: a stored timestamp turned back into seconds gives the original number.
    assert epoch_seconds(from_timestamp(stamp)) == stamp


def test_epoch_seconds_does_not_follow_the_machines_zone():
    # datetime.timestamp() on a naive value assumes local time, so the same stored row would hash
    # to a different cache-busting version on every server. Pinned here instead of asserted loose.
    assert epoch_seconds(datetime(2026, 1, 1)) == 1767225600.0
    assert abs(epoch_seconds() - time.time()) < 2


def test_the_two_helpers_are_the_same_clock():
    assert abs(from_timestamp(epoch_seconds(utcnow())) - utcnow()) < timedelta(seconds=2)
