"""Deterministic trainer color assignment."""

from ..models import CourseDefinition

OKABE_ITO_PALETTE = (
    "#E69F00",
    "#56B4E9",
    "#009E73",
    "#F0E442",
    "#0072B2",
    "#D55E00",
    "#CC79A7",
)


def assign_trainer_colors(course: CourseDefinition) -> dict[str, str]:
    """Assign one consistent color to every trainer in YAML order."""
    explicit = {
        trainer.id: trainer.color
        for trainer in course.trainers
        if trainer.color is not None
    }
    explicitly_used = {color.upper() for color in explicit.values()}
    available = [
        color for color in OKABE_ITO_PALETTE if color.upper() not in explicitly_used
    ] or list(OKABE_ITO_PALETTE)
    colors = dict(explicit)
    missing = (trainer for trainer in course.trainers if trainer.id not in colors)
    for index, trainer in enumerate(missing):
        colors[trainer.id] = available[index % len(available)]
    return colors
