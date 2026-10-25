from datetime import datetime, timedelta

from tools.usage_block import current_block_start


def t(hour, minute=0, day=1):
    return datetime(2026, 10, day, hour, minute)


def test_block_starts_with_first_request():
    assert current_block_start([t(10), t(11), t(14, 59)], now=t(14, 59)) == t(10)


def test_block_ends_after_five_hours():
    assert current_block_start([t(10), t(11)], now=t(15)) is None


def test_request_after_block_end_opens_new_block():
    # 10:00 opens 10-15; 15:30 opens 15:30-20:30, even though 11:00 was active
    assert current_block_start([t(10), t(11), t(15, 30), t(16)], now=t(17)) == t(15, 30)


def test_new_block_starts_at_request_not_at_old_block_end():
    # A request at exactly the end of a block starts the next one
    assert current_block_start([t(10), t(15)], now=t(19, 59)) == t(15)


def test_chained_blocks_without_gaps():
    stamps = [t(8) + timedelta(minutes=30 * i) for i in range(25)]  # 08:00 .. 20:00
    # Blocks: 08:00-13:00, 13:00-18:00, 18:00-23:00
    assert current_block_start(stamps, now=t(20)) == t(18)


def test_no_requests():
    assert current_block_start([], now=t(12)) is None


def test_block_length_is_configurable():
    assert current_block_start([t(10)], now=t(11, 30), hours=1) is None
    assert current_block_start([t(10)], now=t(10, 30), hours=1) == t(10)
