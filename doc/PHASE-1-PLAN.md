# Phase 1: Input contract, schema, and validation

## Outcome

Deliver a reusable Python API and CLI that load a course YAML file, validate it,
and report actionable errors. Publish a generated JSON Schema for editors.

Phase 1 does not calculate schedules, allocate flexible time, insert breaks,
assign trainer colors, or generate PDFs. It does not add a web frontend or CI.
Automatic break mode is valid input, but scheduling it belongs to phase 3.

Preserve Python >=3.14 and the existing uv build setup.

This phase intentionally keeps the current schema contract, which has no
timezone field and represents dates/times with validated string subclasses.
The later scheduling milestone will add optional `course.timezone` (default
`Europe/Amsterdam`) without changing the schema version, and will parse date and
clock strings into typed runtime values. See the timezone rules in
[IMPLEMENTATION-PLAN.md](IMPLEMENTATION-PLAN.md).

## Agreed YAML contract

Replace the original single-day input format; do not implement legacy support.
Unknown fields are errors at every level.

### Top-level fields

| Field | Required | Rules |
|---|---|---|
| `schema_version` | Yes | Integer `1`; reject booleans and strings |
| `course` | Yes | Course metadata and default day times |
| `trainers` | Yes | Nonempty list of trainer definitions |
| `defaults` | No | Omitted fields use the defaults below |
| `days` | Yes | Nonempty ordered list of explicitly grouped days |

### Course

| Field | Required | Rules |
|---|---|---|
| `title` | Yes | Nonblank string |
| `start` | Yes | Quoted `HH:MM` |
| `end` | Yes | Quoted `HH:MM`; later than `start` |
| `description` | No | Nonblank string when supplied |
| `project_id` | No | Nonblank string when supplied |

Optional fields may be absent, but explicit `null` is invalid. Do not silently
trim or otherwise repair input. Whitespace-only strings are invalid.

### Trainers

Each trainer has:

- Required `id`: matches `^[a-z][a-z0-9_-]*$`, unique across the course.
- Required `name`: nonblank full display name, stored in one field.
- Required `email`: syntactically valid email address, with no DNS/network checks.
- Optional `color`: a quoted `#RRGGBB` hexadecimal color, case-insensitive.

Different trainer IDs may have the same name or email. Unused trainers are valid.
A trainer can present multiple sessions, including multiple sessions on one day.
Do not include real personal contact details in committed examples or fixtures.

### Defaults

| Field | Built-in default | Rules |
|---|---|---|
| `coffee_break` | `15m` | Positive duration |
| `lunch` | `45m` | Positive duration |
| `break_mode` | `manual` | Exactly `manual` or `automatic` |

The entire `defaults` mapping is optional. These durations will also apply to
automatic breaks in phase 3. Preferred break times and shift windows remain
fixed in code; phase 1 exposes no YAML settings for them.

### Days

Each day has:

- Required nonblank `label`, unique across the course, compared case-sensitively.
- Optional nonblank `location`, specific to that day.
- Optional quoted `date` in ISO `YYYY-MM-DD` form, validated as a real date.
- Optional quoted `start` and `end`, independently inheriting course times.
- Optional `break_mode`, inheriting `defaults.break_mode`.
- Required nonempty `schedule`, containing session and/or break entries.

Require effective start < effective end. Overnight days and `24:00` are invalid.
Supplied dates must be unique and strictly increasing in input order; ignore
undated days for this comparison.

An all-break day is valid. Do not impose a session-count limit or reject a day
because activity durations exceed its available time; fit diagnostics belong
to phase 2.

### Sessions

All sessions require nonblank `title`, case-sensitive `type`, `duration`, and a
nonempty `trainers` list of valid trainer IDs.

| Type | Accepted duration | Minimum | Future break behavior |
|---|---|---:|---|
| `Presentation` | Fixed duration | 15 min | Splittable |
| `Exercise` | Fixed duration | 15 min | Splittable |
| `Excursion` | Fixed duration | 30 min | Not splittable |
| `Interactive` | Literal `remaining` | Allocated later, minimum 15 min | Not splittable |

At most one Interactive session is allowed per day; it may appear anywhere.
Fixed types cannot use `remaining`, and Interactive cannot use a fixed duration.

Reject duplicate trainer references within a session, but allow reuse across
sessions. Phase 1 needs no explicit session ID: identify entries using their day
and original schedule index.

Keep the built-in type rules centralized. New types require code changes, not
user-defined type declarations.

### Manual breaks

Accept only:

```yaml
- break: coffee
- break: lunch
  duration: 30m
```

`duration` is optional and overrides the corresponding default. Breaks require
no trainers, title, or type. Their only minimum is a positive whole-minute
duration. Permit repeated breaks and breaks at the start or end of a day.

Reject all manual break entries when the day's effective mode is `automatic`.
Reject entries that mix session and break fields.

### Scalar syntax and YAML loading

- Accept duration strings such as `15m`, `90m`, `1h`, `2h`, and `1h 15m`.
- Hours and standalone minutes are positive integers without leading zeros.
- Combined minutes are 1-59, separated from hours by exactly one space.
- Reject bare numbers, fractions, negatives, zero, uppercase units, surrounding
  whitespace, `1h15m`, and `1h 0m`.
- Require exactly two digits each for hours/minutes in times, from `00:00` through
  `23:59`.
- Require date/time values to load as strings; reject YAML-native date objects.
  Document quoting as the supported convention. Ordinary scalar quotes are not
  themselves retained by the loader and need not be inspected.
- Reject duplicate mapping keys, including duplicates in nested mappings.
- Accept exactly one YAML document with a mapping at its root.
- Use a safe loader; do not enable arbitrary object construction.

Share duration syntax and conversion logic so schema generation and Python
validation do not develop separate grammars.

## Representative input

Keep a representative course YAML under `testdata` that demonstrates the
complete contract, including an editor schema directive pointing to the
generated schema. Use fictional trainers in committed example fixtures.

```yaml
schema_version: 1
course:
  title: Hydraulics course
  description: Practical introduction to hydraulic systems.
  project_id: DEMO-001
  start: "09:00"
  end: "17:00"

trainers:
  - id: alex
    name: Alex Example
    email: alex@example.org
    color: "#336699"
  - id: sam
    name: Sam Example
    email: sam@example.org

defaults:
  coffee_break: 15m
  lunch: 45m
  break_mode: manual

days:
  - label: Day 1
    date: "2027-03-01"
    location: Training room A
    schedule:
      - title: Introduction
        type: Presentation
        duration: 30m
        trainers: [alex]
      - break: coffee
      - title: Practical exercise
        type: Exercise
        duration: 1h 15m
        trainers: [alex, sam]
      - break: lunch
        duration: 30m
      - title: Discussion
        type: Interactive
        duration: remaining
        trainers: [sam]
  - label: Day 2
    date: "2027-03-02"
    location: Training room B
    start: "09:30"
    end: "16:00"
    break_mode: automatic
    schedule:
      - title: Advanced hydraulics
        type: Presentation
        duration: 2h
        trainers: [alex]
      - title: Workshop
        type: Exercise
        duration: 2h
        trainers: [sam]
      - title: Questions and close
        type: Interactive
        duration: remaining
        trainers: [alex, sam]
```

This example demonstrates validation, not a phase-1 scheduling guarantee.

## Architecture and implementation sequence

### Step 1: Dependencies and package structure

Add runtime dependencies on Pydantic v2, PyYAML, email-validator, Typer, and
Rich. Add pytest and Ruff as development dependencies using uv. Preserve the
existing build backend.

Use a small package structure:

| Module | Responsibility |
|---|---|
| `models.py` | Input models, built-in types, field and cross-field validation |
| `values.py` | Shared duration/time/date syntax and conversion helpers |
| `diagnostics.py` | Shared `Diagnostic`/`Severity` types, location formatting, and the `PlanningError` hierarchy |
| `loader.py` | Safe YAML loading, duplicate keys, and file loading |
| `validation.py` | Semantic course checks and advisory warnings |
| `schema.py` | Deterministic JSON Schema generation/export |
| `cli.py` | Typer commands and Rich user-facing output |
| `__init__.py` | Small public API exports and existing command entry-point compatibility |

Use Typer for command arguments and Rich for help, success, warning, and error
output. Rich should automatically disable ANSI colors when output is redirected.

### Step 2: Value types and input models

Implement strict field validation without coercing numbers to strings, booleans
to integers, or strings to schema-version numbers.

Use separate session variants and a separate break model so generated schema
captures allowed combinations of fields. Prefer Pydantic's native facilities;
do not maintain a second handwritten schema.

Store durations and times in typed value representations exposing integer-minute
conversion for phase 2. Keep schema-facing input types as strings. Resolve
inheritance through explicit helpers/properties without calculating a timetable
or losing which values were supplied.

Enforce local constraints first, then course-wide references and uniqueness.
Errors should retain field paths rather than only returning a root-level message.

### Step 3: Loader and reusable validation API

Provide:

- `load_course(path) -> CourseDefinition`: read UTF-8 YAML and validate it.
- `parse_course(text) -> CourseDefinition`: validate YAML text without file I/O.
- `generate_schema() -> dict`: obtain the editor schema.

Use a domain validation exception (`CourseValidationError`, a `PlanningError`)
whose `diagnostics` tuple holds `Diagnostic` values with stable codes,
field locations, and readable messages. Include line/column information for YAML
syntax and duplicate-key failures. For model errors, include day/schedule indexes
and field paths; exact source-line mapping is not required in phase 1.

Catch only expected input/file failures at the CLI boundary. Do not hide
programming errors or emit a success-shaped fallback.

### Step 4: Schema generation

Commit `course.schema.json` at the repository root, generated from the models.
Use deterministic formatting and a documented regeneration command.

The schema covers structure, required/unknown fields, scalar syntax, session
variants, and local minimum durations wherever practical. Runtime validation
additionally checks references, uniqueness, calendar validity, effective times,
chronological dates, flexible-session count, and effective break mode.

Document these limits: passing editor validation is not a substitute for
`plan validate`. Do not add a separate JSON Schema runtime-validation pipeline.

### Step 5: CLI

```text
plan validate testdata\Kleppen-cursus-2026.yaml
plan schema --output course.schema.json
```

`validate` prints a short green confirmation on stdout only on success. On
failure, print concise red errors on stderr without a traceback for expected
failures. Print yellow advisory warnings to stderr; warnings do not change the
successful exit status.

`schema` requires an output path and writes UTF-8 JSON. Report write failures
explicitly and avoid leaving partial output.

Exit codes:

- `0`: successful validation or schema export.
- `1`: input validation, YAML, file-read, or file-write failure.
- `2`: invalid command-line arguments, following Typer's standard usage-error
  behavior.

Keep `--help` useful at the root and subcommand levels. Do not expose render or
scheduling commands before they are implemented. The future PDF interface is
`plan render course.yaml --output schedule.pdf`, replacing the earlier flat CLI
proposal.

### Step 6: Tests, schema artifact, and documentation

Use focused pytest tests in existing/new test modules grouped by responsibility.
Use parametrization rather than separate fixture files for every invalid value.

Cover the following acceptance cases:

| Area | Cases |
|---|---|
| Valid input | Two-day example; omitted defaults; inherited and overridden times; optional metadata absent; different per-day locations |
| Scalar syntax | Accepted duration forms; representative invalid forms; invalid dates/times; strict version typing |
| YAML | Malformed input; nested duplicate keys; multiple documents; empty/non-mapping root; unsafe tags |
| Fields | Unknown keys; null values; whitespace-only required/supplied optional strings |
| Trainers | Required name/email; invalid email/color/ID; duplicate definitions; unknown references |
| Assignments | Multiple trainers per session; reuse across sessions succeeds; duplicate reference within a session fails |
| Sessions | Type minimum boundaries; wrong duration variant; unknown type; multiple Interactive entries |
| Days | Empty days/schedules; duplicate labels; date order/duplicates with undated days; invalid inherited time range |
| Breaks | Defaults and overrides; repeated/edge breaks; all-break day; effective automatic mode rejects manual entries |
| Phase boundary | Overlong fixed sessions and unused time do not invalidate otherwise valid input |
| CLI | Success/failure streams and exit codes; missing files; schema export and write errors |
| Schema | Generated schema matches the committed artifact; generated fields reflect the string input contract |

Do not validate email deliverability or access the network in tests.

Update README with installation, YAML structure, quoting requirements, validation,
schema regeneration, schema/runtime limitations, and the phase-1 scope.

Run the targeted/full phase-1 pytest suite, Ruff lint, and Ruff format check.
Exercise the installed `plan` entry point against the example and regenerate
the schema once to confirm there is no artifact drift.

## Definition of done

1. The updated example validates through both the reusable API and CLI.
2. The committed schema is reproducibly generated from the models.
3. Invalid input produces useful field/day/session diagnostics and a nonzero exit.
4. Trainer reuse across sessions works; all agreed cross-field rules are enforced.
5. Tests and existing/new lint/format checks pass.
6. Documentation accurately distinguishes validation from future scheduling.
7. No scheduling or rendering behavior is implemented as part of this phase.
