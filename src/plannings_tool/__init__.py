"""Public course planning and scheduling API."""

from .diagnostics import (
    CourseValidationError,
    Diagnostic,
    DiagnosticCode,
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
    "DiagnosticCode",
    "PlanningError",
    "ScheduleResult",
    "ScheduledDay",
    "ScheduledEntry",
    "SchedulingError",
    "SessionEntry",
    "Severity",
    "check_missing_manual_breaks",
    "generate_schema",
    "load_course",
    "parse_course",
    "schedule_course",
]
