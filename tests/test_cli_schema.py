import json
from io import StringIO
from pathlib import Path

import pytest
import yaml
from rich.console import Console
from typer.testing import CliRunner

from plannings_tool import generate_schema
from plannings_tool.cli import _print_issue, app
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
    assert "Warning" in result.stderr
    assert "days[0].schedule" in result.stderr
    assert "missing_manual_breaks" in result.stderr


def test_invalid_example_reports_multiple_errors():
    path = ROOT / "testdata" / "invalid-course.yaml"
    result = runner.invoke(app, ["validate", str(path)])
    assert result.exit_code == 1
    assert result.stdout == ""
    for code in (
        "time_order",
        "email_syntax",
        "string_pattern_mismatch",
        "calendar_date",
    ):
        assert code in result.stderr


@pytest.mark.parametrize("contents", [None, "course: [", "days: []"])
def test_validate_failure(tmp_path, contents):
    path = tmp_path / "course.yaml"
    if contents is not None:
        path.write_text(contents, encoding="utf-8")
    result = runner.invoke(app, ["validate", str(path)])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert "course.yaml" in result.stderr
    assert "Error" in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("args", [[], ["render"], ["validate"], ["schema"]])
def test_usage_errors(args):
    result = runner.invoke(app, args)
    assert result.exit_code == 2
    assert result.output


@pytest.mark.parametrize(
    "args", [["--help"], ["validate", "--help"], ["schema", "--help"]]
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


def test_schema_write_failure(tmp_path):
    path = tmp_path / "missing" / "schema.json"
    result = runner.invoke(app, ["schema", "--output", str(path)])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert "Cannot write schema" in result.stderr


@pytest.mark.parametrize(
    ("label", "label_style", "message_style"),
    [
        ("Warning", "bold yellow", "yellow"),
        ("Error", "bold red", "red"),
    ],
)
def test_issue_output_uses_rich_styles(tmp_path, label, label_style, message_style):
    stream = StringIO()
    console = Console(
        file=stream, force_terminal=True, color_system="standard", record=True
    )
    _print_issue(
        console,
        label,
        label_style,
        tmp_path / "course.yaml",
        "days[0].schedule: diagnostic",
        message_style,
    )
    assert label in stream.getvalue()
    assert "\x1b[" in stream.getvalue()


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
    assert definitions["Presentation"]["properties"]["duration"]["type"] == "string"
    assert definitions["Interactive"]["properties"]["duration"]["const"] == "remaining"
    assert definitions["Break"]["required"] == ["break"]
    assert definitions["Day"]["properties"]["date"]["type"] == "string"
    assert "anyOf" not in definitions["Day"]["properties"]["date"]
    assert definitions["Day"]["properties"]["location"]["type"] == "string"
    assert "location" not in definitions["Course"]["properties"]
    assert definitions["Course"]["properties"]["description"]["type"] == "string"
    assert definitions["Trainer"]["properties"]["email"]["format"] == "email"
