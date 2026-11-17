from datetime import date
from datetime import time as clock_time
from pathlib import Path

import pytest
import yaml

from plannings_tool import (
    CourseValidationError,
    Severity,
    load_course,
    parse_course,
)
from plannings_tool.diagnostics import display_location

ROOT = Path(__file__).resolve().parents[1]


def test_example():
    result = load_course(ROOT / "testdata" / "Kleppen-cursus-2026.yaml")
    assert len(result.days) == 2
    assert result.days[1].effective_start(result.course) == clock_time(9, 0)
    assert [day.location for day in result.days] == [
        "Paviljoen 2&3",
        "Paviljoen 2&3",
    ]


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("course: [", "yaml_syntax"),
        ("schema_version: 1\nschema_version: 1", "duplicate_key"),
        ("course:\n  title: One\n  title: Two", "duplicate_key"),
        ("base: &base {a: 1}\nother: {<<: *base, a: 2}", "duplicate_key"),
        ("a: 1\n---\nb: 2", "yaml_syntax"),
        ("", "root_mapping"),
        ("[]", "root_mapping"),
        ("null", "root_mapping"),
        ("!!python/object/apply:os.system ['echo unsafe']", "yaml_syntax"),
        ("? [unhashable]\n: value", "yaml_syntax"),
    ],
)
def test_yaml_failures(text, code):
    with pytest.raises(CourseValidationError) as caught:
        parse_course(text)
    diagnostic = caught.value.diagnostics[0]
    assert diagnostic.severity == Severity.ERROR
    assert diagnostic.code == code
    if code != "root_mapping":
        assert diagnostic.line is not None
        assert diagnostic.column is not None


def test_model_error_locations(definition):
    definition["days"][0]["schedule"][0]["duration"] = "14m"
    with pytest.raises(CourseValidationError) as caught:
        parse_course(yaml.safe_dump(definition))
    issue = caught.value.diagnostics[0]
    assert issue.code == "duration_minimum"
    assert issue.location[:4] == ("days", 0, "schedule", 0)
    assert issue.location[-1] == "duration"
    assert display_location(issue.location) == "day 1|entry 1|duration"
    assert "days[0].schedule[0]" in str(issue)


def test_unquoted_yaml_date_rejected(definition):
    definition["days"][0]["date"] = date(2027, 3, 1)
    with pytest.raises(CourseValidationError):
        parse_course(yaml.safe_dump(definition))


def test_file_errors(tmp_path):
    with pytest.raises(CourseValidationError) as caught:
        load_course(tmp_path / "missing.yaml")
    assert caught.value.diagnostics[0].code == "file_read"
    invalid = tmp_path / "invalid.yaml"
    invalid.write_bytes(b"\xff")
    with pytest.raises(CourseValidationError):
        load_course(invalid)
