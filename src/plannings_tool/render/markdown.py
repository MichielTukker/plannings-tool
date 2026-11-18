"""Markdown timetable rendering from sandboxed Jinja2 templates."""

from dataclasses import dataclass
from functools import cache
from importlib.resources import files
from pathlib import Path
from typing import Literal

from jinja2 import StrictUndefined, Template, TemplateError, TemplateSyntaxError
from jinja2.sandbox import SandboxedEnvironment

from ..diagnostics import Diagnostic, DiagnosticCode
from ..formatting import format_duration, format_entry_clock, format_entry_title
from ..scheduler import ScheduleResult
from ._common import course_warnings, day_warnings, trainer_names
from ._errors import raise_render_error

DEFAULT_TEMPLATE = "timetable.md.j2"
_LOCATION = ("template",)


@dataclass(frozen=True, slots=True)
class MarkdownTemplate:
    """A compiled Markdown template and the name used in diagnostics."""

    name: str
    template: Template


@dataclass(frozen=True, slots=True)
class _CourseView:
    title: str
    description: str | None
    project_id: str | None
    timezone: str


@dataclass(frozen=True, slots=True)
class _EntryView:
    kind: Literal["session", "break"]
    start: str
    end: str
    duration: str
    title: str
    type: str | None
    trainers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _DayView:
    label: str
    date: str | None
    location: str | None
    start: str
    end: str
    entries: tuple[_EntryView, ...]
    warnings: tuple[str, ...]


def md_cell(value: object) -> str:
    """Escape a value for a single-line Markdown table cell or list item."""
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("\r", " ")
        .replace("\n", " ")
    )


def _environment() -> SandboxedEnvironment:
    environment = SandboxedEnvironment(
        autoescape=False,
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    environment.filters["md_cell"] = md_cell
    return environment


def _compile(source: str, name: str) -> MarkdownTemplate:
    try:
        return MarkdownTemplate(name, _environment().from_string(source))
    except TemplateSyntaxError as exc:
        raise raise_render_error(
            DiagnosticCode.TEMPLATE_ERROR,
            f"Template {name} line {exc.lineno}: {exc.message}",
            _LOCATION,
        ) from exc


@cache
def _default_template() -> MarkdownTemplate:
    try:
        source = files("plannings_tool.templates").joinpath(DEFAULT_TEMPLATE)
        text = source.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise raise_render_error(
            DiagnosticCode.TEMPLATE_UNREADABLE,
            f"Cannot read the bundled Markdown template: {exc}",
            _LOCATION,
        ) from exc
    return _compile(text, f"bundled {DEFAULT_TEMPLATE}")


def load_markdown_template(path: Path) -> MarkdownTemplate:
    """Read and compile a UTF-8 Jinja2 template for Markdown output."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise raise_render_error(
            DiagnosticCode.TEMPLATE_UNREADABLE,
            f"Cannot read template {path}: {exc}",
            _LOCATION,
        ) from exc
    return _compile(text, str(path))


def _messages(diagnostics: tuple[Diagnostic, ...]) -> tuple[str, ...]:
    return tuple(diagnostic.message for diagnostic in diagnostics)


def _context(result: ScheduleResult) -> dict[str, object]:
    course = result.course.course
    names = trainer_names(result)
    days = tuple(
        _DayView(
            label=day.label,
            date=day.date.isoformat() if day.date is not None else None,
            location=day.location,
            start=f"{day.start_local:%H:%M}",
            end=f"{day.end_local:%H:%M}",
            entries=tuple(
                _EntryView(
                    kind=entry.kind,
                    start=format_entry_clock(day, entry, end=False),
                    end=format_entry_clock(day, entry, end=True),
                    duration=format_duration(entry.duration_minutes),
                    title=format_entry_title(entry),
                    type=entry.session_type,
                    trainers=tuple(names[trainer] for trainer in entry.trainer_ids),
                )
                for entry in day.entries
            ),
            warnings=_messages(day_warnings(result, day.source_index)),
        )
        for day in result.days
    )
    return {
        "course": _CourseView(
            title=course.title,
            description=course.description,
            project_id=course.project_id,
            timezone=course.timezone,
        ),
        "warnings": _messages(course_warnings(result)),
        "days": days,
    }


def render_markdown(
    result: ScheduleResult, *, template: MarkdownTemplate | None = None
) -> str:
    """Render the schedule with the bundled or a caller-supplied template."""
    selected = template or _default_template()
    context = _context(result)
    try:
        rendered = selected.template.render(context)
    except (TemplateError, ArithmeticError, LookupError, TypeError, ValueError) as exc:
        raise raise_render_error(
            DiagnosticCode.TEMPLATE_ERROR,
            f"Template {selected.name} failed: {exc}",
            _LOCATION,
        ) from exc
    return rendered.rstrip() + "\n"
