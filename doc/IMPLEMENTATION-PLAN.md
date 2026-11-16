# Course planning tool implementation plan

## Goal and scope

Build a Python tool that reads a schema-validated YAML course definition and
generates a PDF schedule. Version 1 supports multi-day courses with explicit day
groupings, sequential sessions, and a single track.

Provide a local CLI backed by reusable Python functions. Keep input parsing,
scheduling, and rendering separate so future email templates, action lists,
requirement lists, and a web frontend can consume the same resolved schedule.

The repository currently contains a Python 3.14 package skeleton and a `plan`
command.

## 1. Define the input contract

See [PHASE-1-PLAN.md](PHASE-1-PLAN.md) for the agreed YAML contract,
implementation sequence, and acceptance criteria.

Use Pydantic models as the source of truth and generate a JSON Schema for YAML
editor validation and completion. Load YAML with PyYAML, then validate it. Reject
unknown fields and report errors with the affected day and session.

### Input structure

- **Course:** title, description, location, project ID, default start/end times,
  optional IANA timezone.
- **Trainers:** stable IDs, display names, and optional explicit colors.
- **Days:** label, optional date, optional start/end overrides, break mode, and
  ordered sessions.
- **Sessions:** title, built-in type, duration, and one or more trainer IDs.

Day labels are sufficient; calendar dates are optional. Days inherit course
start/end times unless overridden. Overnight schedules are out of scope.

Add optional `course.timezone`, inherited by every day and defaulting to
`Europe/Amsterdam`. Do not add a per-day override. This is backwards-compatible
with the current schema version and files omitting the field. Validate timezone
names at runtime using Python `zoneinfo`; JSON Schema can validate that the
value is a string but cannot reliably enumerate installed IANA timezone names.

Keep dates and wall-clock times as quoted ISO strings in YAML and in the editor
schema. Parse them into typed `datetime.date` and `datetime.time` values in the
Python input model instead of keeping `StringValue` subclasses as the runtime
representation. Durations remain elapsed-minute values. Resolve a dated day's
local start/end using `zoneinfo.ZoneInfo(course.timezone)` into timezone-aware
datetimes at the scheduling boundary. Undated days remain floating local-clock
schedules: they can be validated and scheduled using wall-clock values, but
cannot be resolved to absolute instants or checked against that date's DST
rules.

Use `duration: remaining` rather than the example's unquoted `*`, which is invalid
YAML. Accept whole-minute durations such as `15m`, `1h`, and `1h 15m`.

### Built-in session rules

| Type | Duration | Minimum | Splittable by a break |
|---|---|---:|---|
| Presentation | Fixed | 15 min | Yes |
| Exercise | Fixed | 15 min | Yes |
| Excursion | Fixed | 30 min | No |
| Interactive | Remaining time | 15 min | No |

Allow at most one Interactive session per day, anywhere in the input sequence.
Fixed durations below their type minimum are validation errors.

New session types are built into the tool rather than defined in YAML. Centralize
type-specific rules and add specialized parsing when a type needs additional
fields.

Every session requires at least one valid trainer ID. Breaks do not require
trainers.

## 2. Implement the scheduling engine

Represent durations and same-day offsets as elapsed integer minutes or
`timedelta` values. Keep the engine independent of CLI, YAML, and PDF libraries.
For dated schedules, anchor local date/time values in the course timezone and
use aware datetimes for instant-based comparisons. For undated schedules, retain
floating wall-clock values and do not claim they are absolute instants.

Session durations represent activity time, excluding breaks. Preserve input order
and original session identity when a session is split into multiple fragments.

### Timezone and daylight-saving rules

Use the IANA timezone identifier from `course.timezone`, defaulting to
`Europe/Amsterdam`. Add the `tzdata` runtime dependency so IANA data is
available consistently on Windows as well as platforms with a system timezone
database. Do not infer the zone from the free-form course location or from the
machine's local timezone.

For dated days:

- Interpret configured start/end clock times in the course timezone.
- Reject nonexistent local times in the spring-forward gap and ambiguous local
  times in the fall-back fold with clear validation/scheduling errors. Never
  guess an occurrence.
- Reject a course day whose local interval crosses a timezone offset transition.
  Do not silently skip or repeat a wall-clock hour.
- Treat activity and break durations as elapsed minutes.

For undated days, preserve floating wall-clock scheduling; DST validation and
conversion to absolute instants require a date. Do not add UTC columns to output.
Display scheduled times in course-local time and show the IANA timezone
identifier in output metadata.

### Manual breaks

Place explicitly supplied breaks in input order. Allow individual durations to
override break defaults.

### Automatic breaks

Automatic mode rejects manual break entries. Use this shared daily policy:

| Break | Preferred start | Duration | Allowed start-time shift |
|---|---|---:|---:|
| Morning coffee | 10:30 | 15 min | 30 min earlier or later |
| Lunch | 12:15 | 45 min | 15 min earlier or later |
| Afternoon coffee | 15:00 | 15 min | 30 min earlier or later |

Placement rules:

1. Prefer the nearest valid session boundary within the allowed window, choosing
   the earlier boundary on a tie.
2. Otherwise split an eligible session at the preferred time. Each fragment must
   be at least 15 minutes; do not create empty fragments.
3. If neither placement is possible, fail with a clear error explaining how to
   revise the schedule or switch the day to manual breaks.

Only consider breaks whose preferred start falls inside the configured day.
Omit breaks after activities have finished.

### Flexible allocation and fit

Allocate remaining time after accounting for fixed activities and applicable
breaks. If fewer than 15 minutes remain, allocate 15 minutes to the Interactive
session and warn about the resulting overflow.

Warn about overflow and unused time; do not silently resize fixed sessions or
move activities to another day.

Flexible duration and automatic-break placement affect each other. Resolve them
together and verify that the resulting schedule is consistent, rather than
calculating each once in isolation.

## 3. Produce a renderer-independent result

The resolved model contains:

- Course and day metadata.
- Timed session fragments and breaks.
- Trainer assignments.
- Structured diagnostics.

Distinguish errors from warnings:

| Category | Examples | Normal behavior |
|---|---|---|
| Error | Invalid input, unknown trainer, impossible break placement | Fail without generating a PDF |
| Warning | Overflow, unused time, insufficient remaining time | Generate PDF with warnings |

This model is the interface for PDF rendering, future outputs, and the future web
frontend. Renderers must not reinterpret scheduling rules.

## 4. Implement the PDF

Use ReportLab, initially without an external charting library, to control page
layout and draw the timeline directly.

Each day occupies exactly one portrait A4 page containing:

- Course/day heading and relevant metadata.
- Course description on day 1 only.
- A Gantt chart with a horizontal time axis.
- A timetable showing the same activities, times, breaks, and trainers.
- A trainer legend and visible scheduling warnings.

Assign consistent trainer colors across days, using explicit colors when
provided. Sessions with multiple trainers use repeating diagonal bands in all
assigned trainer colors. Include trainer names in the table for grayscale
readability.

Display actual scheduled times, including overruns, in course-local time. Use
English labels, 24-hour times, ISO dates in YAML, and show the IANA timezone
identifier in output metadata.

Reserve space for a small Deltares logo when a local asset is supplied; omit the
logo otherwise. Do not download logo assets.

Define a minimum readable font size. Fail with a clear layout error if content
cannot fit on one page; do not silently clip content or add a second page.

## 5. Add the CLI

Initial interface:

```text
plan validate course.yaml
plan schema --output course.schema.json
plan render course.yaml --output schedule.pdf
plan render course.yaml --output schedule.pdf --strict
```

Validation and schema export are implemented in phase 1; rendering comes later.

Print warnings to stderr and include them in the PDF. In normal mode, warnings
do not change the successful exit status.

With `--strict`, scheduling warnings cause a nonzero exit without producing a
new PDF. Invalid YAML, schema errors, scheduling errors, and layout errors also
produce a nonzero exit.

Write PDFs atomically so a failed run does not leave a partial output.

## 6. Deliver in milestones

| Milestone | Deliverable |
|---|---|
| 1 | Models, generated JSON Schema, updated example YAML, helpful validation errors |
| 2 | Typed date/time runtime values, optional timezone with Amsterdam default, DST validation, manual scheduling, flexible allocation, structured diagnostics |
| 3 | Automatic breaks, session splitting, deterministic placement |
| 4 | PDF layout, trainer patterns, CLI integration |
| 5 | Documentation and end-to-end examples |

## Validation strategy

Use pytest for focused validation and scheduling cases, and Ruff for linting and
formatting.

Cover:

- Multi-day inheritance and per-day time overrides.
- Default and overridden IANA course timezones; invalid timezone names.
- Dated local-time resolution, DST gaps/folds, and days crossing an offset change.
- Undated floating schedules that are not converted into absolute instants.
- Timezone labels in output without a UTC column.
- Invalid durations, unknown types, and missing or unknown trainer IDs.
- Flexible sessions before and after fixed activities and breaks.
- Flexible minimum duration and overflow.
- Automatic-break boundary selection, tie-breaking, and allowed windows.
- Splitting and minimum fragment lengths.
- Unsplittable sessions spanning an entire break window.
- Rejection of manual breaks in automatic mode.
- Omission of automatic breaks outside the day or after activities finish.
- Warning behavior and strict-mode failures.
- Atomic output behavior on failure.

PDF checks cover page count, expected text, layout failures, and visual inspection
of representative schedules, including multi-trainer hatching.

## Main implementation risks

Automatic-break allocation and fitting both views legibly on portrait A4 are the
highest-risk parts. Prototype these early, before polishing the CLI or branding.
