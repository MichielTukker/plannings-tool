"""Safe YAML loading and course validation."""

from pathlib import Path

import yaml
from pydantic import ValidationError
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode

from .diagnostics import CourseValidationError, Diagnostic, DiagnosticCode, Severity
from .models import CourseDefinition


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
                Diagnostic(
                    severity=Severity.ERROR,
                    code=(
                        DiagnosticCode.DUPLICATE_KEY
                        if isinstance(exc, DuplicateKeyError)
                        else DiagnosticCode.YAML_SYNTAX
                    ),
                    location=(),
                    message=getattr(exc, "problem", None) or str(exc),
                    line=mark.line + 1 if mark is not None else None,
                    column=mark.column + 1 if mark is not None else None,
                )
            ]
        ) from exc
    if not isinstance(data, dict):
        raise CourseValidationError(
            [
                Diagnostic(
                    severity=Severity.ERROR,
                    code=DiagnosticCode.ROOT_MAPPING,
                    location=(),
                    message="Expected a YAML mapping at the root",
                )
            ]
        )
    try:
        return CourseDefinition.model_validate(data)
    except ValidationError as exc:
        raise CourseValidationError(
            [
                Diagnostic(
                    severity=Severity.ERROR,
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
            [
                Diagnostic(
                    severity=Severity.ERROR,
                    code=DiagnosticCode.FILE_READ,
                    location=(),
                    message=f"Cannot read course file: {exc}",
                )
            ]
        ) from exc
    return parse_course(text)
