"""Safe YAML loading and public validation diagnostics."""

from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import ValidationError
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode

from .models import Break, CourseDefinition


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    location: tuple[str | int, ...]
    message: str
    line: int | None = None
    column: int | None = None

    def __str__(self) -> str:
        path = ""
        for part in self.location:
            path += f"[{part}]" if isinstance(part, int) else f".{part}"
        path = path.lstrip(".") or "$"
        if self.line is not None:
            path += f" (line {self.line}, column {self.column})"
        return f"{path}: {self.message} [{self.code}]"


class CourseValidationError(ValueError):
    def __init__(self, issues: list[ValidationIssue]):
        self.issues = issues
        super().__init__("\n".join(str(issue) for issue in issues))


def collect_validation_warnings(course: CourseDefinition) -> list[ValidationIssue]:
    """Return advisory issues without rejecting an otherwise valid course."""
    warnings: list[ValidationIssue] = []
    for index, day in enumerate(course.days):
        duration = (
            day.effective_end(course.course).minutes
            - day.effective_start(course.course).minutes
        )
        if (
            day.effective_break_mode(course.defaults) == "manual"
            and duration > 4 * 60
            and not any(isinstance(entry, Break) for entry in day.schedule)
        ):
            warnings.append(
                ValidationIssue(
                    code="missing_manual_breaks",
                    location=("days", index, "schedule"),
                    message=(
                        f"Day '{day.label}' exceeds four hours in manual break mode "
                        "but has no breaks; add manual breaks or use automatic mode"
                    ),
                )
            )
    return warnings


class DuplicateKeyError(ConstructorError):
    pass


class UniqueKeyLoader(yaml.SafeLoader):
    def construct_mapping(self, node: MappingNode, deep: bool = False):
        if not isinstance(node, MappingNode):
            return super().construct_mapping(node, deep=deep)
        self.flatten_mapping(node)
        seen: set[object] = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in seen
                seen.add(key)
            except TypeError as exc:
                raise ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    "found an unhashable mapping key",
                    key_node.start_mark,
                ) from exc
            if duplicate:
                raise DuplicateKeyError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"duplicate mapping key: {key}",
                    key_node.start_mark,
                )
        return super().construct_mapping(node, deep=deep)


def parse_course(text: str) -> CourseDefinition:
    """Validate one YAML document without file I/O."""
    try:
        data = yaml.load(text, Loader=UniqueKeyLoader)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        raise CourseValidationError(
            [
                ValidationIssue(
                    code="duplicate_key"
                    if isinstance(exc, DuplicateKeyError)
                    else "yaml_syntax",
                    location=(),
                    message=getattr(exc, "problem", None) or str(exc),
                    line=mark.line + 1 if mark is not None else None,
                    column=mark.column + 1 if mark is not None else None,
                )
            ]
        ) from exc
    if not isinstance(data, dict):
        raise CourseValidationError(
            [ValidationIssue("root_mapping", (), "Expected a YAML mapping at the root")]
        )
    try:
        return CourseDefinition.model_validate(data)
    except ValidationError as exc:
        raise CourseValidationError(
            [
                ValidationIssue(
                    code=error["type"],
                    location=error["loc"],
                    message=error["msg"],
                )
                for error in exc.errors(include_url=False, include_input=False)
            ]
        ) from exc


def load_course(path: str | Path) -> CourseDefinition:
    """Read a UTF-8 course file and validate it."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise CourseValidationError(
            [ValidationIssue("file_read", (), f"Cannot read course file: {exc}")]
        ) from exc
    return parse_course(text)
