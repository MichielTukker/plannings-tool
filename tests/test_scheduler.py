from copy import deepcopy
from datetime import date, time, timedelta, timezone

import pytest
import yaml
from typer.testing import CliRunner

from plannings_tool import (
    CourseDefinition,
    ScheduleResult,
    SchedulingError,
    schedule_course,
)
from plannings_tool.cli import app

runner = CliRunner()


def build_course(definition):
    return CourseDefinition.model_validate(definition)


def fixed(title, duration, trainers=None):
    return {
        "title": title,
        "type": "Presentation",
        "duration": duration,
        "trainers": trainers or ["alex"],
    }


def warning_codes(result):
    return [warning.code for warning in result.warnings]


def test_typed_temporal_values_and_default_timezone(definition):
    definition["days"][0]["date"] = "2027-01-15"
    course = build_course(definition)
    assert isinstance(course.course.start, time)
    assert isinstance(course.course.end, time)
    assert course.days[0].date == date(2027, 1, 15)
    assert course.course.timezone == "Europe/Amsterdam"


def test_manual_schedule_preserves_order_and_break_duration(definition):
    definition["course"].update(start="09:00", end="12:00")
    definition["defaults"] = {"coffee_break": "15m"}
    definition["days"][0]["schedule"] = [
        fixed("First", "30m"),
        {"break": "coffee"},
        fixed("Second", "30m"),
        {
            "title": "Discussion",
            "type": "Interactive",
            "duration": "remaining",
            "trainers": ["sam"],
        },
    ]
    result = schedule_course(build_course(definition))
    day = result.days[0]
    assert isinstance(result, ScheduleResult)
    assert [entry.schedule_index for entry in day.entries] == [0, 1, 2, 3]
    assert [entry.duration_minutes for entry in day.entries] == [30, 15, 30, 105]
    assert [
        (entry.start_offset_minutes, entry.end_offset_minutes) for entry in day.entries
    ] == [(0, 30), (30, 45), (45, 75), (75, 180)]
    assert day.entries[1].title == "Coffee break"
    assert day.entries[1].break_kind == "coffee"
    assert day.entries[-1].local_start == time(10, 15)
    assert day.entries[-1].local_end == time(12, 0)
    assert warning_codes(result) == []


def test_break_override_and_interactive_in_middle(definition):
    definition["course"].update(start="09:00", end="12:00")
    definition["days"][0]["schedule"] = [
        fixed("First", "30m"),
        {"break": "lunch", "duration": "30m"},
        {
            "title": "Discussion",
            "type": "Interactive",
            "duration": "remaining",
            "trainers": ["sam"],
        },
        fixed("Last", "45m"),
    ]
    result = schedule_course(build_course(definition))
    entries = result.days[0].entries
    assert [entry.duration_minutes for entry in entries] == [30, 30, 75, 45]
    assert entries[2].start_offset_minutes == 60
    assert entries[2].end_offset_minutes == 135
    assert entries[3].local_start == time(11, 15)
    assert entries[-1].end_offset_minutes == 180
    assert warning_codes(result) == []


def test_interactive_exactly_minimum_does_not_warn(definition):
    definition["course"].update(start="09:00", end="10:00")
    definition["days"][0]["schedule"] = [
        fixed("Fixed", "45m"),
        {
            "title": "Discussion",
            "type": "Interactive",
            "duration": "remaining",
            "trainers": ["sam"],
        },
    ]
    result = schedule_course(build_course(definition))
    assert [entry.duration_minutes for entry in result.days[0].entries] == [45, 15]
    assert warning_codes(result) == []


@pytest.mark.parametrize(
    ("fixed_minutes", "expected_overflow"),
    [(46, 1), (59, 14), (60, 15), (75, 30)],
)
def test_interactive_minimum_overflow(definition, fixed_minutes, expected_overflow):
    definition["course"].update(start="09:00", end="10:00")
    definition["days"][0]["schedule"] = [
        fixed("First", f"{fixed_minutes}m"),
        {
            "title": "Discussion",
            "type": "Interactive",
            "duration": "remaining",
            "trainers": ["sam"],
        },
    ]
    result = schedule_course(build_course(definition))
    assert result.days[0].entries[-1].duration_minutes == 15
    assert warning_codes(result) == ["interactive_minimum_overflow"]
    assert str(expected_overflow) in result.warnings[0].message


def test_fixed_schedule_overflow_and_unused_time(definition):
    definition["course"].update(start="09:00", end="10:00")
    definition["days"][0]["schedule"] = [
        fixed("First", "45m"),
        fixed("Second", "30m"),
    ]
    result = schedule_course(build_course(definition))
    assert warning_codes(result) == ["schedule_overflow"]
    assert result.warnings[0].location == ("days", 0, "schedule", 1)
    assert result.days[0].entries[-1].end_offset_minutes == 75

    definition["days"][0]["schedule"] = [fixed("First", "30m")]
    result = schedule_course(build_course(definition))
    assert warning_codes(result) == ["unused_time"]
    assert "30 minutes" in result.warnings[0].message


def test_phase_one_missing_break_warning_is_included_once(definition):
    result = schedule_course(build_course(definition))
    assert warning_codes(result).count("missing_manual_breaks") == 1
    assert warning_codes(result).count("unused_time") == 1


def test_repeated_and_edge_breaks_are_scheduled_in_place(definition):
    definition["course"].update(start="09:00", end="10:00")
    definition["days"][0]["schedule"] = [
        {"break": "coffee"},
        fixed("Short session", "15m"),
        {"break": "lunch", "duration": "10m"},
    ]
    result = schedule_course(build_course(definition))
    assert [
        (entry.start_offset_minutes, entry.end_offset_minutes)
        for entry in result.days[0].entries
    ] == [(0, 15), (15, 30), (30, 40)]
    assert warning_codes(result) == ["unused_time"]


@pytest.mark.parametrize(
    ("default_mode", "day_mode"),
    [("automatic", None), ("manual", "automatic")],
)
def test_automatic_mode_is_not_scheduled(definition, default_mode, day_mode):
    definition["defaults"] = {"break_mode": default_mode}
    if day_mode is not None:
        definition["days"][0]["break_mode"] = day_mode
    with pytest.raises(SchedulingError) as caught:
        schedule_course(build_course(definition))
    assert caught.value.diagnostics[0].code == "automatic_breaks_not_supported"
    assert caught.value.diagnostics[0].location == ("days", 0, "break_mode")


def test_dated_schedule_has_aware_instants(definition):
    definition["days"][0]["date"] = "2027-01-15"
    result = schedule_course(build_course(definition))
    day = result.days[0]
    assert day.date == date(2027, 1, 15)
    assert day.start_at is not None and day.end_at is not None
    assert day.start_at.utcoffset() == timedelta(hours=1)
    assert day.entries[0].start_at == day.start_at
    assert day.entries[0].end_at is not None
    assert day.entries[0].end_at.astimezone(timezone.utc) - day.start_at.astimezone(
        timezone.utc
    ) == timedelta(minutes=30)


def test_custom_timezone_is_used(definition):
    definition["course"]["timezone"] = "America/Los_Angeles"
    definition["days"][0]["date"] = "2027-01-15"
    day = schedule_course(build_course(definition)).days[0]
    assert day.timezone == "America/Los_Angeles"
    assert day.start_at is not None
    assert day.start_at.utcoffset() == timedelta(hours=-8)


@pytest.mark.parametrize(
    ("local_date", "start", "end", "code"),
    [
        ("2027-03-28", "02:30", "04:00", "nonexistent_local_time"),
        ("2027-10-31", "02:30", "04:00", "ambiguous_local_time"),
        ("2027-03-28", "01:00", "04:00", "timezone_transition"),
        ("2027-10-31", "01:00", "04:00", "timezone_transition"),
    ],
)
def test_dst_errors(definition, local_date, start, end, code):
    definition["days"][0].update(date=local_date, start=start, end=end)
    with pytest.raises(SchedulingError) as caught:
        schedule_course(build_course(definition))
    assert caught.value.diagnostics[0].code == code


def test_undated_schedule_remains_floating(definition):
    result = schedule_course(build_course(definition))
    day = result.days[0]
    assert day.date is None
    assert day.start_at is None
    assert day.end_at is None
    assert day.entries[0].start_at is None
    assert day.entries[0].local_start == time(9, 0)


def test_scheduler_does_not_mutate_input(definition):
    original = deepcopy(definition)
    schedule_course(build_course(definition))
    assert definition == original


def test_schedule_cli_rich_preview_and_warnings(tmp_path, definition):
    definition["course"].update(start="09:00", end="10:00")
    definition["days"][0]["date"] = "2027-01-15"
    definition["days"][0]["location"] = "Training room"
    definition["days"][0]["schedule"] = [
        fixed("Opening", "30m"),
        {"break": "coffee"},
        fixed("Workshop", "30m"),
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


def test_schedule_cli_automatic_mode_error(tmp_path, definition):
    definition["defaults"] = {"break_mode": "automatic"}
    path = tmp_path / "course.yaml"
    path.write_text(yaml.safe_dump(definition), encoding="utf-8")
    result = runner.invoke(app, ["schedule", str(path)])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert "[ERROR] - course.yaml:day 1|break_mode - " in result.stderr
    assert "Automatic break placement is not supported yet" in result.stderr
    assert "automatic_breaks_not_supported" not in result.stderr
