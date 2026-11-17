"""Course input models; no timetable calculation takes place here."""

from datetime import time
from typing import Annotated, Any, ClassVar, Literal, LiteralString, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from email_validator import EmailNotValidError, validate_email
from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    GetJsonSchemaHandler,
    Tag,
    ValidationError,
    WithJsonSchema,
    WrapValidator,
    field_validator,
    model_validator,
)
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema, InitErrorDetails, PydanticCustomError
from pydantic_core.core_schema import ValidatorFunctionWrapHandler

from .values import CalendarDate, ClockTime, Duration, minutes_since_midnight


def _friendly_pattern_error(
    code: LiteralString, message: LiteralString
) -> WrapValidator:
    def validate(value: Any, handler: ValidatorFunctionWrapHandler) -> Any:
        try:
            return handler(value)
        except ValidationError as exc:
            if any(
                error["type"] == "string_pattern_mismatch" for error in exc.errors()
            ):
                raise PydanticCustomError(code, message) from exc
            raise

    return WrapValidator(validate)


NONBLANK_PATTERN = r"\S"
Nonblank = Annotated[
    str,
    Field(pattern=NONBLANK_PATTERN),
    _friendly_pattern_error(
        "nonblank_syntax", "Value must contain a non-whitespace character"
    ),
    WithJsonSchema({"type": "string", "pattern": NONBLANK_PATTERN}, mode="validation"),
]

TRAINER_ID_PATTERN = r"^[a-z][a-z0-9_-]*$"
TrainerId = Annotated[
    str,
    Field(pattern=TRAINER_ID_PATTERN),
    _friendly_pattern_error(
        "trainer_id_syntax",
        "Trainer ID must start with a lowercase letter and contain only lowercase "
        "letters, digits, '_' or '-'",
    ),
    WithJsonSchema(
        {"type": "string", "pattern": TRAINER_ID_PATTERN}, mode="validation"
    ),
]
COLOR_PATTERN = r"^#[0-9a-fA-F]{6}$"
Color = Annotated[
    str,
    Field(pattern=COLOR_PATTERN),
    _friendly_pattern_error(
        "color_syntax", "Color must be a six-digit hex value, such as #336699"
    ),
    WithJsonSchema({"type": "string", "pattern": COLOR_PATTERN}, mode="validation"),
]
BreakMode = Literal["manual", "automatic"]


def issue(
    code: LiteralString,
    message: LiteralString,
    location: tuple[str | int, ...],
    value: object,
    context: dict[str, object] | None = None,
) -> InitErrorDetails:
    return InitErrorDetails(
        type=PydanticCustomError(code, message, context), loc=location, input=value
    )


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, loc_by_alias=True)

    @model_validator(mode="before")
    @classmethod
    def reject_null_fields(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        errors: list[InitErrorDetails] = []
        for name, field in cls.model_fields.items():
            alias = field.alias
            input_name = alias if alias is not None and alias in value else name
            if input_name in value and value[input_name] is None:
                errors.append(
                    issue(
                        "null_not_allowed",
                        "Omit optional fields instead of using null",
                        (input_name,),
                        None,
                    )
                )
        if errors:
            raise ValidationError.from_exception_data(cls.__name__, errors)
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
    project_id: Nonblank | None = None
    timezone: Nonblank = "Europe/Amsterdam"

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise PydanticCustomError(
                "timezone_name",
                "Unknown IANA timezone: {timezone}",
                {"timezone": value},
            ) from exc
        return value

    @model_validator(mode="after")
    def ordered_times(self) -> Self:
        if self.start >= self.end:
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
            raise PydanticCustomError(
                "email_syntax",
                "Invalid email address: {reason}",
                {"reason": str(exc)},
            ) from exc
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
    duration: Duration
    minimum_minutes: ClassVar[int] = 0
    splittable: ClassVar[bool] = False

    @model_validator(mode="after")
    def check_minimum_duration(self) -> Self:
        if self.duration.minutes < self.minimum_minutes:
            raise ValidationError.from_exception_data(
                type(self).__name__,
                [
                    issue(
                        "duration_minimum",
                        "{session_type} requires at least {minimum} minutes",
                        ("duration",),
                        self.duration,
                        {
                            "session_type": type(self).__name__,
                            "minimum": self.minimum_minutes,
                        },
                    )
                ],
            )
        return self


class Presentation(FixedSession):
    type: Literal["Presentation"]
    minimum_minutes: ClassVar[int] = 15
    splittable: ClassVar[bool] = True


class Exercise(FixedSession):
    type: Literal["Exercise"]
    minimum_minutes: ClassVar[int] = 15
    splittable: ClassVar[bool] = True


class Excursion(FixedSession):
    type: Literal["Excursion"]
    minimum_minutes: ClassVar[int] = 30
    splittable: ClassVar[bool] = False


class Interactive(Session):
    type: Literal["Interactive"]
    duration: Literal["remaining"]
    minimum_minutes: ClassVar[int] = 15
    splittable: ClassVar[bool] = False
    flexible: ClassVar[bool] = True


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
        if "type" in value:
            return "session"
        return None
    if isinstance(value, Break):
        return "break"
    if isinstance(value, (Presentation, Exercise, Excursion, Interactive)):
        return "session"
    return None


SessionEntry = Annotated[
    Presentation | Exercise | Excursion | Interactive,
    Field(discriminator="type"),
]

ScheduleEntry = Annotated[
    Annotated[Break, Tag("break")] | Annotated[SessionEntry, Tag("session")],
    Discriminator(
        entry_kind,
        custom_error_type="schedule_entry_type",
        custom_error_message=(
            "Entry must be a break or a session with a recognized type"
        ),
    ),
]


class Day(InputModel):
    label: Nonblank
    date: CalendarDate | None = None
    location: Nonblank | None = None
    start: ClockTime | None = None
    end: ClockTime | None = None
    break_mode: BreakMode | None = None
    schedule: Annotated[list[ScheduleEntry], Field(min_length=1)]

    def effective_start(self, course: Course) -> time:
        return self.start if self.start is not None else course.start

    def effective_end(self, course: Course) -> time:
        return self.end if self.end is not None else course.end

    def available_minutes(self, course: Course) -> int:
        return minutes_since_midnight(
            self.effective_end(course)
        ) - minutes_since_midnight(self.effective_start(course))

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
            if day.effective_start(self.course) >= day.effective_end(self.course):
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
                                "Unknown trainer ID: {trainer_id}",
                                (*entry_path, "trainers", trainer_index),
                                trainer_id,
                                {"trainer_id": trainer_id},
                            )
                        )
        if errors:
            raise ValidationError.from_exception_data(type(self).__name__, errors)
        return self
