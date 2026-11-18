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

from .diagnostics import (
    Diagnostic,
    DiagnosticCode,
    PlanningError,
    Severity,
    display_location,
)
from .formatting import format_duration, format_entry_clock, format_entry_title
from .loader import load_course
from .models import CourseDefinition
from .render import load_default_logo, load_logo, load_markdown_template
from .render.output import RenderFormat, render_outputs, write_outputs_atomically
from .scheduler import (
    ScheduleResult,
    schedule_course,
)
from .schema import write_schema
from .validation import check_missing_manual_breaks

app = typer.Typer(
    name="plan",
    help="Validate courses, preview and render schedules, and export the schema.",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="rich",
)


SEVERITY_STYLES = {
    Severity.ERROR: "bold red",
    Severity.WARNING: "bold orange1",
    Severity.INFO: "bold green",
}


def _console(context: typer.Context, *, stderr: bool = False) -> Console:
    return Console(
        stderr=stderr,
        force_terminal=context.color,
    )


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
        console.print(table)


@app.command(help="Validate a course YAML file.")
def validate(
    context: typer.Context,
    course: Annotated[Path, typer.Argument(help="UTF-8 course YAML file")],
) -> None:
    """Validate a course definition and report advisory warnings."""
    console = _console(context)
    error_console = _console(context, stderr=True)
    try:
        definition = load_course(course)
    except PlanningError as exc:
        _print_diagnostics(error_console, course, exc.diagnostics)
        raise typer.Exit(code=1) from exc

    _print_diagnostics(error_console, course, check_missing_manual_breaks(definition))

    _print_diagnostic(
        console,
        course,
        Diagnostic(
            severity=Severity.INFO,
            code=DiagnosticCode.VALID_COURSE,
            location=(),
            message="Valid course",
        ),
    )


@app.command(help="Schedule a course with manual or automatic breaks.")
def schedule(
    context: typer.Context,
    course: Annotated[Path, typer.Argument(help="UTF-8 course YAML file")],
    strict: Annotated[
        bool, typer.Option("--strict", help="Treat scheduling warnings as failures")
    ] = False,
) -> None:
    """Build and display a sequential schedule preview."""
    console = _console(context)
    error_console = _console(context, stderr=True)
    try:
        definition = load_course(course)
        result = schedule_course(definition)
    except PlanningError as exc:
        _print_diagnostics(error_console, course, exc.diagnostics)
        raise typer.Exit(code=1) from exc

    _print_diagnostics(error_console, course, result.diagnostics)
    if strict and result.warnings:
        raise typer.Exit(code=1)
    _print_schedule(console, result, definition)


@app.command(help="Render a course schedule to PDF, PNG, and Markdown.")
def render(
    context: typer.Context,
    course: Annotated[Path, typer.Argument(help="UTF-8 course YAML file")],
    output_dir: Annotated[
        Path | None,
        typer.Option(
            "--output-dir", help="Output directory (default: current directory)"
        ),
    ] = None,
    formats: Annotated[
        list[RenderFormat] | None,
        typer.Option("--format", help="Output format; repeat for multiple formats"),
    ] = None,
    logo: Annotated[
        Path | None,
        typer.Option("--logo", help="PNG or JPEG logo override"),
    ] = None,
    no_logo: Annotated[
        bool,
        typer.Option("--no-logo", help="Render without a logo"),
    ] = False,
    template: Annotated[
        Path | None,
        typer.Option("--template", help="Jinja2 template for Markdown output"),
    ] = None,
    strict: Annotated[
        bool, typer.Option("--strict", help="Treat scheduling warnings as failures")
    ] = False,
) -> None:
    """Render a resolved schedule and atomically write requested outputs."""
    formats = formats if formats is not None else list(RenderFormat)
    if logo is not None and no_logo:
        raise typer.BadParameter("--logo and --no-logo are mutually exclusive")
    if template is not None and RenderFormat.MARKDOWN not in formats:
        raise typer.BadParameter("--template requires --format md")

    console = _console(context)
    error_console = _console(context, stderr=True)
    try:
        definition = load_course(course)
        result = schedule_course(definition)
    except PlanningError as exc:
        _print_diagnostics(error_console, course, exc.diagnostics)
        raise typer.Exit(code=1) from exc

    _print_diagnostics(error_console, course, result.diagnostics)
    if strict and result.warnings:
        raise typer.Exit(code=1)

    output_dir = output_dir or Path.cwd()
    logo_bytes = None
    markdown_template = None
    try:
        if not no_logo and any(
            output_format in {RenderFormat.PDF, RenderFormat.PNG}
            for output_format in formats
        ):
            logo_bytes = load_logo(logo) if logo is not None else load_default_logo()
        if template is not None:
            markdown_template = load_markdown_template(template)
        outputs = render_outputs(
            result,
            course,
            output_dir,
            tuple(formats),
            logo_bytes,
            markdown_template=markdown_template,
        )
        written = write_outputs_atomically(outputs)
    except PlanningError as exc:
        _print_diagnostics(error_console, course, exc.diagnostics)
        raise typer.Exit(code=1) from exc

    for path, diagnostic in written:
        _print_diagnostic(console, path, diagnostic)


@app.command(help="Export the editor JSON Schema.")
def schema(
    context: typer.Context,
    output: Annotated[
        Path,
        typer.Option("--output", help="Destination JSON file", show_default=False),
    ],
) -> None:
    """Write the generated JSON Schema atomically."""
    console = _console(context)
    error_console = _console(context, stderr=True)
    try:
        write_schema(output)
    except OSError as exc:
        _print_diagnostic(
            error_console,
            output,
            Diagnostic(
                severity=Severity.ERROR,
                code=DiagnosticCode.SCHEMA_WRITE,
                location=(),
                message=f"Cannot write schema: {exc}",
            ),
        )
        raise typer.Exit(code=1) from exc

    _print_diagnostic(
        console,
        output,
        Diagnostic(
            severity=Severity.INFO,
            code=DiagnosticCode.SCHEMA_WRITTEN,
            location=(),
            message="Schema written",
        ),
    )


def main() -> None:
    """Run the plan command-line application."""
    app()
