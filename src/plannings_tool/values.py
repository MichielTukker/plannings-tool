"""Strict scalar values shared by validation and scheduling."""

import re
from datetime import date, time
from typing import Annotated, Any, ClassVar, Self

from pydantic import (
    BeforeValidator,
    GetCoreSchemaHandler,
    GetJsonSchemaHandler,
    ValidationError,
    WithJsonSchema,
)
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema, PydanticCustomError, core_schema
from pydantic_core.core_schema import ValidatorFunctionWrapHandler

DURATION_PATTERN = r"^(?:[1-9][0-9]*m|[1-9][0-9]*h(?: [1-5]?[1-9]m| [1-5]0m)?)$"
TIME_PATTERN = r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$"
DATE_PATTERN = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"


class StringValue(str):
    pattern: ClassVar[str]

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source: type, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        return core_schema.no_info_wrap_validator_function(
            cls._validate,
            core_schema.str_schema(strict=True, pattern=cls.pattern),
        )

    @classmethod
    def _validate(cls, value: Any, handler: ValidatorFunctionWrapHandler) -> Self:
        try:
            return cls(handler(value))
        except ValidationError as exc:
            if any(
                error["type"] == "string_pattern_mismatch" for error in exc.errors()
            ):
                raise PydanticCustomError(
                    "duration_syntax",
                    "Invalid duration; use a value such as '15m' or '1h 15m'",
                ) from exc
            raise

    @classmethod
    def __get_pydantic_json_schema__(
        cls, schema: CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        return {"type": "string", "pattern": cls.pattern}


class Duration(StringValue):
    pattern = DURATION_PATTERN

    @property
    def minutes(self) -> int:
        total = 0
        for component in self.split(" "):
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
