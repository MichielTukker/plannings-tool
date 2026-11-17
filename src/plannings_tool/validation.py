"""Semantic course checks that do not prevent loading a course."""

from .diagnostics import Diagnostic, Severity
from .models import Break, CourseDefinition


def collect_validation_warnings(course: CourseDefinition) -> list[Diagnostic]:
    """Return advisory warnings without rejecting an otherwise valid course."""
    warnings: list[Diagnostic] = []
    for index, day in enumerate(course.days):
        if (
            day.effective_break_mode(course.defaults) == "manual"
            and day.available_minutes(course.course) > 4 * 60
            and not any(isinstance(entry, Break) for entry in day.schedule)
        ):
            warnings.append(
                Diagnostic(
                    severity=Severity.WARNING,
                    code="missing_manual_breaks",
                    location=("days", index, "schedule"),
                    message=(
                        f"Day '{day.label}' exceeds four hours in manual break mode "
                        "but has no breaks; add manual breaks or use automatic mode"
                    ),
                )
            )
    return warnings
