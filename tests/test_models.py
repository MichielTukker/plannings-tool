from copy import deepcopy
from datetime import time

import pytest
from pydantic import ValidationError

from plannings_tool.models import (
    Break,
    CourseDefinition,
    Day,
    Excursion,
    Exercise,
    Interactive,
    Presentation,
)


def validate(definition):
    return CourseDefinition.model_validate(definition)


def assert_error(definition, code, location=None):
    with pytest.raises(ValidationError) as caught:
        validate(definition)
    matching = [error for error in caught.value.errors() if error["type"] == code]
    assert matching, caught.value.errors()
    if location is not None:
        assert any(error["loc"] == location for error in matching)


def test_defaults_and_independent_inheritance(definition):
    definition["days"][0]["end"] = "16:00"
    result = validate(definition)
    day = result.days[0]
    assert result.defaults.coffee_break.minutes == 15
    assert result.defaults.lunch.minutes == 45
    assert day.effective_start(result.course) == time(9, 0)
    assert day.effective_end(result.course) == time(16, 0)
    assert day.effective_break_mode(result.defaults) == "manual"
    assert "start" not in day.model_fields_set
    assert "end" in day.model_fields_set
    assert result.course.timezone == "Europe/Amsterdam"


@pytest.mark.parametrize(
    ("timezone_name", "valid"),
    [
        ("Europe/Amsterdam", True),
        ("America/Los_Angeles", True),
        ("Invalid/Zone", False),
    ],
)
def test_course_timezone(definition, timezone_name, valid):
    definition["course"]["timezone"] = timezone_name
    if valid:
        assert (
            CourseDefinition.model_validate(definition).course.timezone == timezone_name
        )
    else:
        assert_error(definition, "timezone_name", ("course", "timezone"))


@pytest.mark.parametrize("version", [True, False, "1", 1.0, 2, None])
def test_strict_version(definition, version):
    definition["schema_version"] = version
    with pytest.raises(ValidationError):
        validate(definition)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("course", "title"), " \n"),
        (("course", "description"), ""),
        (("days", 0, "location"), None),
        (("course", "project_id"), " "),
        (("trainers", 0, "name"), "\t"),
        (("trainers", 0, "email"), "not-an-email"),
        (("trainers", 0, "id"), "Alex"),
        (("trainers", 0, "id"), "alex\n"),
        (("trainers", 0, "color"), "#fff"),
        (("trainers", 0, "color"), "#336699\n"),
        (("trainers", 0, "color"), None),
        (("days", 0, "label"), ""),
        (("days", 0, "date"), None),
        (("days", 0, "start"), None),
        (("days", 0, "break_mode"), None),
        (("days", 0, "schedule", 0, "title"), 42),
        (("days", 0, "schedule", 0, "trainers"), []),
        (("days", 0, "schedule", 0, "trainers"), ["alex", "alex"]),
        (("defaults",), None),
        (("trainers",), []),
        (("days",), []),
        (("days", 0, "schedule"), []),
    ],
)
def test_invalid_fields(definition, path, value):
    parent = definition
    for key in path[:-1]:
        parent = parent[key]
    parent[path[-1]] = value
    with pytest.raises(ValidationError):
        validate(definition)


@pytest.mark.parametrize(
    "path",
    [(), ("course",), ("trainers", 0), ("days", 0), ("days", 0, "schedule", 0)],
)
def test_unknown_fields(definition, path):
    target = definition
    for key in path:
        target = target[key]
    target["unexpected"] = True
    assert_error(definition, "extra_forbidden")


def test_location_is_per_day(definition):
    definition["days"][0]["location"] = "Training room A"
    second_day = deepcopy(definition["days"][0])
    second_day.update(label="Day 2", location="Training room B")
    definition["days"].append(second_day)
    course = validate(definition)
    assert [day.location for day in course.days] == [
        "Training room A",
        "Training room B",
    ]
    definition["course"]["location"] = "Course-wide room"
    assert_error(definition, "extra_forbidden", ("course", "location"))


@pytest.mark.parametrize("field", ["name", "email"])
def test_required_trainer_fields(definition, field):
    del definition["trainers"][0][field]
    assert_error(definition, "missing", ("trainers", 0, field))


def test_duplicate_ids_and_unknown_reference_locations(definition):
    definition["trainers"][1]["id"] = "alex"
    definition["days"][0]["schedule"][0]["trainers"] = ["missing"]
    assert_error(definition, "duplicate_trainer_id", ("trainers", 1, "id"))
    assert_error(
        definition, "unknown_trainer", ("days", 0, "schedule", 0, "trainers", 0)
    )


def test_trainer_reuse_multiple_assignments_and_no_normalization(definition):
    definition["trainers"][0]["email"] = "Alex@EXAMPLE.ORG"
    definition["trainers"][1]["email"] = "Alex@EXAMPLE.ORG"
    session = definition["days"][0]["schedule"][0]
    session["trainers"] = ["alex", "sam"]
    session["title"] = " Introduction "
    definition["days"][0]["schedule"].append(deepcopy(session))
    result = validate(definition)
    assert result.trainers[0].email == "Alex@EXAMPLE.ORG"
    assert result.days[0].schedule[0].title == " Introduction "


@pytest.mark.parametrize(
    ("kind", "duration", "valid"),
    [
        ("Presentation", "14m", False),
        ("Presentation", "15m", True),
        ("Exercise", "14m", False),
        ("Exercise", "15m", True),
        ("Excursion", "29m", False),
        ("Excursion", "30m", True),
        ("Interactive", "remaining", True),
        ("Interactive", "30m", False),
        ("Presentation", "remaining", False),
        ("presentation", "30m", False),
        ("Unknown", "30m", False),
        ("Presentation", "100h", True),
    ],
)
def test_session_variants(definition, kind, duration, valid):
    definition["days"][0]["schedule"][0].update(type=kind, duration=duration)
    if valid:
        validate(definition)
    else:
        with pytest.raises(ValidationError):
            validate(definition)


def test_fixed_session_minimums_are_declared_on_types():
    assert Presentation.minimum_minutes == 15
    assert Presentation.splittable
    assert Exercise.minimum_minutes == 15
    assert Exercise.splittable
    assert Excursion.minimum_minutes == 30
    assert not Excursion.splittable
    assert Interactive.minimum_minutes == 15
    assert Interactive.flexible


def test_schedule_entry_discriminator_errors_are_actionable(definition):
    entry = definition["days"][0]["schedule"][0]
    del entry["type"]
    with pytest.raises(ValidationError) as caught:
        validate(definition)
    assert any(
        error["type"] == "schedule_entry_type" and "break or a session" in error["msg"]
        for error in caught.value.errors()
    )

    entry["type"] = "Unknown"
    with pytest.raises(ValidationError) as caught:
        validate(definition)
    errors = caught.value.errors()
    assert any(error["type"] == "union_tag_invalid" for error in errors)
    assert all("entry_kind()" not in error["msg"] for error in errors)


def test_schedule_entry_discriminator_accepts_existing_models(definition):
    course = validate(definition)
    day = course.days[0]
    session = day.schedule[0]
    parsed = Day.model_validate(
        {
            "label": day.label,
            "schedule": [
                Break.model_validate({"break": "coffee"}),
                session,
            ],
        }
    )
    assert isinstance(parsed.schedule[0], Break)
    assert isinstance(parsed.schedule[1], Presentation)


def test_flexible_anywhere_and_at_most_one(definition):
    schedule = definition["days"][0]["schedule"]
    flexible = {
        "title": "Discussion",
        "type": "Interactive",
        "duration": "remaining",
        "trainers": ["alex"],
    }
    schedule.insert(0, flexible)
    validate(definition)
    schedule.append(deepcopy(flexible))
    assert_error(definition, "multiple_interactive", ("days", 0, "schedule", 2, "type"))


def test_label_uniqueness_case_sensitive(definition):
    day = deepcopy(definition["days"][0])
    day["label"] = "day 1"
    definition["days"].append(day)
    validate(definition)
    day["label"] = "Day 1"
    assert_error(definition, "duplicate_day_label", ("days", 1, "label"))


@pytest.mark.parametrize("last_date", ["2027-03-01", "2027-02-28", "2027-03-02"])
def test_date_order_ignores_undated_days(definition, last_date):
    day = definition["days"][0]
    day["date"] = "2027-03-01"
    undated = deepcopy(day)
    undated.pop("date")
    undated["label"] = "Undated"
    last = deepcopy(day)
    last.update(label="Last", date=last_date)
    definition["days"].extend([undated, last])
    if last_date == "2027-03-02":
        validate(definition)
    else:
        assert_error(definition, "date_order", ("days", 2, "date"))


@pytest.mark.parametrize(
    ("target", "overrides", "location"),
    [
        ("course", {"end": "09:00"}, ("course", "end")),
        ("course", {"end": "08:00"}, ("course", "end")),
        ("day", {"start": "18:00"}, ("days", 0, "end")),
        ("day", {"end": "08:00"}, ("days", 0, "end")),
    ],
)
def test_invalid_effective_times(definition, target, overrides, location):
    item = definition["course"] if target == "course" else definition["days"][0]
    item.update(overrides)
    assert_error(definition, "time_order", location)


def test_break_defaults_overrides_and_all_break_day(definition):
    definition["defaults"] = {"coffee_break": "20m"}
    definition["days"][0]["schedule"] = [
        {"break": "coffee"},
        {"break": "lunch", "duration": "30m"},
        {"break": "coffee"},
    ]
    result = validate(definition)
    breaks = result.days[0].schedule
    assert all(isinstance(entry, Break) for entry in breaks)
    assert [entry.effective_duration(result.defaults).minutes for entry in breaks] == [
        20,
        30,
        20,
    ]


@pytest.mark.parametrize(
    "entry",
    [
        {"break": "travel"},
        {"break": "coffee", "duration": "0m"},
        {"break": "coffee", "duration": None},
        {"break": "coffee", "trainers": ["alex"]},
        {"break": "coffee", "type": "Presentation", "title": "Mixed"},
    ],
)
def test_invalid_breaks(definition, entry):
    definition["days"][0]["schedule"] = [entry]
    with pytest.raises(ValidationError):
        validate(definition)


def test_effective_break_mode(definition):
    definition["defaults"] = {"break_mode": "automatic"}
    validate(definition)
    definition["days"][0]["schedule"].append({"break": "coffee"})
    assert_error(
        definition, "manual_break_in_automatic", ("days", 0, "schedule", 1, "break")
    )
    definition["days"][0]["break_mode"] = "manual"
    validate(definition)
