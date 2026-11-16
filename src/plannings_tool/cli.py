"""Typer commands and Rich terminal output."""

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.text import Text

from .loader import (
    CourseValidationError,
    collect_validation_warnings,
    load_course,
)
from .schema import write_schema

app = typer.Typer(
    name="plan",
    help="Validate course definitions and export their editor schema.",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="rich",
)


def _print_issue(
    console: Console,
    label: str,
    label_style: str,
    path: Path,
    message: str,
    message_style: str,
) -> None:
    output = Text()
    output.append(label, style=label_style)
    output.append(" ")
    output.append(str(path), style="cyan")
    output.append(": ")
    output.append(message, style=message_style)
    console.print(output)


@app.command(help="Validate a course YAML file.")
def validate(
    course: Annotated[Path, typer.Argument(help="UTF-8 course YAML file")],
) -> None:
    """Validate a course definition and report advisory warnings."""
    error_console = Console(stderr=True)
    try:
        definition = load_course(course)
    except CourseValidationError as exc:
        for issue in exc.issues:
            _print_issue(
                error_console,
                "Error",
                "bold red",
                course,
                str(issue),
                "red",
            )
        raise typer.Exit(code=1) from exc

    for warning in collect_validation_warnings(definition):
        _print_issue(
            error_console,
            "Warning",
            "bold yellow",
            course,
            str(warning),
            "yellow",
        )

    success = Text()
    success.append("Valid course", style="bold green")
    success.append(f": {course}", style="cyan")
    Console().print(success)


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
        error = Text()
        error.append("Error", style="bold red")
        error.append(f" Cannot write schema to {output}: {exc}", style="red")
        error_console.print(error)
        raise typer.Exit(code=1) from exc

    message = Text()
    message.append("Schema written", style="bold green")
    message.append(f": {output}", style="cyan")
    Console().print(message)


def main() -> None:
    """Run the plan command-line application."""
    app()
