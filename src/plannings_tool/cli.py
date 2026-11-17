"""Typer commands and Rich terminal output."""

from collections.abc import Iterable
from pathlib import Path
from typing import Annotated

import typer
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .diagnostics import Diagnostic, PlanningError, Severity, display_location
from .loader import load_course
from .models import CourseDefinition
from .scheduler import (
    ScheduledDay,
    ScheduledEntry,
    ScheduleResult,
    schedule_course,
)
from .schema import write_schema
from .validation import collect_validation_warnings

app = typer.Typer(
    name="plan",
    help="Validate course definitions, preview schedules, and export their schema.",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="rich",
)


SEVERITY_STYLES = {
    Severity.ERROR: "bold red",
    Severity.WARNING: "bold orange1",
    Severity.INFO: "bold green",
}


def _print_diagnostic(console: Console, path: Path, diagnostic: Diagnostic) -> None:
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
    console.print(output, soft_wrap=True)


def _print_diagnostics(
    console: Console, path: Path, diagnostics: Iterable[Diagnostic]
) -> None:
    for diagnostic in diagnostics:
        _print_diagnostic(console, path, diagnostic)


def _format_duration(minutes: int) -> str:
    hours, remainder = divmod(minutes, 60)
    if hours and remainder:
        return f"{hours}h {remainder}m"
    if hours:
        return f"{hours}h"
    return f"{remainder}m"


def _entry_clock(day: ScheduledDay, entry: ScheduledEntry, *, end: bool) -> str:
    offset = entry.end_offset_minutes if end else entry.start_offset_minutes
    local_time = entry.local_end if end else entry.local_start
    instant = entry.end_at if end else entry.start_at
    if instant is not None and day.date is not None and instant.date() != day.date:
        return f"{instant:%Y-%m-%d %H:%M}"
    if instant is None and offset >= 24 * 60:
        return f"+{offset // (24 * 60)}d {local_time:%H:%M}"
    return f"{local_time:%H:%M}"


def _print_schedule(
    console: Console, result: ScheduleResult, course: CourseDefinition
) -> None:
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
        console.print(Panel(details, title=title, border_style="cyan"))

        table = Table(box=box.SIMPLE_HEAVY)
        table.add_column("Start", style="cyan", no_wrap=True)
        table.add_column("End", style="cyan", no_wrap=True)
        table.add_column("Duration", justify="right", no_wrap=True)
        table.add_column("Type", no_wrap=True)
        table.add_column("Session / break")
        table.add_column("Trainers")
        for entry in day.entries:
            if entry.kind == "break":
                row_type = "Break"
                row_title = entry.title
            else:
                row_type = entry.session_type or "Session"
                row_title = entry.title
            names = ", ".join(
                trainer_names[trainer_id] for trainer_id in entry.trainer_ids
            )
            table.add_row(
                Text(_entry_clock(day, entry, end=False)),
                Text(_entry_clock(day, entry, end=True)),
                Text(_format_duration(entry.duration_minutes)),
                Text(row_type),
                Text(row_title),
                Text(names or "-"),
            )
        console.print(table)


@app.command(help="Validate a course YAML file.")
def validate(
    course: Annotated[Path, typer.Argument(help="UTF-8 course YAML file")],
) -> None:
    """Validate a course definition and report advisory warnings."""
    error_console = Console(stderr=True)
    try:
        definition = load_course(course)
    except PlanningError as exc:
        _print_diagnostics(error_console, course, exc.diagnostics)
        raise typer.Exit(code=1) from exc

    _print_diagnostics(error_console, course, collect_validation_warnings(definition))

    success = Text()
    success.append("Valid course", style="bold green")
    success.append(f": {course}", style="cyan")
    Console().print(success)


@app.command(help="Schedule a course with its manually placed breaks.")
def schedule(
    course: Annotated[Path, typer.Argument(help="UTF-8 course YAML file")],
    strict: Annotated[
        bool, typer.Option("--strict", help="Treat scheduling warnings as failures")
    ] = False,
) -> None:
    """Build and display a sequential schedule preview."""
    error_console = Console(stderr=True)
    try:
        definition = load_course(course)
        result = schedule_course(definition)
    except PlanningError as exc:
        _print_diagnostics(error_console, course, exc.diagnostics)
        raise typer.Exit(code=1) from exc

    _print_diagnostics(error_console, course, result.warnings)
    if strict and result.warnings:
        raise typer.Exit(code=1)
    _print_schedule(Console(), result, definition)


@app.command(help="Export the editor JSON Schema.")
def schema(
    output: Annotated[
        Path,
        typer.Option("--output", help="Destination JSON file", show_default=False),
    ],
) -> None:
    """Write the generated JSON Schema atomically."""
    error_console = Console(stderr=True)
    try:
        write_schema(output)
    except OSError as exc:
        _print_diagnostic(
            error_console,
            output,
            Diagnostic(
                severity=Severity.ERROR,
                code="schema_write",
                location=(),
                message=f"Cannot write schema: {exc}",
            ),
        )
        raise typer.Exit(code=1) from exc

    message = Text()
    message.append("Schema written", style="bold green")
    message.append(f": {output}", style="cyan")
    Console().print(message)


def main() -> None:
    """Run the plan command-line application."""
    app()
