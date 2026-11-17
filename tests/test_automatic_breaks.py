from copy import deepcopy
from pathlib import Path

import pytest

from plannings_tool import (
    CourseDefinition,
    DiagnosticCode,
    SchedulingError,
    load_course,
    schedule_course,
)

ROOT = Path(__file__).resolve().parents[1]


def fixed(title, duration, kind="Presentation"):
    return {
        "title": title,
        "type": kind,
        "duration": duration,
        "trainers": ["alex"],
    }


def automatic_course(
    definition,
    schedule,
    *,
    start="09:00",
    end="11:00",
    coffee_break="15m",
    lunch="45m",
    date=None,
):
    definition["course"].update(start=start, end=end)
    definition["defaults"] = {
        "break_mode": "automatic",
        "coffee_break": coffee_break,
        "lunch": lunch,
    }
    definition["days"][0].update(start=start, end=end, schedule=schedule)
    if date is not None:
        definition["days"][0]["date"] = date
    return CourseDefinition.model_validate(definition)


def break_entries(day):
    return [entry for entry in day.entries if entry.kind == "break"]


def test_kleppen_acceptance_fixture_matches_automatic_schedule():
    result = schedule_course(
        load_course(ROOT / "testdata" / "Kleppen_auto_breaks.yaml")
    )
    day1, day2 = result.days

    assert [
        (entry.title, entry.local_start.strftime("%H:%M"))
        for entry in break_entries(day1)
    ] == [
        ("Coffee break", "10:30"),
        ("Lunch break", "12:30"),
        ("Coffee break", "14:45"),
    ]
    assert day1.entries[-1].local_start.strftime("%H:%M") == "16:15"
    assert not [item for item in result.warnings if item.location[1] == 0]

    split = [entry for entry in day2.entries if entry.schedule_index == 2]
    assert [(entry.part, entry.parts, entry.duration_minutes) for entry in split] == [
        (1, 2, 90),
        (2, 2, 30),
    ]
    assert split[0].source is split[1].source
    assert split[0].part_label == "part 1/2"
    assert split[1].part_label == "part 2/2"
    assert break_entries(day2)[1].local_start.strftime("%H:%M") == "12:15"
    assert [item.code for item in result.infos] == [DiagnosticCode.SESSION_SPLIT]
    assert [item.code for item in result.warnings] == [
        DiagnosticCode.INTERACTIVE_MINIMUM_OVERFLOW
    ]
    assert all(entry.is_automatic for entry in break_entries(day1))
    assert all(entry.schedule_index is None for entry in break_entries(day1))


def test_break_policy_uses_strict_day_boundaries(definition):
    course = automatic_course(
        definition,
        [fixed("Only session", "15m")],
        start="10:30",
        end="12:15",
    )
    result = schedule_course(course)
    assert not break_entries(result.days[0])
    assert not result.infos


def test_dated_automatic_break_uses_course_local_instants(definition):
    course = automatic_course(
        definition,
        [fixed("Before", "1h 30m"), fixed("After", "15m")],
        end="11:15",
        date="2027-01-15",
    )
    day = schedule_course(course).days[0]
    coffee = break_entries(day)[0]
    assert coffee.local_start.strftime("%H:%M") == "10:30"
    assert coffee.start_at is not None
    assert coffee.start_at.date().isoformat() == "2027-01-15"
    assert coffee.start_at.utcoffset().total_seconds() == 3600


def test_policy_uses_non_default_durations(definition):
    course = automatic_course(
        definition,
        [fixed("Before", "1h 30m"), fixed("After", "15m")],
        end="11:05",
        coffee_break="20m",
    )
    day = schedule_course(course).days[0]
    coffee = break_entries(day)[0]
    assert coffee.duration_minutes == 20
    assert coffee.local_start.strftime("%H:%M") == "10:30"
    assert coffee.local_end.strftime("%H:%M") == "10:50"
    assert not schedule_course(course).warnings


def test_policy_uses_non_default_lunch_duration(definition):
    course = automatic_course(
        definition,
        [fixed("Before lunch", "1h 15m"), fixed("After lunch", "15m")],
        start="11:00",
        end="13:00",
        lunch="30m",
    )
    day = schedule_course(course).days[0]
    lunch = break_entries(day)[0]
    assert lunch.title == "Lunch break"
    assert lunch.duration_minutes == 30
    assert lunch.local_start.strftime("%H:%M") == "12:15"
    assert not schedule_course(course).warnings


@pytest.mark.parametrize(
    ("first_duration", "expected_start"),
    [("1h 20m", "10:20"), ("2h", "10:00"), ("2h", "11:00")],
)
def test_boundary_placement_is_nearest_earliest_tie_and_inclusive(
    definition, first_duration, expected_start
):
    if first_duration == "1h 20m":
        schedule = [
            fixed("First", first_duration),
            fixed("Second", "20m"),
            fixed("Third", "20m"),
        ]
    elif expected_start == "10:00":
        schedule = [fixed("First", "1h"), fixed("Second", "1h")]
    else:
        schedule = [fixed("First", "2h"), fixed("Second", "15m")]
    course = automatic_course(definition, schedule, end="12:00")
    coffee = break_entries(schedule_course(course).days[0])[0]
    assert coffee.local_start.strftime("%H:%M") == expected_start


def test_break_splits_at_preferred_time_and_preserves_session_identity(definition):
    course = automatic_course(
        definition,
        [fixed("Long presentation", "1h 45m")],
    )
    day = schedule_course(course).days[0]
    fragments = [entry for entry in day.entries if entry.kind == "session"]
    assert [
        (entry.duration_minutes, entry.part, entry.parts) for entry in fragments
    ] == [
        (90, 1, 2),
        (15, 2, 2),
    ]
    assert fragments[0].source is fragments[1].source
    assert break_entries(day)[0].local_start.strftime("%H:%M") == "10:30"
    assert day.entries[0].schedule_index == day.entries[2].schedule_index == 0


@pytest.mark.parametrize(
    ("start", "end", "duration", "expected_split"),
    [
        ("10:20", "11:10", "40m", "10:45"),
        ("10:00", "10:40", "40m", "10:15"),
    ],
)
def test_split_moves_once_to_keep_fragments_at_least_15_minutes(
    definition, start, end, duration, expected_split
):
    course = automatic_course(
        definition, [fixed("Short edge session", duration)], start=start, end=end
    )
    result = schedule_course(course)
    day = result.days[0]
    fragments = [entry for entry in day.entries if entry.kind == "session"]
    assert [entry.local_end.strftime("%H:%M") for entry in fragments[:1]] == [
        expected_split
    ]
    assert all(entry.duration_minutes >= 15 for entry in fragments)


def test_a_fragment_can_be_split_again_by_a_later_break(definition):
    course = automatic_course(
        definition,
        [fixed("Opening", "30m"), fixed("Long presentation", "3h 30m")],
        end="14:00",
    )
    day = schedule_course(course).days[0]
    fragments = [entry for entry in day.entries if entry.schedule_index == 1]
    assert [
        (entry.part, entry.parts, entry.duration_minutes) for entry in fragments
    ] == [
        (1, 3, 60),
        (2, 3, 90),
        (3, 3, 60),
    ]
    assert [entry.local_start.strftime("%H:%M") for entry in break_entries(day)] == [
        "10:30",
        "12:15",
    ]
    assert (
        len(
            [
                item
                for item in schedule_course(course).infos
                if item.code == "session_split"
            ]
        )
        == 2
    )


def test_unsplittable_excursion_fails_with_actionable_placement_error(definition):
    course = automatic_course(
        definition,
        [fixed("Field visit", "1h", "Excursion")],
        start="10:00",
        end="11:30",
    )
    with pytest.raises(SchedulingError) as caught:
        schedule_course(course)
    diagnostic = caught.value.diagnostics[0]
    assert diagnostic.code == DiagnosticCode.AUTOMATIC_BREAK_PLACEMENT
    assert diagnostic.location == ("days", 0, "schedule", 0)
    assert "Morning coffee" in diagnostic.message
    assert "10:00-11:00" in diagnostic.message
    assert "Field visit" in diagnostic.message
    assert "(Excursion)" in diagnostic.message
    assert "cannot be split" in diagnostic.message
    assert "break_mode: manual" in diagnostic.message


def test_interactive_session_is_never_split(definition):
    course = automatic_course(
        definition,
        [
            {
                "title": "Discussion",
                "type": "Interactive",
                "duration": "remaining",
                "trainers": ["alex"],
            }
        ],
        end="11:00",
    )
    with pytest.raises(SchedulingError) as caught:
        schedule_course(course)
    diagnostic = caught.value.diagnostics[0]
    assert diagnostic.code == DiagnosticCode.AUTOMATIC_BREAK_PLACEMENT
    assert "Interactive sessions cannot be split" in diagnostic.message


def test_too_short_splittable_session_fails_with_fragment_reason(definition):
    course = automatic_course(
        definition,
        [fixed("Short talk", "20m")],
        start="10:20",
        end="11:10",
    )
    with pytest.raises(SchedulingError) as caught:
        schedule_course(course)
    assert (
        "fragment would be shorter than 15 minutes"
        in caught.value.diagnostics[0].message
    )


def test_omission_is_info_and_activities_are_unchanged(definition):
    course = automatic_course(definition, [fixed("Morning session", "1h")], end="11:00")
    result = schedule_course(course)
    assert not break_entries(result.days[0])
    assert result.days[0].entries[0].end_offset_minutes == 60
    assert len(result.infos) == 1
    assert result.infos[0].code == DiagnosticCode.AUTOMATIC_BREAK_OMITTED
    assert "end at 10:00" in result.infos[0].message
    assert "preferred start 10:30" in result.infos[0].message
    assert "left unused" in result.infos[0].message
    assert [item.code for item in result.warnings] == [DiagnosticCode.UNUSED_TIME]


def test_omission_reallocates_interactive_and_restarts_placement(definition):
    course = automatic_course(
        definition,
        [
            fixed("First", "1h 30m"),
            fixed("Second", "1h 30m"),
            {
                "title": "Discussion",
                "type": "Interactive",
                "duration": "remaining",
                "trainers": ["sam"],
            },
        ],
        end="15:05",
    )
    result = schedule_course(course)
    day = result.days[0]
    assert [entry.title for entry in break_entries(day)] == [
        "Coffee break",
        "Lunch break",
    ]
    assert day.entries[-1].duration_minutes == 125
    assert day.entries[-1].local_end.strftime("%H:%M") == "15:05"
    assert len(result.infos) == 1
    assert result.infos[0].code == DiagnosticCode.AUTOMATIC_BREAK_OMITTED
    assert "end at 14:50" in result.infos[0].message
    assert not result.warnings


def test_reallocation_rebuilds_break_times_when_interactive_is_first(definition):
    course = automatic_course(
        definition,
        [
            {
                "title": "Discussion",
                "type": "Interactive",
                "duration": "remaining",
                "trainers": ["sam"],
            },
            fixed("First presentation", "1h 40m"),
            fixed("Second presentation", "1h 30m"),
        ],
        end="15:05",
    )
    result = schedule_course(course)
    day = result.days[0]
    assert [entry.local_start.strftime("%H:%M") for entry in break_entries(day)] == [
        "10:55",
        "12:15",
    ]
    first_presentation = [entry for entry in day.entries if entry.schedule_index == 1]
    assert [entry.duration_minutes for entry in first_presentation] == [65, 35]
    assert day.entries[0].duration_minutes == 115
    assert day.entries[-1].local_end.strftime("%H:%M") == "15:05"
    assert [item.code for item in result.infos] == [
        DiagnosticCode.AUTOMATIC_BREAK_OMITTED,
        DiagnosticCode.SESSION_SPLIT,
    ]
    assert not result.warnings


def test_restart_discards_split_info_from_abandoned_pass(definition):
    course = automatic_course(
        definition,
        [
            {
                "title": "Discussion",
                "type": "Interactive",
                "duration": "remaining",
                "trainers": ["sam"],
            },
            fixed("Long presentation", "1h 45m"),
        ],
        end="12:45",
    )
    result = schedule_course(course)
    assert [
        entry.local_start.strftime("%H:%M") for entry in break_entries(result.days[0])
    ] == ["10:45"]
    assert [item.code for item in result.infos] == [
        DiagnosticCode.AUTOMATIC_BREAK_OMITTED
    ]
    assert [
        entry.part for entry in result.days[0].entries if entry.kind == "session"
    ] == [
        1,
        1,
    ]


def test_restart_fails_when_reallocation_blocks_a_remaining_break(definition):
    course = automatic_course(
        definition,
        [
            {
                "title": "Discussion",
                "type": "Interactive",
                "duration": "remaining",
                "trainers": ["sam"],
            },
            fixed("Presentation", "1h 20m"),
        ],
        end="12:45",
    )
    with pytest.raises(SchedulingError) as caught:
        schedule_course(course)
    diagnostic = caught.value.diagnostics[0]
    assert diagnostic.code == DiagnosticCode.AUTOMATIC_BREAK_PLACEMENT
    assert "Morning coffee" in diagnostic.message
    assert "Interactive" in diagnostic.message


def test_final_minimum_allocation_recomputes_overflow_after_omission(definition):
    course = automatic_course(
        definition,
        [
            fixed("Long session", "2h 30m"),
            {
                "title": "Discussion",
                "type": "Interactive",
                "duration": "remaining",
                "trainers": ["sam"],
            },
        ],
        start="11:00",
        end="17:00",
        coffee_break="5h",
    )
    result = schedule_course(course)
    assert result.days[0].entries[-1].duration_minutes == 165
    assert result.days[0].entries[-1].local_end.strftime("%H:%M") == "17:00"
    assert [item.code for item in result.infos] == [
        DiagnosticCode.AUTOMATIC_BREAK_OMITTED,
        DiagnosticCode.SESSION_SPLIT,
    ]
    assert not result.warnings


def test_all_omissions_terminate_and_report_once_per_rule(definition):
    course = automatic_course(
        definition,
        [fixed("Early session", "15m")],
        end="17:00",
    )
    result = schedule_course(course)
    assert not break_entries(result.days[0])
    assert [item.code for item in result.infos] == [
        DiagnosticCode.AUTOMATIC_BREAK_OMITTED,
        DiagnosticCode.AUTOMATIC_BREAK_OMITTED,
        DiagnosticCode.AUTOMATIC_BREAK_OMITTED,
    ]


def test_automatic_break_crossing_day_end_warns_at_day_location(definition):
    course = automatic_course(
        definition,
        [fixed("Long presentation", "1h 50m")],
        end="10:40",
    )
    result = schedule_course(course)
    assert result.warnings[0].code == DiagnosticCode.SCHEDULE_OVERFLOW
    assert result.warnings[0].location == ("days", 0, "schedule")


def test_placement_errors_are_collected_across_days(definition):
    first = deepcopy(definition["days"][0])
    first.update(
        label="Day 2",
        start="10:00",
        end="11:30",
        schedule=[fixed("First excursion", "1h", "Excursion")],
    )
    definition["days"][0].update(
        start="10:00",
        end="11:30",
        schedule=[fixed("First excursion", "1h", "Excursion")],
    )
    definition["days"].append(first)
    definition["course"].update(start="10:00", end="11:30")
    definition["defaults"] = {"break_mode": "automatic"}
    course = CourseDefinition.model_validate(definition)
    with pytest.raises(SchedulingError) as caught:
        schedule_course(course)
    assert [item.location for item in caught.value.diagnostics] == [
        ("days", 0, "schedule", 0),
        ("days", 1, "schedule", 0),
    ]
