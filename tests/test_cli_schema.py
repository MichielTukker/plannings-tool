import json
from pathlib import Path

import pytest
import yaml

from plannings_tool import generate_schema
from plannings_tool.cli import main
from plannings_tool.schema import schema_text, write_schema

ROOT = Path(__file__).resolve().parents[1]


def test_validate_success(tmp_path, capsys, definition):
    definition["days"][0]["schedule"].append({"break": "coffee"})
    path = tmp_path / "course.yaml"
    path.write_text(yaml.safe_dump(definition), encoding="utf-8")
    assert main(["validate", str(path)]) == 0
    output = capsys.readouterr()
    assert "Valid course:" in output.out
    assert output.err == ""


def test_validate_warning_preserves_success(tmp_path, capsys, definition):
    path = tmp_path / "course.yaml"
    path.write_text(yaml.safe_dump(definition), encoding="utf-8")
    assert main(["validate", str(path)]) == 0
    output = capsys.readouterr()
    assert "Valid course:" in output.out
    assert "warning:" in output.err
    assert "days[0].schedule" in output.err
    assert "missing_manual_breaks" in output.err


@pytest.mark.parametrize("contents", [None, "course: [", "days: []"])
def test_validate_failure(tmp_path, capsys, contents):
    path = tmp_path / "course.yaml"
    if contents is not None:
        path.write_text(contents, encoding="utf-8")
    assert main(["validate", str(path)]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert str(path) in output.err
    assert "Traceback" not in output.err


@pytest.mark.parametrize("args", [[], ["render"], ["validate"], ["schema"]])
def test_usage_errors(args, capsys):
    with pytest.raises(SystemExit) as caught:
        main(args)
    assert caught.value.code == 2
    assert capsys.readouterr().err


@pytest.mark.parametrize(
    "args", [["--help"], ["validate", "--help"], ["schema", "--help"]]
)
def test_help(args, capsys):
    with pytest.raises(SystemExit) as caught:
        main(args)
    assert caught.value.code == 0
    assert "usage:" in capsys.readouterr().out


def test_schema_export_and_drift(tmp_path, capsys):
    path = tmp_path / "schema.json"
    assert main(["schema", "--output", str(path)]) == 0
    assert capsys.readouterr().err == ""
    assert json.loads(path.read_text(encoding="utf-8")) == generate_schema()
    assert path.read_text(encoding="utf-8") == schema_text()
    assert path.read_bytes() == (ROOT / "course.schema.json").read_bytes()


def test_schema_write_failure(tmp_path, capsys):
    path = tmp_path / "missing" / "schema.json"
    assert main(["schema", "--output", str(path)]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "Cannot write schema" in output.err


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
    assert definitions["Course"]["properties"]["description"]["type"] == "string"
    assert definitions["Trainer"]["properties"]["email"]["format"] == "email"
