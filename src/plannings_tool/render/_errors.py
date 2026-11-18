"""Helpers for structured renderer errors."""

from ..diagnostics import Diagnostic, DiagnosticCode, Location, RenderError, Severity


def raise_render_error(
    code: DiagnosticCode,
    message: str,
    location: Location = (),
) -> RenderError:
    return RenderError(
        [
            Diagnostic(
                severity=Severity.ERROR,
                code=code,
                location=location,
                message=message,
            )
        ]
    )
