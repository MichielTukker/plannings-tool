# plannings-tool

A Python course-planning tool. Phases 1 and 2 provide YAML validation, JSON
Schema export, and a sequential schedule preview with manually placed breaks.
Automatic break insertion and PDF generation are not implemented yet.

## Install and run

Requires Python 3.14+ and [uv](https://docs.astral.sh/uv/).

```powershell
uv sync
uv run plan validate testdata\Kleppen-cursus-2026.yaml
uv run plan schedule testdata\Kleppen-cursus-2026.yaml
uv run plan schema --output course.schema.json
uv run plan --help
```

The CLI uses Typer for commands and Rich for terminal formatting. In
diagnostics, the severity is red for errors, orange for warnings, and green for
info; the file location is cyan and the message uses the default terminal
color. Redirected output remains readable without color. Validation prints a
short confirmation on success. Diagnostics use
`[ERROR] - filename:source - message` or
`[WARNING] - filename:source - message` on stderr. The source is the YAML line
for syntax errors. Other diagnostics do not have line numbers yet, so they show
a 1-based location such as `day 1|entry 3|duration`, `trainer 2|email`, or
`course|timezone`; entries count sessions and breaks. `~` marks a file-level
problem without a location.

Exit codes are `0` for success, `1` for input/file errors, and `2` for invalid
command-line arguments. Expected failures do not print tracebacks. Schema
exports replace their destination atomically.

`plan schedule` previews sequential timings with Rich tables. Scheduling
warnings are printed on stderr and do not change the successful exit status;
`--strict` treats warnings as failures and suppresses the preview. Automatic
break scheduling is not implemented yet, so scheduling an automatic-mode day
reports an error.

Manual-mode days longer than four hours with no breaks produce a
`missing_manual_breaks` warning on stderr, using inherited or overridden day
times. Warnings do not reject the course or change the successful exit code.

## Course YAML

See [testdata/Kleppen-cursus-2026.yaml](testdata/Kleppen-cursus-2026.yaml) for a
complete two-day example.
A minimal course is:

```yaml
schema_version: 1
course:
  title: Hydraulics course
  start: "09:00"
  end: "17:00"
trainers:
  - id: alex
    name: Alex Example
    email: alex@example.org
days:
  - label: Day 1
    schedule:
      - title: Introduction
        type: Presentation
        duration: 30m
        trainers: [alex]
```

The original single-day input format is not supported.

### Fields and defaults

- `schema_version` must be integer `1`.
- `course` requires `title`, `start`, and `end`. Optional `description` and
  `project_id` must be nonblank strings when supplied.
  Optional `timezone` is an IANA timezone name and defaults to
  `Europe/Amsterdam`; it applies to all dated days in the course.
- YAML date and time values remain quoted strings, but the Python model parses
  dates and clock times to `datetime.date` and `datetime.time`.
- `trainers` is a nonempty list. Each trainer requires `id`, `name` (full display
  name), and `email`; `color` is an optional quoted `"#RRGGBB"` value. IDs match
  `[a-z][a-z0-9_-]*` and must be unique. Email syntax is checked offline, without
  checking deliverability; the input address is preserved.
- `defaults` is optional and may contain `coffee_break` (default `15m`), `lunch`
  (default `45m`), and `break_mode` (default `manual`).
- `days` is a nonempty ordered list. Each day requires a nonblank unique `label`
  and nonempty `schedule`. Optional `location` must be a nonblank string when
  supplied. Optional `start` and `end` independently override course times.
  Optional `break_mode` overrides the shared default.
- Day `date` is optional: use a quoted real ISO date such as `"2027-03-01"`.
  Supplied dates must be unique and strictly increasing, ignoring undated days.
- Unknown fields and explicit `null` values are rejected. Omit optional fields
  instead. Supplied text is not trimmed; whitespace-only text is invalid.

Use quoted `HH:MM` times from `"00:00"` to `"23:59"`. Course and effective day
end times must be later than start times; overnight days are not supported.
Quote dates as well: YAML-native date values are rejected.

### Sessions and breaks

Each session requires `title`, `type`, `duration`, and a nonempty `trainers` list.
Trainer references must exist and must not repeat within one session. Trainers
may teach multiple sessions; multiple trainers may share one session. Unused
trainers are allowed.

| Type | Duration | Minimum | Future break behavior |
|---|---|---:|---|
| `Presentation` | Fixed | 15 min | Splittable |
| `Exercise` | Fixed | 15 min | Splittable |
| `Excursion` | Fixed | 30 min | Not splittable |
| `Interactive` | `remaining` | Allocated later, minimum 15 min | Not splittable |

Types are case-sensitive and built into the tool. At most one Interactive
session is allowed per day, anywhere in the sequence. Its duration must be the
literal `remaining`; fixed types cannot use that value.

Durations accept positive integer minutes/hours: `15m`, `90m`, `1h`, or
`1h 15m`. Combined minutes must be 1-59 with exactly one separating space.
Numbers, fractions, negatives, zero, leading zeros, uppercase units, surrounding
whitespace, `1h15m`, and `1h 0m` are rejected.

In manual mode, insert breaks in sequence:

```yaml
- break: coffee
- break: lunch
  duration: 30m
```

Only coffee and lunch are supported. Omitted durations use the corresponding
default; overrides need only be positive. Breaks have no trainers. Repeated
breaks, breaks at the start/end, and all-break days are valid.

`automatic` break mode is accepted as input but rejects all manual break
entries. Preferred break times/windows will be fixed in code in phase 3.
The phase-2 scheduler does not place automatic breaks. It also reports overflow
and unused time as warnings instead of changing fixed durations.

Dated days resolve local wall-clock times in the course timezone. Nonexistent
and ambiguous daylight-saving times, and days crossing an offset transition,
are rejected. Undated days remain floating local-clock schedules without
timezone-aware instants. Activity and break lengths are elapsed minutes.

YAML must contain exactly one mapping document. Duplicate keys, including
collisions introduced by merge mappings, and unsafe object tags are rejected.

## Editor schema

`course.schema.json` is generated from the Pydantic models. For YAML Language
Server-compatible editors, add a directive with a path relative to your YAML:

```yaml
# yaml-language-server: $schema=../course.schema.json
```

Regenerate after model changes:

```powershell
uv run plan schema --output course.schema.json
```

The schema describes structure, scalar syntax, and session variants. Python
validation additionally checks duration minimums, calendar validity, trainer
references, uniqueness, date/time ordering, the Interactive limit, and effective
break mode. Editor email-format checks depend on editor support. Always run
`plan validate`; editor validation alone is not sufficient.

## Python API

```python
from plannings_tool import (
    CourseValidationError,
    generate_schema,
    load_course,
    parse_course,
    schedule_course,
)

course = load_course("testdata\\Kleppen-cursus-2026.yaml")
day = course.days[0]
start = day.effective_start(course.course)
start_minutes = start.hour * 60 + start.minute
schema = generate_schema()
schedule = schedule_course(course)
```

`parse_course(text)` validates YAML without file I/O. Both loading functions
raise `CourseValidationError` with an `issues` list containing codes, locations,
messages, and optional source line/column information.

Use `collect_validation_warnings(course)` to obtain advisory issues separately
after loading or parsing. The loading functions do not emit warnings themselves.

## Development

```powershell
uv sync --dev
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run ty check
```

`schedule_course` returns a `ScheduleResult` with ordered day/entry data and
structured warnings; it raises `SchedulingError` for unsupported automatic
breaks and timezone resolution failures.

Tests check that the committed schema matches model generation. See
[IMPLEMENTATION-PLAN.md](IMPLEMENTATION-PLAN.md) and
[PHASE-1-PLAN.md](PHASE-1-PLAN.md) and
[PHASE-2-PLAN.md](PHASE-2-PLAN.md) for scope and acceptance criteria.
