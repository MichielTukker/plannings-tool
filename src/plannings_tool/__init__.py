"""Public course validation API."""

from .cli import main
from .diagnostics import (
    CourseValidationError,
    Diagnostic,
    PlanningError,
    SchedulingError,
    Severity,
)
from .loader import load_course, parse_course
from .models import CourseDefinition, SessionEntry
from .scheduler import (
    ScheduledDay,
    ScheduledEntry,
    ScheduleResult,
    schedule_course,
)
from .schema import generate_schema
from .validation import check_missing_manual_breaks

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
    "check_missing_manual_breaks",
    "generate_schema",
    "load_course",
    "main",
    "parse_course",
    "schedule_course",
]
