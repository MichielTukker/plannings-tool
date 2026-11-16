"""Course input models; no timetable calculation takes place here."""

import re
from dataclasses import dataclass
from typing import Annotated, Literal, Self

from email_validator import EmailNotValidError, validate_email
from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    GetJsonSchemaHandler,
    Tag,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema, InitErrorDetails, PydanticCustomError

from .values import CalendarDate, ClockTime, Duration

Nonblank = Annotated[str, Field(pattern=r"\S")]
TrainerId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]*$")]
Color = Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")]
BreakMode = Literal["manual", "automatic"]
SessionType = Literal["Presentation", "Exercise", "Excursion", "Interactive"]


@dataclass(frozen=True)
class SessionRule:
    minimum_minutes: int
    splittable: bool
    flexible: bool = False


SESSION_RULES: dict[SessionType, SessionRule] = {
    "Presentation": SessionRule(15, True),
    "Exercise": SessionRule(15, True),
    "Excursion": SessionRule(30, False),
    "Interactive": SessionRule(15, False, flexible=True),
}


def issue(
    code: str, message: str, location: tuple[str | int, ...], value: object
) -> InitErrorDetails:
    return InitErrorDetails(
        type=PydanticCustomError(code, message), loc=location, input=value
    )


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, loc_by_alias=True)

    @field_validator("*", mode="before")
    @classmethod
    def reject_null(cls, value: object) -> object:
        if value is None:
            raise PydanticCustomError(
                "null_not_allowed", "Omit optional fields instead of using null"
            )
        return value

    @classmethod
    def __get_pydantic_json_schema__(
        cls, schema: CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        result = handler.resolve_ref_schema(handler(schema))
        # Optional means omittable, never explicitly null, for all input fields.
        for prop in result.get("properties", {}).values():
            choices = prop.get("anyOf")
            if choices and any(choice == {"type": "null"} for choice in choices):
                allowed = [choice for choice in choices if choice != {"type": "null"}]
                prop.pop("anyOf")
                if len(allowed) == 1:
                    prop.update(allowed[0])
                else:
                    prop["anyOf"] = allowed
                prop.pop("default", None)
        return result


class Course(InputModel):
    title: Nonblank
    start: ClockTime
    end: ClockTime
    description: Nonblank | None = None
    location: Nonblank | None = None
    project_id: Nonblank | None = None

    @model_validator(mode="after")
    def ordered_times(self) -> Self:
        if self.start.minutes >= self.end.minutes:
            raise ValidationError.from_exception_data(
                type(self).__name__,
                [
                    issue(
                        "time_order", "End must be later than start", ("end",), self.end
                    )
                ],
            )
        return self


class Trainer(InputModel):
    id: TrainerId
    name: Nonblank
    email: Annotated[str, Field(json_schema_extra={"format": "email"})]
    color: Color | None = None

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str) -> str:
        try:
            validate_email(value, check_deliverability=False)
        except EmailNotValidError as exc:
            raise PydanticCustomError("email_syntax", str(exc)) from exc
        return value

    @field_validator("id", "color")
    @classmethod
    def exact_pattern(cls, value: str) -> str:
        pattern = r"#[0-9a-fA-F]{6}" if value.startswith("#") else r"[a-z][a-z0-9_-]*"
        if re.fullmatch(pattern, value) is None:
            raise PydanticCustomError("value_syntax", "Invalid value syntax")
        return value


class Defaults(InputModel):
    coffee_break: Duration = Duration("15m")
    lunch: Duration = Duration("45m")
    break_mode: BreakMode = "manual"


class Session(InputModel):
    title: Nonblank
    trainers: Annotated[list[TrainerId], Field(min_length=1)]

    @field_validator("trainers")
    @classmethod
    def unique_trainers(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise PydanticCustomError(
                "duplicate_trainer_reference",
                "A trainer may appear only once within a session",
            )
        return value


class FixedSession(Session):
    type: SessionType
    duration: Duration

    @model_validator(mode="after")
    def minimum_duration(self) -> Self:
        minimum = SESSION_RULES[self.type].minimum_minutes
        if self.duration.minutes < minimum:
            raise ValidationError.from_exception_data(
                type(self).__name__,
                [
                    issue(
                        "duration_minimum",
                        f"{self.type} requires at least {minimum} minutes",
                        ("duration",),
                        self.duration,
                    )
                ],
            )
        return self


class Presentation(FixedSession):
    type: Literal["Presentation"]


class Exercise(FixedSession):
    type: Literal["Exercise"]


class Excursion(FixedSession):
    type: Literal["Excursion"]


class Interactive(Session):
    type: Literal["Interactive"]
    duration: Literal["remaining"]


class Break(InputModel):
    kind: Literal["coffee", "lunch"] = Field(alias="break")
    duration: Duration | None = None

    def effective_duration(self, defaults: Defaults) -> Duration:
        if self.duration is not None:
            return self.duration
        return defaults.coffee_break if self.kind == "coffee" else defaults.lunch


def entry_kind(value: object) -> str | None:
    if isinstance(value, dict):
        if "break" in value:
            return "break"
        return value.get("type")
    if isinstance(value, Break):
        return "break"
    return getattr(value, "type", None)


ScheduleEntry = Annotated[
    Annotated[Break, Tag("break")]
    | Annotated[Presentation, Tag("Presentation")]
    | Annotated[Exercise, Tag("Exercise")]
    | Annotated[Excursion, Tag("Excursion")]
    | Annotated[Interactive, Tag("Interactive")],
    Discriminator(entry_kind),
]


class Day(InputModel):
    label: Nonblank
    date: CalendarDate | None = None
    start: ClockTime | None = None
    end: ClockTime | None = None
    break_mode: BreakMode | None = None
    schedule: Annotated[list[ScheduleEntry], Field(min_length=1)]

    def effective_start(self, course: Course) -> ClockTime:
        return self.start if self.start is not None else course.start

    def effective_end(self, course: Course) -> ClockTime:
        return self.end if self.end is not None else course.end

    def effective_break_mode(self, defaults: Defaults) -> BreakMode:
        return self.break_mode if self.break_mode is not None else defaults.break_mode


class CourseDefinition(InputModel):
    schema_version: Literal[1]
    course: Course
    trainers: Annotated[list[Trainer], Field(min_length=1)]
    defaults: Defaults = Field(default_factory=Defaults)
    days: Annotated[list[Day], Field(min_length=1)]

    @field_validator("schema_version", mode="before")
    @classmethod
    def strict_version(cls, value: object) -> object:
        if type(value) is not int:
            raise PydanticCustomError(
                "schema_version", "schema_version must be integer 1"
            )
        return value

    @model_validator(mode="after")
    def validate_relationships(self) -> Self:
        errors: list[InitErrorDetails] = []
        trainer_ids: set[str] = set()
        for index, trainer in enumerate(self.trainers):
            if trainer.id in trainer_ids:
                errors.append(
                    issue(
                        "duplicate_trainer_id",
                        "Trainer IDs must be unique",
                        ("trainers", index, "id"),
                        trainer.id,
                    )
                )
            trainer_ids.add(trainer.id)

        labels: set[str] = set()
        previous_date: CalendarDate | None = None
        for day_index, day in enumerate(self.days):
            path = ("days", day_index)
            if day.label in labels:
                errors.append(
                    issue(
                        "duplicate_day_label",
                        "Day labels must be unique",
                        (*path, "label"),
                        day.label,
                    )
                )
            labels.add(day.label)
            if day.date is not None:
                if previous_date is not None and day.date <= previous_date:
                    errors.append(
                        issue(
                            "date_order",
                            "Supplied dates must be unique and strictly increasing",
                            (*path, "date"),
                            day.date,
                        )
                    )
                previous_date = day.date
            if (
                day.effective_start(self.course).minutes
                >= day.effective_end(self.course).minutes
            ):
                errors.append(
                    issue(
                        "time_order",
                        "Effective end must be later than effective start",
                        (*path, "end"),
                        day.end,
                    )
                )
            interactive_seen = False
            for entry_index, entry in enumerate(day.schedule):
                entry_path = (*path, "schedule", entry_index)
                if isinstance(entry, Break):
                    if day.effective_break_mode(self.defaults) == "automatic":
                        errors.append(
                            issue(
                                "manual_break_in_automatic",
                                "Manual breaks are not allowed in automatic mode",
                                (*entry_path, "break"),
                                entry.kind,
                            )
                        )
                    continue
                if isinstance(entry, Interactive):
                    if interactive_seen:
                        errors.append(
                            issue(
                                "multiple_interactive",
                                "At most one Interactive session is allowed per day",
                                (*entry_path, "type"),
                                entry.type,
                            )
                        )
                    interactive_seen = True
                for trainer_index, trainer_id in enumerate(entry.trainers):
                    if trainer_id not in trainer_ids:
                        errors.append(
                            issue(
                                "unknown_trainer",
                                f"Unknown trainer ID: {trainer_id}",
                                (*entry_path, "trainers", trainer_index),
                                trainer_id,
                            )
                        )
        if errors:
            raise ValidationError.from_exception_data(type(self).__name__, errors)
        return self
