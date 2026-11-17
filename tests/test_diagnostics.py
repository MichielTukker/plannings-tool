import pytest

from plannings_tool.diagnostics import display_location


@pytest.mark.parametrize(
    ("location", "expected"),
    [
        (
            ("days", 0, "schedule", 1, "Presentation", "duration"),
            "day 1|entry 2|duration",
        ),
        (("days", 1, "schedule", 2, "break", "duration"), "day 2|entry 3|duration"),
        (("days", 1, "schedule", 2, "break", "break"), "day 2|entry 3|break"),
        (("days", 0, "schedule", 2, "break"), "day 1|entry 3|break"),
        (("days", 0, "schedule", 0, "trainers", 1), "day 1|entry 1|trainer 2"),
        (("trainers", 0, "email"), "trainer 1|email"),
        (("course", "timezone"), "course|timezone"),
        ((), ""),
    ],
)
def test_display_location(location, expected):
    assert display_location(location) == expected
