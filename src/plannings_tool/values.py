"""Strict scalar values shared by validation and scheduling."""

import re
from datetime import date, time
from typing import Annotated

from pydantic import BeforeValidator, WithJsonSchema
from pydantic_core import PydanticCustomError

DURATION_PATTERN = r"^(?:[1-9][0-9]*m|[1-9][0-9]*h(?: [1-5]?[1-9]m| [1-5]0m)?)$"
TIME_PATTERN = r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$"
DATE_PATTERN = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"


def parse_duration_minutes(value: object) -> int:
    if not isinstance(value, str) or re.fullmatch(DURATION_PATTERN, value) is None:
        raise PydanticCustomError(
            "duration_syntax",
            "Invalid duration; use a value such as '15m' or '1h 15m'",
        )
    total = 0
    for component in value.split(" "):
        total += int(component[:-1]) * (60 if component.endswith("h") else 1)
    return total


def parse_clock_time(value: object) -> time:
    if not isinstance(value, str) or re.fullmatch(TIME_PATTERN, value) is None:
        raise PydanticCustomError("value_syntax", "Invalid time; expected HH:MM")
    hours, minutes = value.split(":")
    return time(hour=int(hours), minute=int(minutes))


def parse_calendar_date(value: object) -> date:
    if not isinstance(value, str) or re.fullmatch(DATE_PATTERN, value) is None:
        raise PydanticCustomError("value_syntax", "Invalid date; expected YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise PydanticCustomError(
            "calendar_date", "Date must be a real calendar date"
        ) from exc


Duration = Annotated[
    int,
    BeforeValidator(parse_duration_minutes),
    WithJsonSchema({"type": "string", "pattern": DURATION_PATTERN}, mode="validation"),
]
"""Elapsed whole minutes, written in YAML as e.g. ``15m`` or ``1h 15m``."""
ClockTime = Annotated[
    time,
    BeforeValidator(parse_clock_time),
    WithJsonSchema({"type": "string", "pattern": TIME_PATTERN}, mode="validation"),
]
CalendarDate = Annotated[
    date,
    BeforeValidator(parse_calendar_date),
    WithJsonSchema({"type": "string", "pattern": DATE_PATTERN}, mode="validation"),
]


def minutes_since_midnight(value: time) -> int:
    return value.hour * 60 + value.minute
