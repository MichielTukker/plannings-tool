from pathlib import Path

import pytest
import yaml
from rich.console import Console
from typer.testing import CliRunner

from plannings_tool import cli
from plannings_tool.cli import STDERR, STDOUT, app

ROOT = Path(__file__).resolve().parents[1]
runner = CliRunner()


def test_validate_success(tmp_path, definition):
    definition["days"][0]["schedule"].append({"break": "coffee"})
    path = tmp_path / "course.yaml"
    path.write_text(yaml.safe_dump(definition), encoding="utf-8")
    result = runner.invoke(app, ["validate", str(path)])
    assert result.exit_code == 0
    assert "[INFO] - course.yaml:~ - Valid course" in result.stdout
    assert result.stderr == ""


def test_validate_warning_preserves_success(tmp_path, definition):
    path = tmp_path / "course.yaml"
    path.write_text(yaml.safe_dump(definition), encoding="utf-8")
    result = runner.invoke(app, ["validate", str(path)])
    assert result.exit_code == 0
    assert "[INFO] - course.yaml:~ - Valid course" in result.stdout
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


def test_schema_command_exports_schema(tmp_path):
    path = tmp_path / "schema.json"
    result = runner.invoke(app, ["schema", "--output", str(path)])
    assert result.exit_code == 0
    assert result.stderr == ""
    assert "[INFO] - schema.json:~ - Schema written" in result.stdout
    assert path.is_file()
    assert path.read_text(encoding="utf-8").startswith("{")


def test_schema_write_failure(tmp_path):
    path = tmp_path / "missing" / "schema.json"
    result = runner.invoke(app, ["schema", "--output", str(path)])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert "Cannot write schema" in result.stderr


def test_validate_diagnostics_use_rich_severity_colors(
    tmp_path, definition, monkeypatch
):
    monkeypatch.setattr(
        cli, "STDERR", Console(force_terminal=True, color_system="256", stderr=True)
    )
    monkeypatch.setattr(
        cli, "STDOUT", Console(force_terminal=True, color_system="256", stderr=False)
    )
    definition["course"].update(start="09:00", end="14:00")
    path = tmp_path / "course.yaml"
    path.write_text(yaml.safe_dump(definition), encoding="utf-8")
    result = runner.invoke(app, ["validate", str(path)])
    assert result.exit_code == 0
    assert "\x1b[1;32m[INFO]" in result.stdout
    assert "\x1b[1;38;5;214m[WARNING]" in result.stderr
    path.write_text("course: [", encoding="utf-8")
    result = runner.invoke(app, ["validate", str(path)])
    assert result.exit_code == 1
    assert "\x1b[1;31m[ERROR]" in result.stderr


def test_schedule_cli_rich_preview_and_warnings(tmp_path, definition):
    definition["course"].update(start="09:00", end="10:00")
    definition["days"][0]["date"] = "2027-01-15"
    definition["days"][0]["location"] = "Training room"
    definition["days"][0]["schedule"] = [
        {
            "title": "Opening",
            "type": "Presentation",
            "duration": "30m",
            "trainers": ["alex"],
        },
        {"break": "coffee"},
        {
            "title": "Workshop",
            "type": "Presentation",
            "duration": "30m",
            "trainers": ["alex"],
        },
    ]
    path = tmp_path / "course.yaml"
    path.write_text(yaml.safe_dump(definition), encoding="utf-8")
    result = runner.invoke(app, ["schedule", str(path)])
    assert result.exit_code == 0
    assert "Day 1" in result.stdout
    assert "2027-01-15" in result.stdout
    assert "Europe/Amsterdam" in result.stdout
    assert "Training room" in result.stdout
    assert "Opening" in result.stdout
    assert "Coffee break" in result.stdout
    assert "Alex Example" in result.stdout
    assert "[WARNING] - course.yaml:day 1|entry 3 - " in result.stderr
    assert "schedule_overflow" not in result.stderr


def test_schedule_cli_strict_suppresses_preview(tmp_path, definition):
    path = tmp_path / "course.yaml"
    path.write_text(yaml.safe_dump(definition), encoding="utf-8")
    result = runner.invoke(app, ["schedule", str(path), "--strict"])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr.count("[WARNING] - course.yaml:day 1|schedule - ") == 2
    assert "exceeds four hours" in result.stderr
    assert "remain unused" in result.stderr


def test_schedule_cli_automatic_preview_and_infos():
    path = ROOT / "testdata" / "Kleppen_auto_breaks.yaml"
    result = runner.invoke(app, ["schedule", str(path)])
    assert result.exit_code == 0
    assert "part" in result.stdout
    assert "1/2" in result.stdout
    assert "Coffee break" in result.stdout
    assert "[INFO] - Kleppen_auto_breaks.yaml:day 2|entry 3 - " in result.stdout
    assert "[WARNING] - Kleppen_auto_breaks.yaml:day 2|entry 6 - " in result.stderr


def test_schedule_cli_strict_ignores_info_diagnostics(tmp_path, definition):
    definition["course"].update(start="11:00", end="17:00")
    definition["defaults"] = {
        "break_mode": "automatic",
        "coffee_break": "5h",
    }
    definition["days"][0].update(
        start="11:00",
        end="17:00",
        schedule=[
            {
                "title": "Long session",
                "type": "Presentation",
                "duration": "2h 30m",
                "trainers": ["alex"],
            },
            {
                "title": "Discussion",
                "type": "Interactive",
                "duration": "remaining",
                "trainers": ["sam"],
            },
        ],
    )
    path = tmp_path / "course.yaml"
    path.write_text(yaml.safe_dump(definition), encoding="utf-8")
    result = runner.invoke(app, ["schedule", str(path), "--strict"])
    assert result.exit_code == 0
    assert "Discussion" in result.stdout
    assert "[INFO] - course.yaml:day 1 - " in result.stdout
