import json
from io import StringIO
from pathlib import Path

import pytest
import yaml
from rich.console import Console
from typer.testing import CliRunner

from plannings_tool import Diagnostic, Severity, generate_schema
from plannings_tool.cli import _print_diagnostic, app
from plannings_tool.schema import schema_text, write_schema

ROOT = Path(__file__).resolve().parents[1]
runner = CliRunner()


def test_validate_success(tmp_path, definition):
    definition["days"][0]["schedule"].append({"break": "coffee"})
    path = tmp_path / "course.yaml"
    path.write_text(yaml.safe_dump(definition), encoding="utf-8")
    result = runner.invoke(app, ["validate", str(path)])
    assert result.exit_code == 0
    assert "Valid course:" in result.stdout
    assert result.stderr == ""


def test_validate_warning_preserves_success(tmp_path, definition):
    path = tmp_path / "course.yaml"
    path.write_text(yaml.safe_dump(definition), encoding="utf-8")
    result = runner.invoke(app, ["validate", str(path)])
    assert result.exit_code == 0
    assert "Valid course:" in result.stdout
    assert "[WARNING] - course.yaml:day 1|schedule - " in result.stderr
    assert "Day 1" in result.stderr
    assert "days[0].schedule" not in result.stderr
    assert "missing_manual_breaks" not in result.stderr


def test_invalid_example_reports_multiple_errors():
    path = ROOT / "testdata" / "invalid-course.yaml"
    result = runner.invoke(app, ["validate", str(path)])
    assert result.exit_code == 1
    assert result.stdout == ""
    for source in (
        "course|timezone",
        "trainer 1|email",
        "defaults|coffee_break",
        "day 1|date",
        "day 1|entry 1",
        "day 1|entry 2|duration",
    ):
        assert f"[ERROR] - invalid-course.yaml:{source} - " in result.stderr
    assert "Invalid duration; use a value such as '15m' or '1h 15m'" in result.stderr
    assert "String should match pattern" not in result.stderr


@pytest.mark.parametrize(
    ("contents", "source"),
    [(None, "~"), ("course: [", "1"), ("days: []", "schema_version")],
)
def test_validate_failure(tmp_path, contents, source):
    path = tmp_path / "course.yaml"
    if contents is not None:
        path.write_text(contents, encoding="utf-8")
    result = runner.invoke(app, ["validate", str(path)])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert f"[ERROR] - course.yaml:{source} - " in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("args", [[], ["render"], ["validate"], ["schema"]])
def test_usage_errors(args):
    result = runner.invoke(app, args)
    assert result.exit_code == 2
    assert result.output


@pytest.mark.parametrize(
    "args",
    [["--help"], ["validate", "--help"], ["schedule", "--help"], ["schema", "--help"]],
)
def test_help(args):
    result = runner.invoke(app, args)
    assert result.exit_code == 0
    assert "Usage:" in result.stdout


def test_schema_export_and_drift(tmp_path):
    path = tmp_path / "schema.json"
    result = runner.invoke(app, ["schema", "--output", str(path)])
    assert result.exit_code == 0
    assert result.stderr == ""
    assert "Schema written:" in result.stdout
    assert json.loads(path.read_text(encoding="utf-8")) == generate_schema()
    assert path.read_text(encoding="utf-8") == schema_text()
    assert path.read_bytes() == (ROOT / "course.schema.json").read_bytes()


def test_schema_has_native_session_type_discriminator():
    schedule = generate_schema()["$defs"]["Day"]["properties"]["schedule"]["items"]
    session = next(
        variant for variant in schedule["oneOf"] if "discriminator" in variant
    )
    assert session["discriminator"]["propertyName"] == "type"
    assert set(session["discriminator"]["mapping"]) == {
        "Presentation",
        "Exercise",
        "Excursion",
        "Interactive",
    }


def test_schema_write_failure(tmp_path):
    path = tmp_path / "missing" / "schema.json"
    result = runner.invoke(app, ["schema", "--output", str(path)])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert "Cannot write schema" in result.stderr


@pytest.mark.parametrize(
    ("severity", "ansi"),
    [
        (Severity.ERROR, "1;31"),
        (Severity.WARNING, "1;38;5;214"),
        (Severity.INFO, "1;32"),
    ],
)
def test_diagnostic_output_uses_rich_styles(tmp_path, severity, ansi):
    stream = StringIO()
    console = Console(file=stream, force_terminal=True, color_system="256")
    _print_diagnostic(
        console,
        tmp_path / "course.yaml",
        Diagnostic(
            severity=severity,
            code="test",
            location=("days", 0),
            message="diagnostic",
            line=12,
        ),
    )
    label = severity.upper()
    assert stream.getvalue() == (
        f"\x1b[{ansi}m[{label}]\x1b[0m - \x1b[36mcourse.yaml:12\x1b[0m - diagnostic\n"
    )


def test_atomic_export_keeps_original_on_failure(tmp_path, monkeypatch):
    path = tmp_path / "schema.json"
    path.write_text("original", encoding="utf-8")

    def fail_replace(source, destination):
        raise PermissionError("test replacement failure")

    monkeypatch.setattr("plannings_tool.schema.os.replace", fail_replace)
    with pytest.raises(PermissionError):
        write_schema(path)
    assert path.read_text(encoding="utf-8") == "original"
    assert list(tmp_path.iterdir()) == [path]


def test_schema_represents_input_contract():
    schema = generate_schema()
    definitions = schema["$defs"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["schema_version"]["const"] == 1
    assert definitions["Course"]["properties"]["title"]["pattern"] == r"\S"
    assert definitions["Presentation"]["properties"]["duration"]["type"] == "string"
    assert (
        definitions["Presentation"]["properties"]["duration"]["pattern"]
        == r"^(?:[1-9][0-9]*m|[1-9][0-9]*h(?: [1-5]?[1-9]m| [1-5]0m)?)$"
    )
    assert definitions["Interactive"]["properties"]["duration"]["const"] == "remaining"
    assert definitions["Break"]["required"] == ["break"]
    assert definitions["Day"]["properties"]["date"]["type"] == "string"
    assert "anyOf" not in definitions["Day"]["properties"]["date"]
    assert definitions["Day"]["properties"]["location"]["type"] == "string"
    assert "location" not in definitions["Course"]["properties"]
    assert definitions["Course"]["properties"]["description"]["type"] == "string"
    assert definitions["Course"]["properties"]["timezone"]["type"] == "string"
    assert (
        definitions["Course"]["properties"]["timezone"]["default"] == "Europe/Amsterdam"
    )
    assert definitions["Trainer"]["properties"]["email"]["format"] == "email"
    assert (
        definitions["Trainer"]["properties"]["id"]["pattern"] == r"^[a-z][a-z0-9_-]*$"
    )
    assert (
        definitions["Trainer"]["properties"]["color"]["pattern"] == r"^#[0-9a-fA-F]{6}$"
    )
