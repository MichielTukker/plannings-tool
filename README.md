# plannings-tool

A Python course-planning tool. **Phase 1 provides YAML validation and JSON Schema
export only.** Timetable calculation, automatic break insertion, PDFs, and other
outputs are not implemented yet.

## Install and run

Requires Python 3.14+ and [uv](https://docs.astral.sh/uv/).

```powershell
uv sync
uv run plan validate testdata\course.yaml
uv run plan schema --output course.schema.json
uv run plan --help
```

Validation prints a short confirmation on success. Invalid files produce
field-path diagnostics on stderr, for example
`days[0].schedule[0].Presentation.duration`. Indexes are zero-based. YAML syntax
and duplicate-key errors also include line/column information.

Exit codes are `0` for success, `1` for input/file errors, and `2` for invalid
command-line arguments. Expected failures do not print tracebacks. Schema
exports replace their destination atomically.

Manual-mode days longer than four hours with no breaks produce a
`missing_manual_breaks` warning on stderr, using inherited or overridden day
times. Warnings do not reject the course or change the successful exit code.

## Course YAML

See [testdata/course.yaml](testdata/course.yaml) for a complete two-day example.
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
**Validation does not run scheduling:** it neither places automatic breaks nor
rejects overflow or unused time.

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
)

course = load_course("testdata\\course.yaml")
day = course.days[0]
start_minutes = day.effective_start(course.course).minutes
schema = generate_schema()
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
```

Tests check that the committed schema matches model generation. See
[IMPLEMENTATION-PLAN.md](IMPLEMENTATION-PLAN.md) and
[PHASE-1-PLAN.md](PHASE-1-PLAN.md) for scope and acceptance criteria.
