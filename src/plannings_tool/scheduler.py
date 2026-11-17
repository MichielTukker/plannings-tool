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
    Defaults,
    FixedSession,
    Interactive,
    SessionEntry,
)
from .validation import check_missing_manual_breaks
from .values import minutes_since_midnight

ScheduledSource = Break | SessionEntry


@dataclass(frozen=True)
class ScheduledEntry:
    schedule_index: int
    source: ScheduledSource
    duration_minutes: int
    start_offset_minutes: int
    end_offset_minutes: int
    local_start: time
    local_end: time
    start_at: datetime | None
    end_at: datetime | None

    @property
    def kind(self) -> Literal["session", "break"]:
        return "break" if isinstance(self.source, Break) else "session"

    @property
    def title(self) -> str:
        if isinstance(self.source, Break):
            return f"{self.source.kind.title()} break"
        return self.source.title

    @property
    def session_type(self) -> str | None:
        if isinstance(self.source, Break):
            return None
        return self.source.type

    @property
    def break_kind(self) -> str | None:
        if isinstance(self.source, Break):
            return self.source.kind
        return None

    @property
    def trainer_ids(self) -> tuple[str, ...]:
        if isinstance(self.source, Break):
            return ()
        return tuple(self.source.trainers)


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


def _scheduling_error(code: str, message: str, location: Location) -> SchedulingError:
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


def _localize_wall_time(
    local_date: date,
    local_time: time,
    zone: ZoneInfo,
    location: Location,
) -> datetime:
    naive = datetime.combine(local_date, local_time)
    candidates: dict[datetime, datetime] = {}
    for fold in (0, 1):
        candidate = naive.replace(tzinfo=zone, fold=fold)
        instant = candidate.astimezone(UTC)
        if instant.astimezone(zone).replace(tzinfo=None) == naive:
            candidates[instant] = candidate
    if not candidates:
        raise _scheduling_error(
            "nonexistent_local_time",
            f"{local_date} {local_time:%H:%M} does not exist in {zone.key}",
            location,
        )
    if len(candidates) > 1:
        raise _scheduling_error(
            "ambiguous_local_time",
            f"{local_date} {local_time:%H:%M} occurs twice in {zone.key}",
            location,
        )
    return next(iter(candidates.values()))


def _available_minutes(start_at: datetime, end_at: datetime) -> int:
    elapsed = end_at.astimezone(UTC) - start_at.astimezone(UTC)
    return int(elapsed.total_seconds() // 60)


def _reject_offset_transition(
    day_index: int, start_at: datetime, end_at: datetime, zone: ZoneInfo
) -> None:
    if start_at.utcoffset() != end_at.utcoffset():
        raise _scheduling_error(
            "timezone_transition",
            f"Day interval crosses a timezone offset change in {zone.key}",
            ("days", day_index),
        )


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

    start_at = _localize_wall_time(
        day.date, start_local, zone, ("days", day_index, "start")
    )
    end_at = _localize_wall_time(day.date, end_local, zone, ("days", day_index, "end"))
    _reject_offset_transition(day_index, start_at, end_at, zone)
    available = _available_minutes(start_at, end_at)
    if available <= 0:
        raise _scheduling_error(
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


def _wall_clock_at(start: time, offset_minutes: int) -> time:
    minute_of_day = (minutes_since_midnight(start) + offset_minutes) % (24 * 60)
    return time(hour=minute_of_day // 60, minute=minute_of_day % 60)


def _fixed_duration_minutes(entry: Break | FixedSession, defaults: Defaults) -> int:
    if isinstance(entry, Break):
        return entry.effective_duration(defaults)
    return entry.duration


def _allocate_durations(
    day: Day, available_minutes: int, defaults: Defaults
) -> tuple[int, list[int], int | None]:
    fixed_total = sum(
        _fixed_duration_minutes(entry, defaults)
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
    remaining = available_minutes - fixed_total
    durations = [
        max(15, remaining)
        if isinstance(entry, Interactive)
        else _fixed_duration_minutes(entry, defaults)
        for entry in day.schedule
    ]
    return fixed_total, durations, interactive_index


def _allocation_diagnostics(
    day_index: int,
    available_minutes: int,
    fixed_total: int,
    durations: list[int],
    interactive_index: int | None,
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    remaining = available_minutes - fixed_total
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
        if fixed_total > available_minutes:
            cursor = 0
            crossing_index = 0
            for index, duration in enumerate(durations):
                cursor += duration
                if cursor > available_minutes:
                    crossing_index = index
                    break
            diagnostics.append(
                Diagnostic(
                    severity=Severity.WARNING,
                    code="schedule_overflow",
                    location=("days", day_index, "schedule", crossing_index),
                    message=f"Schedule exceeds the configured day by "
                    f"{fixed_total - available_minutes} minutes",
                )
            )
        elif fixed_total < available_minutes:
            diagnostics.append(
                Diagnostic(
                    severity=Severity.WARNING,
                    code="unused_time",
                    location=("days", day_index, "schedule"),
                    message=f"{available_minutes - fixed_total} minutes remain unused",
                )
            )
    return diagnostics


def _build_scheduled_entries(
    day: Day, durations: list[int], window: _DayWindow, zone: ZoneInfo
) -> tuple[ScheduledEntry, ...]:
    entries: list[ScheduledEntry] = []
    cursor = 0
    start_utc = window.start_at.astimezone(UTC) if window.start_at is not None else None
    for index, (entry, duration) in enumerate(
        zip(day.schedule, durations, strict=True)
    ):
        start_offset = cursor
        end_offset = start_offset + duration
        start_at = (
            start_utc + timedelta(minutes=start_offset)
            if start_utc is not None
            else None
        )
        end_at = (
            start_utc + timedelta(minutes=end_offset) if start_utc is not None else None
        )
        local_start_at = start_at.astimezone(zone) if start_at is not None else None
        local_end_at = end_at.astimezone(zone) if end_at is not None else None
        entries.append(
            ScheduledEntry(
                schedule_index=index,
                source=entry,
                duration_minutes=duration,
                start_offset_minutes=start_offset,
                end_offset_minutes=end_offset,
                local_start=(
                    local_start_at.time()
                    if local_start_at is not None
                    else _wall_clock_at(window.start_local, start_offset)
                ),
                local_end=(
                    local_end_at.time()
                    if local_end_at is not None
                    else _wall_clock_at(window.start_local, end_offset)
                ),
                start_at=local_start_at,
                end_at=local_end_at,
            )
        )
        cursor = end_offset
    return tuple(entries)


def _schedule_day(
    course: CourseDefinition,
    day_index: int,
    day: Day,
    zone: ZoneInfo,
) -> tuple[ScheduledDay, list[Diagnostic]]:
    window = _resolve_day_window(course, day_index, day, zone)
    fixed_total, durations, interactive_index = _allocate_durations(
        day, window.available_minutes, course.defaults
    )
    diagnostics = _allocation_diagnostics(
        day_index,
        window.available_minutes,
        fixed_total,
        durations,
        interactive_index,
    )
    scheduled_entries = _build_scheduled_entries(day, durations, window, zone)

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
            entries=scheduled_entries,
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
        raise _scheduling_error(
            "timezone_unavailable",
            f"Timezone data is unavailable for {course.course.timezone}",
            ("course", "timezone"),
        ) from exc

    scheduled_days: list[ScheduledDay] = []
    diagnostics = check_missing_manual_breaks(course)
    errors: list[Diagnostic] = []
    for index, day in enumerate(course.days):
        try:
            scheduled_day, day_diagnostics = _schedule_day(course, index, day, zone)
        except SchedulingError as exc:
            errors.extend(exc.diagnostics)
            continue
        scheduled_days.append(scheduled_day)
        diagnostics.extend(day_diagnostics)
    if errors:
        raise SchedulingError(errors)
    return ScheduleResult(tuple(scheduled_days), tuple(diagnostics))
