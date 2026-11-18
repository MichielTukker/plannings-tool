"""Bundled and caller-supplied renderer assets."""

from importlib.resources import files
from io import BytesIO
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from ..diagnostics import DiagnosticCode
from ._errors import raise_render_error


def load_default_logo() -> bytes:
    """Load the bundled default logo as image bytes."""
    try:
        logo = files("plannings_tool.assets").joinpath("deltares-logo.png").read_bytes()
        validate_logo(logo)
        return logo
    except OSError as exc:
        raise raise_render_error(
            DiagnosticCode.LOGO_UNREADABLE,
            f"Cannot read the bundled Deltares logo: {exc}",
        ) from exc


def load_logo(path: Path) -> bytes:
    """Read and validate a PNG or JPEG logo override."""
    try:
        logo = path.read_bytes()
        validate_logo(logo)
        return logo
    except OSError as exc:
        raise raise_render_error(
            DiagnosticCode.LOGO_UNREADABLE,
            f"Cannot read logo {path}: {exc}",
        ) from exc


def validate_logo(logo: bytes) -> Image.Image:
    """Decode and validate supplied logo bytes."""
    try:
        with Image.open(BytesIO(logo)) as image:
            if image.format not in {"PNG", "JPEG"}:
                raise ValueError(f"unsupported image format {image.format}")
            image.load()
            return image.convert("RGBA")
    except (OSError, ValueError, UnidentifiedImageError) as exc:
        raise raise_render_error(
            DiagnosticCode.LOGO_UNREADABLE,
            f"Logo is not a readable PNG or JPEG: {exc}",
        ) from exc
