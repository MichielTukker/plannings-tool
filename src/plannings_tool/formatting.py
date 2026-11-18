"""Pure formatting helpers shared by the CLI and renderers."""

from .scheduler import ScheduledDay, ScheduledEntry
from .values import minutes_since_midnight


def format_duration(minutes: int) -> str:
    hours, remainder = divmod(minutes, 60)
    if hours and remainder:
        return f"{hours}h {remainder}m"
    if hours:
        return f"{hours}h"
    return f"{remainder}m"


def format_entry_clock(day: ScheduledDay, entry: ScheduledEntry, *, end: bool) -> str:
    offset = entry.end_offset_minutes if end else entry.start_offset_minutes
    local_time = entry.local_end if end else entry.local_start
    instant = entry.end_at if end else entry.start_at
    if instant is not None and day.date is not None and instant.date() != day.date:
        return f"{instant:%Y-%m-%d %H:%M}"
    if instant is None:
        day_offset = (minutes_since_midnight(day.start_local) + offset) // (24 * 60)
        if day_offset:
            return f"+{day_offset}d {local_time:%H:%M}"
    return f"{local_time:%H:%M}"


def format_entry_title(entry: ScheduledEntry) -> str:
    if entry.part_label is None:
        return entry.title
    return f"{entry.title} ({entry.part_label})"
