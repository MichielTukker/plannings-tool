import pytest
import yaml

from plannings_tool import Severity, check_missing_manual_breaks, parse_course


@pytest.mark.parametrize(
    ("default_mode", "override_mode", "end", "has_break", "expected"),
    [
        ("manual", None, "17:00", False, True),
        ("manual", None, "13:00", False, False),
        ("manual", None, "12:59", False, False),
        ("manual", None, "13:01", False, True),
        ("manual", None, "17:00", True, False),
        ("automatic", None, "17:00", False, False),
        ("manual", "automatic", "17:00", False, False),
        ("automatic", "manual", "17:00", False, True),
    ],
)
def test_missing_manual_break_warning(
    definition, default_mode, override_mode, end, has_break, expected
):
    definition["defaults"] = {"break_mode": default_mode}
    day = definition["days"][0]
    day["end"] = end
    if override_mode is not None:
        day["break_mode"] = override_mode
    if has_break:
        day["schedule"].append({"break": "coffee"})
    course = parse_course(yaml.safe_dump(definition))
    warnings = check_missing_manual_breaks(course)
    assert len(warnings) == int(expected)
    if expected:
        assert warnings[0].severity == Severity.WARNING
        assert warnings[0].code == "missing_manual_breaks"
        assert warnings[0].location == ("days", 0, "schedule")
        assert "Day 1" in warnings[0].message


def test_missing_break_warning_uses_inherited_times(definition):
    definition["course"]["end"] = "13:00"
    course = parse_course(yaml.safe_dump(definition))
    assert check_missing_manual_breaks(course) == []
    definition["course"]["end"] = "13:01"
    course = parse_course(yaml.safe_dump(definition))
    assert len(check_missing_manual_breaks(course)) == 1
