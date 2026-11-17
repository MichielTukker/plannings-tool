"""Shared diagnostics and domain errors for validation and scheduling."""

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

type Location = tuple[str | int, ...]


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class DiagnosticCode(StrEnum):
    AMBIGUOUS_LOCAL_TIME = "ambiguous_local_time"
    AUTOMATIC_BREAK_OMITTED = "automatic_break_omitted"
    AUTOMATIC_BREAK_PLACEMENT = "automatic_break_placement"
    CALENDAR_DATE = "calendar_date"
    COLOR_SYNTAX = "color_syntax"
    DATE_ORDER = "date_order"
    DUPLICATE_DAY_LABEL = "duplicate_day_label"
    DUPLICATE_KEY = "duplicate_key"
    DUPLICATE_TRAINER_ID = "duplicate_trainer_id"
    DUPLICATE_TRAINER_REFERENCE = "duplicate_trainer_reference"
    DURATION_MINIMUM = "duration_minimum"
    DURATION_SYNTAX = "duration_syntax"
    EMAIL_SYNTAX = "email_syntax"
    FILE_READ = "file_read"
    INTERACTIVE_MINIMUM_OVERFLOW = "interactive_minimum_overflow"
    INVALID_DAY_INTERVAL = "invalid_day_interval"
    MANUAL_BREAK_IN_AUTOMATIC = "manual_break_in_automatic"
    MISSING_MANUAL_BREAKS = "missing_manual_breaks"
    MULTIPLE_INTERACTIVE = "multiple_interactive"
    NONBLANK_SYNTAX = "nonblank_syntax"
    NONEXISTENT_LOCAL_TIME = "nonexistent_local_time"
    NULL_NOT_ALLOWED = "null_not_allowed"
    ROOT_MAPPING = "root_mapping"
    SCHEDULE_OVERFLOW = "schedule_overflow"
    SCHEDULE_ENTRY_TYPE = "schedule_entry_type"
    SCHEMA_VERSION = "schema_version"
    SCHEMA_WRITTEN = "schema_written"
    SCHEMA_WRITE = "schema_write"
    SESSION_SPLIT = "session_split"
    TIME_ORDER = "time_order"
    TIMEZONE_NAME = "timezone_name"
    TIMEZONE_TRANSITION = "timezone_transition"
    TIMEZONE_UNAVAILABLE = "timezone_unavailable"
    TRAINER_ID_SYNTAX = "trainer_id_syntax"
    UNKNOWN_TRAINER = "unknown_trainer"
    UNUSED_TIME = "unused_time"
    VALID_COURSE = "valid_course"
    VALUE_SYNTAX = "value_syntax"
    YAML_SYNTAX = "yaml_syntax"


def format_path(location: Location) -> str:
    """Render a developer-oriented path, e.g. ``days[0].schedule[2]``."""
    path = ""
    for part in location:
        path += f"[{part}]" if isinstance(part, int) else f".{part}"
    return path.lstrip(".") or "$"


_COLLECTION_ITEMS = {"days": "day", "schedule": "entry", "trainers": "trainer"}
_ENTRY_TAGS = frozenset(
    {"break", "session", "Presentation", "Exercise", "Excursion", "Interactive"}
)


def display_location(location: Location) -> str:
    """Render a location for users, e.g. ``day 1|entry 3|duration``."""
    parts: list[str] = []
    for position, part in enumerate(location):
        if isinstance(part, int):
            previous = location[position - 1] if position else None
            if isinstance(previous, str) and parts and parts[-1] == previous:
                parts[-1] = f"{_COLLECTION_ITEMS.get(previous, previous)} {part + 1}"
            else:
                parts.append(str(part + 1))
            continue
        tag_start = position
        while tag_start > 0 and location[tag_start - 1] in _ENTRY_TAGS:
            tag_start -= 1
        follows_entry = (
            tag_start >= 2
            and location[tag_start - 2] == "schedule"
            and isinstance(location[tag_start - 1], int)
        )
        if (
            follows_entry
            and part in _ENTRY_TAGS
            and (position + 1 < len(location) or part == "session")
        ):
            continue
        parts.append(part)
    return "|".join(parts)


@dataclass(frozen=True, kw_only=True)
class Diagnostic:
    severity: Severity
    code: str
    location: Location
    message: str
    line: int | None = None
    column: int | None = None

    def __str__(self) -> str:
        path = format_path(self.location)
        if self.line is not None:
            path += f" (line {self.line}, column {self.column})"
        return f"{path}: {self.message} [{self.code}]"


class PlanningError(ValueError):
    """Base error carrying the diagnostics that caused an operation to fail."""

    def __init__(self, diagnostics: Iterable[Diagnostic]):
        self.diagnostics = tuple(diagnostics)
        super().__init__("\n".join(str(item) for item in self.diagnostics))


class CourseValidationError(PlanningError):
    """The course file could not be read, parsed, or validated."""


class SchedulingError(PlanningError):
    """A valid course could not be scheduled."""
