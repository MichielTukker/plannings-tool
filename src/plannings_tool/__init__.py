"""Public course validation API."""

from .cli import main
from .diagnostics import (
    CourseValidationError,
    Diagnostic,
    PlanningError,
    SchedulingError,
    Severity,
)
from .loader import collect_validation_warnings, load_course, parse_course
from .models import CourseDefinition, SessionEntry
from .scheduler import (
    ScheduledDay,
    ScheduledEntry,
    ScheduleResult,
    schedule_course,
)
from .schema import generate_schema

__all__ = [
    "CourseDefinition",
    "CourseValidationError",
    "Diagnostic",
    "PlanningError",
    "ScheduleResult",
    "SessionEntry",
    "ScheduledDay",
    "ScheduledEntry",
    "SchedulingError",
    "Severity",
    "collect_validation_warnings",
    "generate_schema",
    "load_course",
    "main",
    "parse_course",
    "schedule_course",
]
