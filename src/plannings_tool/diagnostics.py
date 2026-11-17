"""Shared diagnostics and domain errors for validation and scheduling."""

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

type Location = tuple[str | int, ...]


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


def format_path(location: Location) -> str:
    """Render a developer-oriented path, e.g. ``days[0].schedule[2]``."""
    path = ""
    for part in location:
        path += f"[{part}]" if isinstance(part, int) else f".{part}"
    return path.lstrip(".") or "$"


_COLLECTION_ITEMS = {"days": "day", "schedule": "entry", "trainers": "trainer"}
_ENTRY_TAGS = frozenset(
    {"break", "Presentation", "Exercise", "Excursion", "Interactive"}
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
        # Pydantic inserts the union tag after an entry index; it adds no detail.
        follows_entry = (
            position >= 2
            and location[position - 2] == "schedule"
            and isinstance(location[position - 1], int)
        )
        if follows_entry and part in _ENTRY_TAGS and position + 1 < len(location):
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
