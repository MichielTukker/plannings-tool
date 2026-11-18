"""Sequential scheduling for course activities and manual or automatic breaks."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from itertools import pairwise
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .automatic_breaks import AUTOMATIC_BREAK_POLICY, AutomaticBreakRule
from .diagnostics import Diagnostic, DiagnosticCode, Location, SchedulingError, Severity
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
    schedule_index: int | None
    source: ScheduledSource
    duration_minutes: int
    start_offset_minutes: int
    end_offset_minutes: int
    local_start: time
    local_end: time
    start_at: datetime | None
    end_at: datetime | None
    part: int = 1
    parts: int = 1

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

    @property
    def part_label(self) -> str | None:
        return f"part {self.part}/{self.parts}" if self.parts > 1 else None

    @property
    def is_automatic(self) -> bool:
        return self.schedule_index is None


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
    course: CourseDefinition
    days: tuple[ScheduledDay, ...]
    diagnostics: tuple[Diagnostic, ...]

    @property
    def warnings(self) -> tuple[Diagnostic, ...]:
        return tuple(
            diagnostic
            for diagnostic in self.diagnostics
            if diagnostic.severity == Severity.WARNING
        )

    @property
    def infos(self) -> tuple[Diagnostic, ...]:
        return tuple(
            diagnostic
            for diagnostic in self.diagnostics
            if diagnostic.severity == Severity.INFO
        )


@dataclass(frozen=True)
class _Segment:
    source: ScheduledSource
    schedule_index: int | None
    duration_minutes: int
    part: int = 1
    parts: int = 1


@dataclass(frozen=True)
class _SegmentSpan:
    index: int
    segment: _Segment
    start: int
    end: int


@dataclass(frozen=True)
class _DayWindow:
    start_local: time
    end_local: time
    start_at: datetime | None
    end_at: datetime | None
    available_minutes: int


def _scheduling_error(
    code: DiagnosticCode, message: str, location: Location
) -> SchedulingError:
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
            DiagnosticCode.NONEXISTENT_LOCAL_TIME,
            f"{local_date} {local_time:%H:%M} does not exist in {zone.key}",
            location,
        )
    if len(candidates) > 1:
        raise _scheduling_error(
            DiagnosticCode.AMBIGUOUS_LOCAL_TIME,
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
            DiagnosticCode.TIMEZONE_TRANSITION,
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
            DiagnosticCode.INVALID_DAY_INTERVAL,
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
                code=DiagnosticCode.INTERACTIVE_MINIMUM_OVERFLOW,
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
                    code=DiagnosticCode.SCHEDULE_OVERFLOW,
                    location=("days", day_index, "schedule", crossing_index),
                    message=f"Schedule exceeds the configured day by "
                    f"{fixed_total - available_minutes} minutes",
                )
            )
        elif fixed_total < available_minutes:
            diagnostics.append(
                Diagnostic(
                    severity=Severity.WARNING,
                    code=DiagnosticCode.UNUSED_TIME,
                    location=("days", day_index, "schedule"),
                    message=f"{available_minutes - fixed_total} minutes remain unused",
                )
            )
    return diagnostics


def _segment_spans(segments: list[_Segment]) -> list[_SegmentSpan]:
    spans: list[_SegmentSpan] = []
    cursor = 0
    for index, segment in enumerate(segments):
        end = cursor + segment.duration_minutes
        spans.append(_SegmentSpan(index, segment, cursor, end))
        cursor = end
    return spans


def _normalize_parts(segments: list[_Segment]) -> list[_Segment]:
    totals: dict[int, int] = {}
    for segment in segments:
        if segment.schedule_index is not None and not isinstance(segment.source, Break):
            totals[segment.schedule_index] = totals.get(segment.schedule_index, 0) + 1
    parts: dict[int, int] = {}
    normalized: list[_Segment] = []
    for segment in segments:
        if segment.schedule_index is None or isinstance(segment.source, Break):
            normalized.append(segment)
            continue
        index = segment.schedule_index
        parts[index] = parts.get(index, 0) + 1
        normalized.append(
            _Segment(
                source=segment.source,
                schedule_index=index,
                duration_minutes=segment.duration_minutes,
                part=parts[index],
                parts=totals[index],
            )
        )
    return normalized


def _break_segment(rule: AutomaticBreakRule, defaults: Defaults) -> _Segment:
    return _Segment(
        source=Break.model_validate({"break": rule.kind}),
        schedule_index=None,
        duration_minutes=rule.duration(defaults),
    )


def _create_initial_segments(day: Day, durations: list[int]) -> list[_Segment]:
    return [
        _Segment(entry, index, duration)
        for index, (entry, duration) in enumerate(
            zip(day.schedule, durations, strict=True)
        )
    ]


def _boundary_candidate(
    segments: list[_Segment], preferred: int, shift_minutes: int
) -> int | None:
    lower = preferred - shift_minutes
    upper = preferred + shift_minutes
    spans = _segment_spans(segments)
    candidates = [
        span.end
        for span, following in pairwise(spans)
        if not isinstance(span.segment.source, Break)
        and not isinstance(following.segment.source, Break)
        and lower <= span.end <= upper
    ]
    return min(
        candidates, key=lambda offset: (abs(offset - preferred), offset), default=None
    )


def _clock_label(start: time, offset: int) -> str:
    day_offset = (minutes_since_midnight(start) + offset) // (24 * 60)
    clock = _wall_clock_at(start, offset).strftime("%H:%M")
    return f"+{day_offset}d {clock}" if day_offset else clock


def _blocking_span(spans: list[_SegmentSpan], preferred: int) -> _SegmentSpan | None:
    containing = next(
        (span for span in spans if span.start < preferred < span.end), None
    )
    if containing is not None:
        return containing
    return next(
        (
            span
            for span in spans
            if not isinstance(span.segment.source, Break)
            and span.start <= preferred <= span.end
        ),
        next(
            (span for span in spans if not isinstance(span.segment.source, Break)),
            None,
        ),
    )


def _placement_failure(
    rule: AutomaticBreakRule,
    segments: list[_Segment],
    preferred: int,
    window: _DayWindow,
    day_index: int,
) -> Diagnostic:
    spans = _segment_spans(segments)
    blocker = _blocking_span(spans, preferred)
    window_start = _clock_label(window.start_local, preferred - rule.shift_minutes)
    window_end = _clock_label(window.start_local, preferred + rule.shift_minutes)
    location: Location = ("days", day_index)
    if blocker is None:
        detail = "an already placed break blocks the preferred time"
    elif isinstance(blocker.segment.source, Break):
        source = blocker.segment.source
        detail = (
            f"the previously placed {source.kind.title()} break runs "
            f"{_clock_label(window.start_local, blocker.start)}-"
            f"{_clock_label(window.start_local, blocker.end)}"
        )
    else:
        segment = blocker.segment
        source = segment.source
        assert not isinstance(source, Break)
        location = (
            ("days", day_index, "schedule", segment.schedule_index)
            if segment.schedule_index is not None
            else ("days", day_index)
        )
        if isinstance(source, FixedSession) and source.splittable:
            reason = "a resulting fragment would be shorter than 15 minutes"
        else:
            reason = f"{source.type} sessions cannot be split"
        detail = (
            f"blocking session '{source.title}' ({source.type}) runs "
            f"{_clock_label(window.start_local, blocker.start)}-"
            f"{_clock_label(window.start_local, blocker.end)}; {reason}"
        )
    return Diagnostic(
        severity=Severity.ERROR,
        code=DiagnosticCode.AUTOMATIC_BREAK_PLACEMENT,
        location=location,
        message=(
            f"{rule.name} break cannot be placed in its "
            f"{window_start}-{window_end} window: {detail}. Reorder or resize "
            "sessions so one ends inside the window, or set "
            "break_mode: manual for this day"
        ),
    )


def _place_automatic_break(
    segments: list[_Segment],
    rule: AutomaticBreakRule,
    preferred: int,
    window: _DayWindow,
    day_index: int,
    defaults: Defaults,
) -> tuple[list[_Segment] | None, Diagnostic | None, Diagnostic | None]:
    spans = _segment_spans(segments)
    boundary = _boundary_candidate(segments, preferred, rule.shift_minutes)
    if boundary is not None:
        insertion_index = next(
            span.index + 1
            for span in spans
            if span.end == boundary
            and span.index + 1 < len(segments)
            and not isinstance(span.segment.source, Break)
            and not isinstance(segments[span.index + 1].source, Break)
        )
        result = list(segments)
        result.insert(insertion_index, _break_segment(rule, defaults))
        return result, None, None

    containing = next(
        (span for span in spans if span.start < preferred < span.end), None
    )
    if containing is not None:
        segment = containing.segment
        source = segment.source
        if isinstance(source, FixedSession) and source.splittable:
            split_at = preferred
            if split_at - containing.start < 15:
                split_at = preferred + 15
            elif containing.end - split_at < 15:
                split_at = preferred - 15
            if split_at - containing.start >= 15 and containing.end - split_at >= 15:
                index = segment.schedule_index
                assert index is not None
                before = _Segment(source, index, split_at - containing.start)
                after = _Segment(source, index, containing.end - split_at)
                result = list(segments)
                result[containing.index : containing.index + 1] = [
                    before,
                    _break_segment(rule, defaults),
                    after,
                ]
                result = _normalize_parts(result)
                split_diagnostic = Diagnostic(
                    severity=Severity.INFO,
                    code=DiagnosticCode.SESSION_SPLIT,
                    location=("days", day_index, "schedule", index),
                    message=(
                        f"{rule.name} break split '{source.title}' at "
                        f"{_clock_label(window.start_local, split_at)}"
                    ),
                )
                return result, split_diagnostic, None

    activity_end = max(
        (span.end for span in spans if not isinstance(span.segment.source, Break)),
        default=0,
    )
    if activity_end <= preferred:
        return (
            None,
            None,
            Diagnostic(
                severity=Severity.INFO,
                code=DiagnosticCode.AUTOMATIC_BREAK_OMITTED,
                location=("days", day_index),
                message=(
                    f"{rule.name} break omitted: provisional activities end at "
                    f"{_clock_label(window.start_local, activity_end)}, at or before "
                    f"the preferred start "
                    f"{_clock_label(window.start_local, preferred)}; its "
                    f"{rule.duration(defaults)} reserved minutes are "
                    + (
                        "reallocated to Interactive"
                        if any(
                            isinstance(segment.source, Interactive)
                            for segment in segments
                        )
                        else "left unused"
                    )
                ),
            ),
        )
    return (
        None,
        None,
        _placement_failure(rule, segments, preferred, window, day_index),
    )


def _automatic_durations(
    day: Day,
    available_minutes: int,
    defaults: Defaults,
    active_rules: tuple[AutomaticBreakRule, ...],
) -> tuple[list[int], int, int | None]:
    fixed_minutes = sum(
        entry.duration for entry in day.schedule if isinstance(entry, FixedSession)
    )
    interactive_index = next(
        (
            index
            for index, entry in enumerate(day.schedule)
            if isinstance(entry, Interactive)
        ),
        None,
    )
    break_minutes = sum(rule.duration(defaults) for rule in active_rules)
    remaining = available_minutes - fixed_minutes - break_minutes
    durations: list[int] = []
    for entry in day.schedule:
        if isinstance(entry, Interactive):
            durations.append(max(15, remaining))
        elif isinstance(entry, Break):
            durations.append(entry.effective_duration(defaults))
        else:
            durations.append(entry.duration)
    return durations, fixed_minutes + break_minutes, interactive_index


def _automatic_policy_for_day(
    window: _DayWindow,
) -> tuple[tuple[AutomaticBreakRule, int], ...]:
    start = minutes_since_midnight(window.start_local)
    end = minutes_since_midnight(window.end_local)
    return tuple(
        (rule, minutes_since_midnight(rule.preferred) - start)
        for rule in AUTOMATIC_BREAK_POLICY
        if start < minutes_since_midnight(rule.preferred) < end
    )


def _automatic_fit_diagnostics(
    day_index: int,
    available_minutes: int,
    segments: list[_Segment],
    fixed_minutes: int,
    interactive_index: int | None,
) -> list[Diagnostic]:
    if interactive_index is not None:
        remaining = available_minutes - fixed_minutes
        if remaining < 15:
            return [
                Diagnostic(
                    severity=Severity.WARNING,
                    code=DiagnosticCode.INTERACTIVE_MINIMUM_OVERFLOW,
                    location=("days", day_index, "schedule", interactive_index),
                    message=(
                        f"Interactive needs its 15-minute minimum, causing "
                        f"{15 - remaining} minutes of overflow"
                    ),
                )
            ]
        return []

    used_minutes = sum(segment.duration_minutes for segment in segments)
    if used_minutes > available_minutes:
        cursor = 0
        for segment in segments:
            cursor += segment.duration_minutes
            if cursor > available_minutes:
                location: Location = ("days", day_index, "schedule")
                if segment.schedule_index is not None:
                    location = (*location, segment.schedule_index)
                return [
                    Diagnostic(
                        severity=Severity.WARNING,
                        code=DiagnosticCode.SCHEDULE_OVERFLOW,
                        location=location,
                        message=(
                            f"Schedule exceeds the configured day by "
                            f"{used_minutes - available_minutes} minutes"
                        ),
                    )
                ]
    if used_minutes < available_minutes:
        return [
            Diagnostic(
                severity=Severity.WARNING,
                code=DiagnosticCode.UNUSED_TIME,
                location=("days", day_index, "schedule"),
                message=f"{available_minutes - used_minutes} minutes remain unused",
            )
        ]
    return []


def _build_scheduled_entries(
    segments: list[_Segment], window: _DayWindow, zone: ZoneInfo
) -> tuple[ScheduledEntry, ...]:
    entries: list[ScheduledEntry] = []
    cursor = 0
    start_utc = window.start_at.astimezone(UTC) if window.start_at is not None else None
    for segment in segments:
        start_offset = cursor
        end_offset = start_offset + segment.duration_minutes
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
                schedule_index=segment.schedule_index,
                source=segment.source,
                duration_minutes=segment.duration_minutes,
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
                part=segment.part,
                parts=segment.parts,
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
    if day.effective_break_mode(course.defaults) == "automatic":
        return _schedule_automatic_day(course, day_index, day, zone, window)

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
    segments = _create_initial_segments(day, durations)
    scheduled_entries = _build_scheduled_entries(segments, window, zone)

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


def _schedule_automatic_day(
    course: CourseDefinition,
    day_index: int,
    day: Day,
    zone: ZoneInfo,
    window: _DayWindow,
) -> tuple[ScheduledDay, list[Diagnostic]]:
    policy = _automatic_policy_for_day(window)
    active_rules = tuple(rule for rule, _ in policy)
    omitted_diagnostics: list[Diagnostic] = []
    final_split_diagnostics: list[Diagnostic] = []
    final_segments: list[_Segment] = []
    final_interactive_index: int | None = None
    final_fixed_minutes = 0

    while True:
        durations, fixed_minutes, interactive_index = _automatic_durations(
            day, window.available_minutes, course.defaults, active_rules
        )
        segments = _create_initial_segments(day, durations)
        split_diagnostics: list[Diagnostic] = []
        omitted_rule: AutomaticBreakRule | None = None
        placement_error: Diagnostic | None = None

        for rule, preferred in policy:
            if rule not in active_rules:
                continue
            placed, split_diagnostic, omission_or_error = _place_automatic_break(
                segments,
                rule,
                preferred,
                window,
                day_index,
                course.defaults,
            )
            if placed is None:
                if (
                    omission_or_error is not None
                    and omission_or_error.severity == Severity.INFO
                ):
                    omitted_diagnostics.append(omission_or_error)
                    omitted_rule = rule
                else:
                    placement_error = omission_or_error
                break
            segments = placed
            if split_diagnostic is not None:
                split_diagnostics.append(split_diagnostic)

        if placement_error is not None:
            raise SchedulingError([placement_error])
        if omitted_rule is not None:
            active_rules = tuple(rule for rule in active_rules if rule != omitted_rule)
            continue

        final_segments = _normalize_parts(segments)
        final_split_diagnostics = split_diagnostics
        final_interactive_index = interactive_index
        final_fixed_minutes = fixed_minutes
        break

    diagnostics = [
        *omitted_diagnostics,
        *final_split_diagnostics,
        *_automatic_fit_diagnostics(
            day_index,
            window.available_minutes,
            final_segments,
            final_fixed_minutes,
            final_interactive_index,
        ),
    ]
    scheduled_entries = _build_scheduled_entries(final_segments, window, zone)
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
    """Resolve manual and automatic schedules without mutating the input model."""
    try:
        zone = ZoneInfo(course.course.timezone)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise _scheduling_error(
            DiagnosticCode.TIMEZONE_UNAVAILABLE,
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
    return ScheduleResult(course, tuple(scheduled_days), tuple(diagnostics))
