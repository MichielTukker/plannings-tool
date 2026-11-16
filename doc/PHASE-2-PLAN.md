# Phase 2: Manual scheduling, fit diagnostics, and timezone-aware dates

## Outcome and boundaries

Turn validated course input into a renderer-independent, sequential schedule.
Support fixed sessions, explicitly positioned manual breaks, and one flexible
Interactive session per day. Resolve course/day time inheritance, dated
timezone-aware schedules, and fit warnings.

Add a Rich-formatted text preview command for users and a reusable Python
scheduling API. Use Typer for command arguments and subcommands throughout the
CLI, including the phase-1 validation/schema commands.

Phase 2 does not insert automatic breaks, split sessions, move or resize fixed
sessions, render PDFs, or implement parallel tracks. A day configured for
automatic breaks is valid input but cannot be scheduled until phase 3; fail with
a clear `automatic_breaks_not_supported` scheduling error.

## Agreed behavior

### Input and time semantics

- Preserve the current YAML syntax and schema version.
- Keep ISO date and `HH:MM` clock strings in YAML and the JSON Schema, but parse
  them to typed `datetime.date` and `datetime.time` values at runtime.
- Add optional `course.timezone`, defaulting to `Europe/Amsterdam`. It is one
  IANA timezone for the whole course; there is no day-level override.
- Validate the timezone with `zoneinfo.ZoneInfo`. Add `tzdata` as a runtime
  dependency so timezone data is available on Windows as well as systems with
  an installed IANA database. Do not infer a timezone from course/day location
  or the host machine.
- Keep dates optional. A dated day resolves in the course timezone. An undated
  day is a floating wall-clock schedule and is not converted into absolute
  instants or checked against date-specific DST transitions.
- Durations are elapsed minutes. Overnight schedules remain invalid.

### Manual schedule construction

For each day, derive the local start and end from day overrides or course
defaults. For a dated day, resolve both in the course timezone. For an undated
day, retain the wall-clock values.

Walk entries in input order, beginning at the day's start:

- Fixed sessions consume their declared duration exactly.
- Manual coffee/lunch breaks consume their effective duration, using the
  configured default unless the entry has an override.
- Preserve every entry's order, type/title, trainers, and original schedule
  index. A phase-2 entry is not split into fragments.
- At most one Interactive session is already enforced by phase-1 validation.
  Its duration is the positive remainder after subtracting all fixed session
  and break durations from the day's available elapsed minutes, regardless of
  where the Interactive entry appears in the input.
- If that remainder is less than 15 minutes, including when negative, assign
  15 minutes and report the resulting overflow. Do not produce a zero or
  negative duration.
- If there is no Interactive session, keep all fixed durations unchanged and
  report overflow or unused time as appropriate.
- Never move an entry to another day or silently resize a fixed session.

The flexible session remains at its input position. Its assigned duration is
calculated for the whole day, independent of where Interactive appears; its
position still determines its start time and where subsequent entries begin.
An exact fit has no fit warning.

Explicit manual breaks remain exactly where listed. Repeated breaks and breaks
at the start/end remain valid and consume time. Any break in the day satisfies
the phase-1 missing-manual-break check. A manual-mode day with no breaks and
more than four hours of configured time retains the `missing_manual_breaks`
warning; four hours exactly does not meet the threshold.

### Fit and other diagnostics

Return structured diagnostics, not only formatted strings. Each diagnostic has
severity (`warning` or `error`), a stable code, a day/schedule location, and a
human-readable message. Include affected schedule indexes where relevant.

Phase-2 warning codes:

| Code | Condition |
|---|---|
| `missing_manual_breaks` | Manual-mode day exceeds four hours and contains no manual break |
| `schedule_overflow` | Sequential schedule ends after the configured day end |
| `unused_time` | Schedule ends before day end and there is no Interactive session |
| `interactive_minimum_overflow` | Less than 15 minutes remain for Interactive, so its minimum causes overflow |

When an Interactive session minimum generates overflow, use the specific
`interactive_minimum_overflow` warning instead of also emitting a redundant
generic overflow warning for the same excess. Still emit `unused_time` for
positive unallocated time when there is no flexible session.

Warnings do not prevent an otherwise schedulable result. Impossible conditions,
invalid date/timezone resolution, and automatic mode in this phase produce
explicit scheduling errors and no partial schedule. Input/schema problems
continue to be reported by phase-1 validation.

### Timezones and daylight saving

Use the course IANA timezone for all dated days.

- Resolve local start/end clock values to aware datetimes.
- Reject nonexistent spring-forward local times and ambiguous fall-back local
  times with explicit errors; do not choose a fold automatically.
- Reject a dated course-day interval that crosses any timezone offset
  transition. Do not skip or repeat a wall-clock interval.
- Use elapsed-minute/timedelta arithmetic once dated boundaries are resolved.
- For undated days, use wall-clock arithmetic only and leave absolute instants
  unavailable.
- Present local times with the IANA timezone identifier in the preview; do not
  show a UTC column.

Implement a focused local-time resolver that distinguishes normal, ambiguous,
and nonexistent local datetimes by testing both `fold` interpretations and
round-tripping through UTC. Do not treat assigning `tzinfo` alone as proof that
a local time exists.

## Public interfaces

### Python API

Provide a primary function such as:

```python
schedule_course(course: CourseDefinition) -> ScheduleResult
```

`ScheduleResult` contains ordered `ScheduledDay` values and structured warning
diagnostics. Each scheduled day retains the source day metadata, date, location,
timezone, resolved effective start/end, and ordered scheduled entries.

Each scheduled entry retains its original schedule index and source entry
identity, session/break data, trainer IDs, effective duration, and start/end.
Use elapsed offsets from day start as the canonical ordering/arithmetic values.
For dated schedules, also make aware start/end datetimes available. For undated
schedules, provide local wall-clock start/end values and do not fabricate
timezone-aware instants.

Use explicit typed models/dataclasses for scheduled results and diagnostics;
do not mutate `CourseDefinition`, add generated times back to YAML models, or
make the PDF/web renderers recalculate scheduling semantics.

Raise a dedicated `SchedulingError` carrying structured error diagnostics for
automatic mode, unresolvable dated times, and other impossible scheduling
conditions. Keep normal fit/missing-break conditions as warnings on the result.
Preserve the phase-1 `CourseValidationError` API for input errors.

### CLI

Continue using Typer (already used by phase-1 commands) for the root command and
every subcommand. Use Rich consoles/text for status messages, warnings, and
errors; allow Rich to disable terminal colors when output is redirected.

Add:

```text
plan schedule course.yaml
plan schedule course.yaml --strict
```

Print a readable, deterministic day-by-day preview to stdout using Rich tables,
consistent columns, styled day headings, and clear break/session distinctions.
Include date when present, day label, location, course timezone, local
start/end, durations, session titles/types, trainer display names, and break
labels. For undated days, identify times as floating local times rather than
absolute instants. Print warnings to stderr in yellow and errors in red, with
day and schedule locations.

Normal mode exits `0` and prints the preview when warnings exist. `--strict`
exits nonzero on any warning and prints no schedule preview. Input errors,
scheduling errors, and file errors exit nonzero without a traceback. Follow
Typer's standard usage-error exit behavior and use exit code `1` for expected
validation/scheduling failures.

Do not add JSON CLI output in phase 2; structured output is available through
the Python API.

## Implementation sequence

### 1. Typed temporal values and timezone dependency

- Add `tzdata` to runtime dependencies.
- Replace runtime `ClockTime`/`CalendarDate` string subclasses with strict
  validators that accept the existing YAML strings and return `datetime.time`
  and `datetime.date`.
- Keep generated JSON Schema string-based and deterministic, with existing
  patterns and behavior.
- Add and validate optional `Course.timezone`, default `Europe/Amsterdam`.
- Regenerate `course.schema.json` and update YAML examples and README.
- Verify phase-1 APIs and validation behavior remain compatible except that
  model date/time values are now typed objects.

### 2. Scheduling and diagnostic result models

- Add a scheduler module separate from models, YAML loading, and CLI rendering.
- Define immutable scheduled-day, scheduled-entry, diagnostic, result, and
  scheduling-error types.
- Reuse `SESSION_RULES`, defaults, effective day properties, and existing
  validation warnings.
- Define stable warning/error codes and path formatting once, not separately
  in the CLI.

### 3. Local time resolution

- Implement timezone lookup and clear unknown-zone errors.
- Implement local datetime classification (valid, nonexistent, ambiguous).
- Detect an offset transition inside each dated day's configured interval.
- Keep an explicit undated/floating code path with no aware datetimes.
- Add focused tests for timezone default/overrides, DST, and floating days.

### 4. Sequential day scheduler

- Reject effective automatic break mode before producing partial output.
- Calculate each day's available elapsed duration.
- Sum fixed-session and manual-break durations; determine Interactive remainder.
- Assign sequential starts/ends at each original input position.
- Calculate actual end, overflow, and unused-time diagnostics.
- Include the missing-manual-break warning from phase 1 exactly once.

### 5. CLI preview and documentation

- Extend the existing Typer app with the `schedule` command.
- Add the `schedule` subcommand and optional `--strict`.
- Use Rich for statuses, errors, warnings, and schedule preview tables.
- Format dated times in course-local time with the IANA timezone and undated
  times as floating local times.
- Add examples for `schedule`, strict mode, warnings, and structured API use.
- Clarify that automatic breaks and PDF rendering are later phases.

## Acceptance tests

Use pytest, Ruff, and `ty check`. Keep fixtures deterministic and avoid network
access.

| Area | Acceptance cases |
|---|---|
| Runtime types | YAML strings parse to `date`/`time`; schema remains string-based; omitted timezone defaults to Amsterdam; explicit valid IANA zone is honored; invalid zone errors |
| Manual scheduling | Entry order and original indexes preserved; start/end inheritance; per-day overrides; break default and duration override; repeated and edge breaks consume time |
| Flexible duration | Interactive before, between, and after fixed entries; remainder includes manual breaks; exact 15-minute minimum; under-minimum/negative remainder produces 15 minutes and one overflow warning |
| Fit diagnostics | Exact fit; fixed-only overflow; fixed-only unused time; no redundant generic overflow for Interactive minimum; manual missing-break warning inherited from phase 1 and threshold boundary |
| Timezones | Normal dated day; nonexistent Amsterdam spring-forward time; ambiguous Amsterdam fall-back time; interval crossing offset change; valid explicit timezone; undated floating day has no aware instants |
| Unsupported behavior | Effective automatic mode inherited from defaults and day override both fail with `automatic_breaks_not_supported`; no partial schedule |
| CLI | Preview content/order; warnings on stderr; normal warning exit is zero; strict warning exits nonzero and emits no preview; expected failures have no traceback |
| Regression | Existing phase-1 YAML validation, schema determinism/export, and course/day input rules continue to pass |

Run the complete pytest suite, `ruff check .`, `ruff format --check .`, and
`ty check`; regenerate the committed schema; validate the examples; and exercise
both normal and strict CLI preview commands.

## Definition of done

1. The reusable API produces deterministic sequential schedules for supported
   manual-mode days.
2. Flexible duration, break consumption, overflow, and unused-time behavior
   match the rules above.
3. Dated schedules use validated course-local aware datetimes; undated schedules
   remain explicitly floating; DST errors are clear and covered by tests.
4. Structured diagnostics are shared by API and CLI, with stable codes and
   locations.
5. The text preview and strict mode behave as specified.
6. Existing phase-1 functionality and the generated JSON Schema remain valid.
7. Automatic break placement, split fragments, and PDF rendering are not
   implemented in this phase.
