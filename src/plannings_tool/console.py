"""Render output naming and atomic file replacement."""

from collections.abc import Iterable
from pathlib import Path

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .diagnostics import (
    Diagnostic,
    Severity,
    display_location,
)
from .formatting import format_duration, format_entry_clock, format_entry_title
from .models import CourseDefinition
from .scheduler import (
    ScheduleResult,
)

SEVERITY_STYLES = {
    Severity.ERROR: "bold red",
    Severity.WARNING: "bold orange1",
    Severity.INFO: "bold green",
}

# Rich Console,  modify properties as needed.  Note that the unit tests actually
# check console behavior (colloring, stdout/stderr, etc.
STDOUT = Console(color_system="auto", stderr=False)
STDERR = Console(color_system="auto", stderr=True)


def _print_diagnostic(path: Path, diagnostic: Diagnostic) -> None:
    # TODO: carry YAML line numbers into model and scheduling diagnostics; until
    # then, the day/entry location identifies where the problem is.
    if diagnostic.line is not None:
        source = str(diagnostic.line)
    else:
        source = display_location(diagnostic.location) or "~"
    output = Text()
    output.append(
        f"[{diagnostic.severity.upper()}]",
        style=SEVERITY_STYLES[diagnostic.severity],
    )
    output.append(" - ")
    output.append(f"{path.name}:{source}", style="cyan")
    output.append(f" - {diagnostic.message}")
    if diagnostic.severity == Severity.INFO:
        STDOUT.print(output, soft_wrap=True)
    else:
        STDERR.print(output, soft_wrap=True)


def _print_diagnostics(path: Path, diagnostics: Iterable[Diagnostic]) -> None:
    for diagnostic in diagnostics:
        _print_diagnostic(path, diagnostic)


def print_schedule(result: ScheduleResult, course: CourseDefinition) -> None:
    trainer_names = {trainer.id: trainer.name for trainer in course.trainers}
    for day in result.days:
        title = Text(day.label, style="bold cyan")
        if day.date is not None:
            title.append(f"  {day.date.isoformat()}", style="bold")
        details = Text()
        details.append(
            f"{day.start_local:%H:%M}-{day.end_local:%H:%M}  {day.timezone}",
            style="dim",
        )
        if day.location:
            details.append(f"  |  {day.location}")
        if day.date is None:
            details.append("  |  floating local times", style="yellow")
        STDOUT.print(Panel(details, title=title, border_style="cyan"))

        table = Table(box=box.SIMPLE_HEAVY)
        table.add_column("Start", style="cyan", no_wrap=True)
        table.add_column("End", style="cyan", no_wrap=True)
        table.add_column("Duration", justify="right", no_wrap=True)
        table.add_column("Type", no_wrap=True)
        table.add_column("Session / break")
        table.add_column("Trainers")
        for entry in day.entries:
            names = ", ".join(
                trainer_names[trainer_id] for trainer_id in entry.trainer_ids
            )
            table.add_row(
                Text(format_entry_clock(day, entry, end=False)),
                Text(format_entry_clock(day, entry, end=True)),
                Text(format_duration(entry.duration_minutes)),
                Text(entry.session_type or "Break"),
                Text(format_entry_title(entry)),
                Text(names or "-"),
            )
        STDOUT.print(table)
