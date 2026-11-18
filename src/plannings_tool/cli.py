"""Typer commands and Rich terminal output."""

from pathlib import Path
from typing import Annotated

import typer

from .console import _print_diagnostic, _print_diagnostics, print_schedule
from .diagnostics import (
    Diagnostic,
    DiagnosticCode,
    PlanningError,
    Severity,
)
from .loader import load_course
from .render import load_default_logo, load_logo, load_markdown_template
from .render.output import (
    RenderFormat,
    render_outputs,
    write_outputs_atomically,
)
from .scheduler import (
    schedule_course,
)
from .schema import write_schema
from .validation import check_missing_manual_breaks

app = typer.Typer(
    name="plan",
    help="Preview, schedule and render a time schedule for courses.",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="rich",
)


@app.command(help="Validate a course YAML file.")
def validate(
    course: Annotated[Path, typer.Argument(help="UTF-8 course YAML file")],
) -> None:
    """Validate a course definition and report advisory warnings."""
    try:
        definition = load_course(course)
    except PlanningError as exc:
        _print_diagnostics(course, exc.diagnostics)
        raise typer.Exit(code=1) from exc

    _print_diagnostics(course, check_missing_manual_breaks(definition))

    _print_diagnostic(
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
    course: Annotated[Path, typer.Argument(help="UTF-8 course YAML file")],
    strict: Annotated[
        bool, typer.Option("--strict", help="Treat scheduling warnings as failures")
    ] = False,
) -> None:
    """Build and display a sequential schedule preview."""
    try:
        definition = load_course(course)
        result = schedule_course(definition)
    except PlanningError as exc:
        _print_diagnostics(course, exc.diagnostics)
        raise typer.Exit(code=1) from exc

    _print_diagnostics(course, result.diagnostics)
    if strict and result.warnings:
        raise typer.Exit(code=1)
    print_schedule(result, definition)


@app.command(help="Render a course schedule to PDF, PNG, and Markdown.")
def render(
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

    try:
        definition = load_course(course)
        result = schedule_course(definition)
    except PlanningError as exc:
        _print_diagnostics(course, exc.diagnostics)
        raise typer.Exit(code=1) from exc

    _print_diagnostics(course, result.diagnostics)
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
        _print_diagnostics(course, exc.diagnostics)
        raise typer.Exit(code=1) from exc

    for path, diagnostic in written:
        _print_diagnostic(path, diagnostic)


@app.command(help="Export the editor JSON Schema.")
def schema(
    output: Annotated[
        Path,
        typer.Option("--output", help="Destination JSON file", show_default=False),
    ],
) -> None:
    """Write the generated JSON Schema atomically."""
    try:
        write_schema(output)
    except OSError as exc:
        _print_diagnostic(
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
