"""Render output naming and atomic file replacement."""

import os
import tempfile
from enum import StrEnum
from pathlib import Path

from ..diagnostics import Diagnostic, DiagnosticCode, Severity
from ..scheduler import ScheduleResult
from ._errors import raise_render_error
from .gantt import render_gantt_png
from .markdown import MarkdownTemplate, render_markdown
from .pdf import render_pdf


class RenderFormat(StrEnum):
    PDF = "pdf"
    PNG = "png"
    MARKDOWN = "md"


def render_outputs(
    result: ScheduleResult,
    input_path: Path,
    output_dir: Path,
    formats: tuple[RenderFormat, ...],
    logo: bytes | None,
    *,
    markdown_template: MarkdownTemplate | None = None,
) -> dict[Path, bytes]:
    """Render requested files fully in memory, before any filesystem writes."""
    outputs: dict[Path, bytes] = {}
    for output_format in dict.fromkeys(formats):
        if output_format == RenderFormat.PDF:
            outputs[output_dir / f"{input_path.stem}.pdf"] = render_pdf(
                result, logo=logo
            )
        elif output_format == RenderFormat.PNG:
            digits = len(str(len(result.days)))
            for day_index in range(len(result.days)):
                day_number = str(day_index + 1).zfill(digits)
                path = output_dir / f"{input_path.stem}-day-{day_number}.png"
                outputs[path] = render_gantt_png(
                    result,
                    day_index=day_index,
                    logo=logo,
                )
        else:
            outputs[output_dir / f"{input_path.stem}.md"] = render_markdown(
                result, template=markdown_template
            ).encode("utf-8")
    return outputs


def write_outputs_atomically(
    outputs: dict[Path, bytes],
) -> tuple[tuple[Path, Diagnostic], ...]:
    """Replace each destination atomically and report successful files."""
    if not outputs:
        return ()
    output_dir = next(iter(outputs)).parent
    replaced: list[Path] = []
    temporary: Path | None = None
    failed_path = output_dir
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        for destination, content in outputs.items():
            failed_path = destination
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=f".{destination.name}.",
                suffix=".tmp",
                dir=destination.parent,
                delete=False,
            ) as file:
                temporary = Path(file.name)
                file.write(content)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, destination)
            temporary = None
            replaced.append(destination)
    except OSError as exc:
        cleanup_message = ""
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError as cleanup_error:
                cleanup_message = (
                    f"; could not remove temporary file {temporary}: {cleanup_error}"
                )
        replaced_message = (
            ", ".join(str(path) for path in replaced) if replaced else "none"
        )
        raise raise_render_error(
            DiagnosticCode.OUTPUT_WRITE,
            f"Cannot write {failed_path}: {exc}. Files already replaced: "
            f"{replaced_message}{cleanup_message}",
            ("output", failed_path.name),
        ) from exc

    return tuple(
        (
            path,
            Diagnostic(
                severity=Severity.INFO,
                code=DiagnosticCode.OUTPUT_WRITTEN,
                location=(),
                message="Output written",
            ),
        )
        for path in replaced
    )
