"""Command-line validation and schema export."""

import argparse
import sys
from pathlib import Path

from .loader import (
    CourseValidationError,
    collect_validation_warnings,
    load_course,
)
from .schema import write_schema


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="plan", description="Validate course definitions and export their schema."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate", help="Validate a course YAML file")
    validate.add_argument("course", type=Path, help="UTF-8 course YAML file")
    schema = commands.add_parser("schema", help="Export the editor JSON Schema")
    schema.add_argument(
        "--output", type=Path, required=True, help="Destination JSON file"
    )
    args = parser.parse_args(argv)
    if args.command == "validate":
        try:
            course = load_course(args.course)
        except CourseValidationError as exc:
            for issue in exc.issues:
                print(f"{args.course}: {issue}", file=sys.stderr)
            return 1
        for warning in collect_validation_warnings(course):
            print(f"{args.course}: warning: {warning}", file=sys.stderr)
        print(f"Valid course: {args.course}")
    else:
        try:
            write_schema(args.output)
        except OSError as exc:
            print(f"Cannot write schema to {args.output}: {exc}", file=sys.stderr)
            return 1
        print(f"Schema written to {args.output}")
    return 0
