"""Shared renderer lookups and diagnostic grouping."""

from ..diagnostics import Diagnostic
from ..scheduler import ScheduleResult


def trainer_names(result: ScheduleResult) -> dict[str, str]:
    return {trainer.id: trainer.name for trainer in result.course.trainers}


def day_warnings(result: ScheduleResult, day_index: int) -> tuple[Diagnostic, ...]:
    return tuple(
        diagnostic
        for diagnostic in result.warnings
        if diagnostic.location[:2] == ("days", day_index)
    )


def course_warnings(result: ScheduleResult) -> tuple[Diagnostic, ...]:
    return tuple(
        diagnostic
        for diagnostic in result.warnings
        if not (
            len(diagnostic.location) >= 2
            and diagnostic.location[0] == "days"
            and isinstance(diagnostic.location[1], int)
        )
    )
