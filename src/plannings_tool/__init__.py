"""Public course validation API."""

from .cli import main
from .loader import (
    CourseValidationError,
    ValidationIssue,
    collect_validation_warnings,
    load_course,
    parse_course,
)
from .models import CourseDefinition
from .scheduler import (
    ScheduledDay,
    ScheduledEntry,
    ScheduleDiagnostic,
    ScheduleResult,
    SchedulingError,
    schedule_course,
)
from .schema import generate_schema

__all__ = [
    "CourseDefinition",
    "CourseValidationError",
    "ScheduleDiagnostic",
    "ScheduleResult",
    "ScheduledDay",
    "ScheduledEntry",
    "SchedulingError",
    "ValidationIssue",
    "collect_validation_warnings",
    "generate_schema",
    "load_course",
    "main",
    "parse_course",
    "schedule_course",
]
