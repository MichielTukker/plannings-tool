"""Reproducible editor schema generation."""

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from .models import CourseDefinition


def generate_schema() -> dict:
    schema = CourseDefinition.model_json_schema(mode="validation", by_alias=True)
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "Course definition"
    return schema


def render_schema() -> str:
    return json.dumps(generate_schema(), indent=2, sort_keys=True) + "\n"


def write_schema(path: str | Path) -> None:
    destination = Path(path)
    temporary: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=destination.absolute().parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(render_schema())
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
