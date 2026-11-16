"""Typer commands and Rich terminal output."""

from pathlib import Path
from typing import Annotated

import typer
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .loader import (
    CourseValidationError,
    collect_validation_warnings,
    display_location,
    load_course,
)
from .models import CourseDefinition
from .scheduler import (
    ScheduledDay,
    ScheduledEntry,
    ScheduleResult,
    SchedulingError,
    schedule_course,
)
from .schema import write_schema

app = typer.Typer(
    name="plan",
    help="Validate course definitions, preview schedules, and export their schema.",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="rich",
)


SEVERITY_STYLES = {
    "ERROR": "bold red",
    "WARNING": "bold orange1",
    "INFO": "bold green",
}


def _print_issue(
    console: Console,
    severity: str,
    path: Path,
    message: str,
    *,
    line: int | None = None,
    location: tuple[str | int, ...] = (),
) -> None:
    severity = severity.upper()
    # TODO: carry YAML line numbers into model and scheduling diagnostics; until
    # then, the day/entry location identifies where the problem is.
    source = str(line) if line is not None else display_location(location) or "~"
    output = Text()
    output.append(f"[{severity}]", style=SEVERITY_STYLES[severity])
    output.append(" - ")
    output.append(f"{path.name}:{source}", style="cyan")
    output.append(f" - {message}")
    console.print(output, soft_wrap=True)


def _print_validation_issues(
    console: Console, path: Path, error: CourseValidationError
) -> None:
    for issue in error.issues:
        _print_issue(
            console,
            "ERROR",
            path,
            issue.message,
            line=issue.line,
            location=issue.location,
        )


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
    except CourseValidationError as exc:
        _print_validation_issues(error_console, course, exc)
        raise typer.Exit(code=1) from exc

    for warning in collect_validation_warnings(definition):
        _print_issue(
            error_console,
            "WARNING",
            course,
            warning.message,
            line=warning.line,
            location=warning.location,
        )

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
    except CourseValidationError as exc:
        _print_validation_issues(error_console, course, exc)
        raise typer.Exit(code=1) from exc
    except SchedulingError as exc:
        for diagnostic in exc.diagnostics:
            _print_issue(
                error_console,
                diagnostic.severity,
                course,
                diagnostic.message,
                location=diagnostic.location,
            )
        raise typer.Exit(code=1) from exc

    for diagnostic in result.warnings:
        _print_issue(
            error_console,
            diagnostic.severity,
            course,
            diagnostic.message,
            location=diagnostic.location,
        )
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
        _print_issue(
            error_console,
            "ERROR",
            output,
            f"Cannot write schema: {exc}",
        )
        raise typer.Exit(code=1) from exc

    message = Text()
    message.append("Schema written", style="bold green")
    message.append(f": {output}", style="cyan")
    Console().print(message)


def main() -> None:
    """Run the plan command-line application."""
    app()
