"""Semantic course checks that do not prevent loading a course."""

from .diagnostics import Diagnostic, DiagnosticCode, Severity
from .models import Break, CourseDefinition


def check_missing_manual_breaks(course: CourseDefinition) -> list[Diagnostic]:
    """Report long manual-mode days that have no scheduled breaks."""
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
                    code=DiagnosticCode.MISSING_MANUAL_BREAKS,
                    location=("days", index, "schedule"),
                    message=(
                        f"Day '{day.label}' exceeds four hours in manual break mode "
                        "but has no breaks; add manual breaks or use automatic mode"
                    ),
                )
            )
    return warnings
