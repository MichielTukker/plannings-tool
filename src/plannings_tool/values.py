"""Strict scalar values shared by validation and scheduling."""

import re
from datetime import date
from typing import ClassVar

from pydantic import GetCoreSchemaHandler
from pydantic_core import CoreSchema, PydanticCustomError, core_schema

DURATION_PATTERN = r"^(?:[1-9][0-9]*m|[1-9][0-9]*h(?: [1-5]?[1-9]m| [1-5]0m)?)$"
TIME_PATTERN = r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$"
DATE_PATTERN = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"


class StringValue(str):
    pattern: ClassVar[str]

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source: type, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        return core_schema.no_info_after_validator_function(
            cls._validate,
            core_schema.str_schema(strict=True, pattern=cls.pattern),
        )

    @classmethod
    def _validate(cls, value: str):
        # A full match also rejects trailing newlines, unlike a regex '$' anchor.
        if re.fullmatch(cls.pattern, value) is None:
            raise PydanticCustomError("value_syntax", "Invalid value syntax")
        return cls(value)


class Duration(StringValue):
    pattern = DURATION_PATTERN

    @property
    def minutes(self) -> int:
        total = 0
        for component in self.split(" "):
            total += int(component[:-1]) * (60 if component.endswith("h") else 1)
        return total


class ClockTime(StringValue):
    pattern = TIME_PATTERN

    @property
    def minutes(self) -> int:
        hours, minutes = self.split(":")
        return int(hours) * 60 + int(minutes)


class CalendarDate(StringValue):
    pattern = DATE_PATTERN

    @classmethod
    def _validate(cls, value: str):
        result = super()._validate(value)
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise PydanticCustomError(
                "calendar_date", "Date must be a real calendar date"
            ) from exc
        return result
