import pytest
from pydantic import TypeAdapter, ValidationError

from plannings_tool.values import CalendarDate, ClockTime, Duration


@pytest.mark.parametrize(
    ("text", "minutes"),
    [
        ("15m", 15),
        ("90m", 90),
        ("1h", 60),
        ("2h", 120),
        ("1h 15m", 75),
        ("1h 1m", 61),
        ("1h 10m", 70),
        ("1h 59m", 119),
    ],
)
def test_duration_conversion(text, minutes):
    assert TypeAdapter(Duration).validate_python(text).minutes == minutes


@pytest.mark.parametrize(
    "value",
    [
        15,
        True,
        1.5,
        "0m",
        "0h",
        "-1m",
        "1.5h",
        "01m",
        "01h",
        "1H",
        "1h15m",
        "1h 0m",
        "1h 01m",
        "1h 60m",
        "1h  15m",
        " 15m",
        "15m ",
        "15m\n",
        "remaining",
        "15s",
    ],
)
def test_invalid_duration(value):
    with pytest.raises(ValidationError):
        TypeAdapter(Duration).validate_python(value)


@pytest.mark.parametrize(
    ("value", "minutes"), [("00:00", 0), ("09:30", 570), ("23:59", 1439)]
)
def test_clock_time(value, minutes):
    assert TypeAdapter(ClockTime).validate_python(value).minutes == minutes


@pytest.mark.parametrize(
    "value", ["24:00", "09:60", "9:00", "09:0", "09:00:00", "09:00\n", 900]
)
def test_invalid_time(value):
    with pytest.raises(ValidationError):
        TypeAdapter(ClockTime).validate_python(value)


@pytest.mark.parametrize(
    "value", ["2027-02-29", "2027-13-01", "0000-01-01", "2027-1-01"]
)
def test_invalid_date(value):
    with pytest.raises(ValidationError):
        TypeAdapter(CalendarDate).validate_python(value)


def test_leap_date():
    assert TypeAdapter(CalendarDate).validate_python("2028-02-29") == "2028-02-29"
