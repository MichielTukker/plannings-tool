"""Sequential scheduling for manually placed course activities and breaks."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .diagnostics import Diagnostic, Location, SchedulingError, Severity
from .models import (
    Break,
    CourseDefinition,
    Day,
    Interactive,
    ScheduleEntry,
    SessionEntry,
)
from .validation import collect_validation_warnings
from .values import minutes_since_midnight

ScheduledSource = Break | SessionEntry


@dataclass(frozen=True)
class ScheduledEntry:
    schedule_index: int
    source: ScheduledSource
    kind: Literal["session", "break"]
    title: str
    session_type: str | None
    break_kind: str | None
    trainer_ids: tuple[str, ...]
    duration_minutes: int
    start_offset_minutes: int
    end_offset_minutes: int
    local_start: time
    local_end: time
    start_at: datetime | None
    end_at: datetime | None


@dataclass(frozen=True)
class ScheduledDay:
    source_index: int
    source: Day
    label: str
    date: date | None
    location: str | None
    timezone: str
    start_local: time
    end_local: time
    start_at: datetime | None
    end_at: datetime | None
    available_minutes: int
    entries: tuple[ScheduledEntry, ...]


@dataclass(frozen=True)
class ScheduleResult:
    days: tuple[ScheduledDay, ...]
    diagnostics: tuple[Diagnostic, ...]

    @property
    def warnings(self) -> tuple[Diagnostic, ...]:
        return tuple(
            diagnostic
            for diagnostic in self.diagnostics
            if diagnostic.severity == Severity.WARNING
        )


@dataclass(frozen=True)
class _DayWindow:
    start_local: time
    end_local: time
    start_at: datetime | None
    end_at: datetime | None
    available_minutes: int


def _failure(code: str, message: str, location: Location) -> SchedulingError:
    return SchedulingError(
        [
            Diagnostic(
                severity=Severity.ERROR,
                code=code,
                location=location,
                message=message,
            )
        ]
    )


def _resolve_local(
    day_index: int,
    field: Literal["start", "end"],
    local_date: date,
    local_time: time,
    zone: ZoneInfo,
) -> datetime:
    location = ("days", day_index, field)
    naive = datetime.combine(local_date, local_time)
    candidates: dict[datetime, datetime] = {}
    for fold in (0, 1):
        candidate = naive.replace(tzinfo=zone, fold=fold)
        instant = candidate.astimezone(UTC)
        if instant.astimezone(zone).replace(tzinfo=None) == naive:
            candidates[instant] = candidate
    if not candidates:
        raise _failure(
            "nonexistent_local_time",
            f"{local_date} {local_time:%H:%M} does not exist in {zone.key}",
            location,
        )
    if len(candidates) > 1:
        raise _failure(
            "ambiguous_local_time",
            f"{local_date} {local_time:%H:%M} occurs twice in {zone.key}",
            location,
        )
    return next(iter(candidates.values()))


def _check_no_offset_transition(
    day_index: int, start_at: datetime, end_at: datetime, zone: ZoneInfo
) -> int:
    start_utc = start_at.astimezone(UTC)
    end_utc = end_at.astimezone(UTC)
    available = int((end_utc - start_utc).total_seconds() // 60)
    starting_offset = start_utc.astimezone(zone).utcoffset()
    for minute in range(1, available + 1):
        current_offset = (
            (start_utc + timedelta(minutes=minute)).astimezone(zone).utcoffset()
        )
        if current_offset != starting_offset:
            raise _failure(
                "timezone_transition",
                f"Day interval crosses a timezone offset change in {zone.key}",
                ("days", day_index),
            )
    return available


def _resolve_day_window(
    course: CourseDefinition, day_index: int, day: Day, zone: ZoneInfo
) -> _DayWindow:
    start_local = day.effective_start(course.course)
    end_local = day.effective_end(course.course)
    if day.date is None:
        return _DayWindow(
            start_local=start_local,
            end_local=end_local,
            start_at=None,
            end_at=None,
            available_minutes=day.available_minutes(course.course),
        )

    start_at = _resolve_local(day_index, "start", day.date, start_local, zone)
    end_at = _resolve_local(day_index, "end", day.date, end_local, zone)
    available = _check_no_offset_transition(day_index, start_at, end_at, zone)
    if available <= 0:
        raise _failure(
            "invalid_day_interval",
            "Resolved day end must be later than its start",
            ("days", day_index, "end"),
        )
    return _DayWindow(
        start_local=start_local,
        end_local=end_local,
        start_at=start_at,
        end_at=end_at,
        available_minutes=available,
    )


def _local_clock(start: time, offset_minutes: int) -> time:
    minute_of_day = (minutes_since_midnight(start) + offset_minutes) % (24 * 60)
    return time(hour=minute_of_day // 60, minute=minute_of_day % 60)


def _source_duration(entry: ScheduleEntry, course: CourseDefinition) -> int:
    if isinstance(entry, Break):
        return entry.effective_duration(course.defaults).minutes
    if isinstance(entry, Interactive):
        return 0
    return entry.duration.minutes


def _entry_identity(
    entry: ScheduleEntry,
) -> tuple[Literal["session", "break"], str, str | None, tuple[str, ...]]:
    if isinstance(entry, Break):
        return "break", f"{entry.kind.title()} break", entry.kind, ()
    return "session", entry.title, None, tuple(entry.trainers)


def _aware_at_offset(start_at: datetime | None, offset_minutes: int) -> datetime | None:
    if start_at is None:
        return None
    return start_at.astimezone(UTC) + timedelta(minutes=offset_minutes)


def _make_scheduled_day(
    course: CourseDefinition,
    day_index: int,
    day: Day,
    zone: ZoneInfo,
) -> tuple[ScheduledDay, list[Diagnostic]]:
    window = _resolve_day_window(course, day_index, day, zone)
    fixed_total = sum(
        _source_duration(entry, course)
        for entry in day.schedule
        if not isinstance(entry, Interactive)
    )
    interactive_index = next(
        (
            index
            for index, entry in enumerate(day.schedule)
            if isinstance(entry, Interactive)
        ),
        None,
    )
    remaining = window.available_minutes - fixed_total
    interactive_duration = max(15, remaining) if interactive_index is not None else None

    durations: list[int] = []
    for entry in day.schedule:
        if isinstance(entry, Interactive):
            if interactive_duration is None:
                raise RuntimeError("Interactive duration was not allocated")
            durations.append(interactive_duration)
        else:
            durations.append(_source_duration(entry, course))
    diagnostics: list[Diagnostic] = []
    if interactive_index is not None and remaining < 15:
        diagnostics.append(
            Diagnostic(
                severity=Severity.WARNING,
                code="interactive_minimum_overflow",
                location=("days", day_index, "schedule", interactive_index),
                message=f"Interactive needs its 15-minute minimum, causing "
                f"{15 - remaining} minutes of overflow",
            )
        )
    elif interactive_index is None:
        if fixed_total > window.available_minutes:
            crossing_index = 0
            cursor = 0
            for index, duration in enumerate(durations):
                cursor += duration
                if cursor > window.available_minutes:
                    crossing_index = index
                    break
            diagnostics.append(
                Diagnostic(
                    severity=Severity.WARNING,
                    code="schedule_overflow",
                    location=("days", day_index, "schedule", crossing_index),
                    message=f"Schedule exceeds the configured day by "
                    f"{fixed_total - window.available_minutes} minutes",
                )
            )
        elif fixed_total < window.available_minutes:
            diagnostics.append(
                Diagnostic(
                    severity=Severity.WARNING,
                    code="unused_time",
                    location=("days", day_index, "schedule"),
                    message=f"{window.available_minutes - fixed_total} minutes "
                    "remain unused",
                )
            )

    scheduled_entries: list[ScheduledEntry] = []
    cursor = 0
    for index, (entry, duration) in enumerate(
        zip(day.schedule, durations, strict=True)
    ):
        kind, title, break_kind, trainer_ids = _entry_identity(entry)
        start_offset = cursor
        end_offset = start_offset + duration
        start_at_utc = _aware_at_offset(window.start_at, start_offset)
        end_at_utc = _aware_at_offset(window.start_at, end_offset)
        scheduled_entries.append(
            ScheduledEntry(
                schedule_index=index,
                source=entry,
                kind=kind,
                title=title,
                session_type=None if isinstance(entry, Break) else entry.type,
                break_kind=break_kind,
                trainer_ids=trainer_ids,
                duration_minutes=duration,
                start_offset_minutes=start_offset,
                end_offset_minutes=end_offset,
                local_start=(
                    start_at_utc.astimezone(zone).time()
                    if start_at_utc is not None
                    else _local_clock(window.start_local, start_offset)
                ),
                local_end=(
                    end_at_utc.astimezone(zone).time()
                    if end_at_utc is not None
                    else _local_clock(window.start_local, end_offset)
                ),
                start_at=(
                    start_at_utc.astimezone(zone) if start_at_utc is not None else None
                ),
                end_at=(
                    end_at_utc.astimezone(zone) if end_at_utc is not None else None
                ),
            )
        )
        cursor = end_offset

    return (
        ScheduledDay(
            source_index=day_index,
            source=day,
            label=day.label,
            date=day.date,
            location=day.location,
            timezone=course.course.timezone,
            start_local=window.start_local,
            end_local=window.end_local,
            start_at=window.start_at,
            end_at=window.end_at,
            available_minutes=window.available_minutes,
            entries=tuple(scheduled_entries),
        ),
        diagnostics,
    )


def schedule_course(course: CourseDefinition) -> ScheduleResult:
    """Resolve all manually scheduled days without mutating the input model."""
    automatic_days = [
        (index, day)
        for index, day in enumerate(course.days)
        if day.effective_break_mode(course.defaults) == "automatic"
    ]
    if automatic_days:
        raise SchedulingError(
            [
                Diagnostic(
                    severity=Severity.ERROR,
                    code="automatic_breaks_not_supported",
                    location=("days", index, "break_mode"),
                    message=f"Automatic break placement is not supported yet for "
                    f"day '{day.label}'; use manual break mode",
                )
                for index, day in automatic_days
            ]
        )

    try:
        zone = ZoneInfo(course.course.timezone)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise _failure(
            "timezone_unavailable",
            f"Timezone data is unavailable for {course.course.timezone}",
            ("course", "timezone"),
        ) from exc

    scheduled_days: list[ScheduledDay] = []
    diagnostics = collect_validation_warnings(course)
    errors: list[Diagnostic] = []
    for index, day in enumerate(course.days):
        try:
            scheduled_day, day_diagnostics = _make_scheduled_day(
                course, index, day, zone
            )
        except SchedulingError as exc:
            errors.extend(exc.diagnostics)
            continue
        scheduled_days.append(scheduled_day)
        diagnostics.extend(day_diagnostics)
    if errors:
        raise SchedulingError(errors)
    return ScheduleResult(tuple(scheduled_days), tuple(diagnostics))
