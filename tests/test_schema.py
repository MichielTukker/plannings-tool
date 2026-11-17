import json
from pathlib import Path

import pytest

from plannings_tool import generate_schema
from plannings_tool.schema import render_schema, write_schema

ROOT = Path(__file__).resolve().parents[1]


def test_schema_export_and_drift(tmp_path):
    path = tmp_path / "schema.json"
    write_schema(path)
    assert json.loads(path.read_text(encoding="utf-8")) == generate_schema()
    assert path.read_text(encoding="utf-8") == render_schema()
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
