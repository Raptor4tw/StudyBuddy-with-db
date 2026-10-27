from datetime import datetime, timedelta


def current_block_start(timestamps: list[datetime], now: datetime, hours: float = 5) -> datetime | None:
    """Return the start of the usage block that contains `now`, or None.

    A block starts with the first request after the previous block ended and
    lasts `hours`, like a 5-hour usage limit: the first request at 10:00 opens a
    block until 15:00; the next request after 15:00 opens a new one.
    `timestamps` must be sorted ascending.
    """
    length = timedelta(hours=hours)
    start = None
    for ts in timestamps:
        if start is None or ts >= start + length:
            start = ts
    if start is None or now >= start + length:
        return None
    return start
