# Course planning tool implementation plan

## Goal and scope

Build a Python tool that reads a schema-validated YAML course definition and
generates a PDF schedule, a transparent Gantt-chart PNG per day, and a Markdown
timetable. Version 1 supports multi-day courses with explicit day groupings,
sequential sessions, and a single track.

Provide a local CLI backed by reusable Python functions. Keep input parsing,
scheduling, and rendering separate so future email templates, action lists,
requirement lists, and a web frontend can consume the same resolved schedule.

Phases 1-3 are implemented: validation, JSON Schema export, manual and
automatic-break scheduling, session splitting, and the `plan schedule`
preview. Phase 4 (rendering) is planned.

## 1. Define the input contract

See [PHASE-1-PLAN.md](PHASE-1-PLAN.md) for the agreed YAML contract,
implementation sequence, and acceptance criteria.
See [PHASE-2-PLAN.md](PHASE-2-PLAN.md) for the detailed manual-scheduling and
timezone implementation plan.
See [PHASE-3-PLAN.md](PHASE-3-PLAN.md) for automatic breaks and session
splitting, and [PHASE-4-PLAN.md](PHASE-4-PLAN.md) for PDF, PNG, and Markdown
rendering.

Use Pydantic models as the source of truth and generate a JSON Schema for YAML
editor validation and completion. Load YAML with PyYAML, then validate it. Reject
unknown fields and report errors with the affected day and session.

### Input structure

- **Course:** title, description, project ID, default start/end times,
  optional IANA timezone.
- **Trainers:** stable IDs, display names, and optional explicit colors.
- **Days:** label, optional date and location, optional start/end overrides,
  break mode, and ordered sessions.
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
representation. Durations are parsed into whole elapsed-minute integers. Resolve a dated day's
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
database. Do not infer the zone from the free-form day location or from the
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

Automatic mode rejects manual break entries. Use this shared daily policy (see
[PHASE-3-PLAN.md](PHASE-3-PLAN.md) for the full algorithm):

| Break | Preferred start | Duration | Allowed start-time shift |
|---|---|---:|---:|
| Morning coffee | 10:30 | `defaults.coffee_break` (15 min) | 30 min earlier or later |
| Lunch | 12:15 | `defaults.lunch` (45 min) | 15 min earlier or later |
| Afternoon coffee | 15:00 | `defaults.coffee_break` (15 min) | 30 min earlier or later |

Placement rules, applied to active breaks in chronological order within each
placement pass:

1. Prefer the nearest boundary between two sessions within the allowed window,
   choosing the earlier boundary on a tie. The day start and the end of the
   last session are not boundaries.
2. Otherwise split an eligible session at the preferred time. If a fragment
   would be shorter than 15 minutes, move the split 15 minutes later or
   earlier. Each fragment must be at least 15 minutes.
3. Otherwise permanently omit the break if provisional activities end at or
   before its preferred start (info diagnostic). Remove its reserved duration,
   reallocate Interactive, and restart placement from the original sessions.
   The omitted break is never retested, even if reallocation extends activities
   past its preferred start.
4. Otherwise fail with a clear error explaining how to revise the schedule or
   switch the day to manual breaks.

Only consider breaks whose preferred start falls strictly inside the
configured day. Report split sessions as info diagnostics.

### Flexible allocation and fit

Allocate remaining time after accounting for fixed activities and breaks.
Manual mode subtracts the supplied breaks. Automatic mode initially reserves
all applicable breaks, then releases reservations for permanently omitted
breaks. The final Interactive duration subtracts only actually placed breaks.
If fewer than 15 minutes remain, allocate 15 minutes and warn about the
resulting overflow.

Warn about overflow and unused time; do not silently resize fixed sessions or
move activities to another day.

Flexible duration and automatic-break placement affect each other. An
Interactive day can provisionally end before a preferred break start while
that break's duration is still reserved. Omission is decided on that timeline
and stays final; returning the reservation to Interactive must not reintroduce
the break. Rebuild all remaining placements and splits after reallocation,
because Interactive can occur anywhere in the input. Each restart removes one
active break, giving at most four passes for the three-rule policy. If a
remaining break cannot be placed, report an error rather than keep stale
timings. Calculate fit warnings only from the final allocation.

For example, a 09:00-15:05 day with two 90-minute Presentations followed by
Interactive initially reserves 75 break minutes and assigns 110 minutes to
Interactive. After morning coffee and lunch, activities end at 14:50, so
afternoon coffee is omitted. Reallocation assigns 125 minutes to Interactive,
which now ends at 15:05; afternoon coffee remains omitted, with an info
diagnostic explaining the provisional end. See the phase-3 worked examples.

## 3. Produce a renderer-independent result

The resolved model contains:

- Course and day metadata.
- Timed session fragments and breaks.
- Trainer assignments.
- Structured diagnostics.

Distinguish errors from warnings:

| Category | Examples | Normal behavior |
|---|---|---|
| Error | Invalid input, unknown trainer, impossible break placement, layout overflow | Fail without generating output |
| Warning | Overflow, unused time, insufficient remaining time | Generate outputs with warnings |
| Info | Session split by a break, automatic break omitted | Print only; never affects exit status |

This model is the interface for PDF, PNG, and Markdown rendering, future
outputs, and the future web frontend. Renderers must not reinterpret
scheduling rules.

## 4. Render the outputs

See [PHASE-4-PLAN.md](PHASE-4-PLAN.md) for the detailed rendering plan.

Draw the Gantt chart with matplotlib. Use one figure builder for both the
standalone PNG and the chart embedded in the PDF. Lay out PDF pages with
ReportLab.

The Gantt chart has one row per original session. The fragments of a split
session appear as separate bars on that row. Breaks are labelled shaded
vertical bands across all rows. A horizontal time axis shows course-local times.

**PDF:** each day occupies exactly one portrait A4 page containing:

- Course/day heading and relevant metadata.
- Course description on day 1 only.
- The Gantt chart, embedded as a 300 DPI image.
- A timetable showing the same activities, times, breaks, and trainers.
- A trainer legend and visible scheduling warnings.

**Gantt PNG:** one 1920 px wide PNG per day with a transparent background.
Each PNG has the time axis, session labels, trainer legend, heading, and logo.

**Markdown:** one file with a course heading and, per day, a metadata line, a
Start/End/Duration/Activity/Type/Trainers table, and the day's warnings.

Assign consistent trainer colors across days and outputs. Use explicit colors
when provided; otherwise use a colorblind-safe palette. Sessions with multiple
trainers use repeating diagonal bands in all assigned trainer colors. Include
trainer names in the tables for grayscale readability.

Display actual scheduled times, including overruns, in course-local time. Use
English labels, 24-hour times, ISO dates in YAML, and show the IANA timezone
identifier in output metadata.

Use the provided transparent PNG at `assets/Deltares_logo_D-blauw_RGB.png` as
the default logo; package a copy at
`src/plannings_tool/assets/deltares-logo.png`. Do not download or recreate it.
Show it in the PDF header and PNGs. `--logo PATH` overrides it; `--no-logo`
omits it.

Use fixed font sizes, none below 8 pt. Fail with a clear layout error if a day
cannot fit on one page; do not silently clip content or add a second page.

## 5. Add the CLI

Interface:

```text
plan validate course.yaml
plan schema --output course.schema.json
plan schedule course.yaml [--strict]
plan render course.yaml [--output-dir DIR] [--format pdf|png|md ...]
                        [--logo PATH | --no-logo] [--strict]
```

Validation and schema export are implemented in phase 1 and the schedule
preview in phase 2. Rendering comes in phase 4. `plan render` writes
`<stem>.pdf`, `<stem>-day-<n>.png`, and `<stem>.md` to the output directory
(default: current directory). It writes all formats by default.

Print warnings to stderr and include them in the PDF and Markdown output. In
normal mode, warnings do not change the successful exit status.

With `--strict`, scheduling warnings cause a nonzero exit without writing
output. Invalid YAML, schema errors, scheduling errors, and layout errors also
produce a nonzero exit.

Render all requested outputs in memory first; rendering failures write nothing.
Then replace each destination atomically, so individual files are never
partially written. This is not an all-or-nothing multi-file transaction:
on a write failure, report which files were already replaced and clean up
temporary files.

## 6. Deliver in milestones

| Milestone | Deliverable | Status |
|---|---|---|
| 1 | Models, generated JSON Schema, updated example YAML, helpful validation errors | Done |
| 2 | Typed date/time runtime values, optional timezone with Amsterdam default, DST validation, manual scheduling, flexible allocation, structured diagnostics | Done |
| 3 | Automatic breaks, session splitting, deterministic placement, info diagnostics | Done ([PHASE-3-PLAN.md](PHASE-3-PLAN.md)) |
| 4 | PDF, per-day transparent Gantt PNGs, Markdown timetable, trainer colors/stripes, bundled logo, `plan render` | Planned ([PHASE-4-PLAN.md](PHASE-4-PLAN.md)) |
| 5 | Documentation and end-to-end examples | Planned |

## Validation strategy

Use pytest for focused validation and scheduling cases, Ruff for linting and
formatting, and `ty check` for static type checking.

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
- Splitting, the ±15 minute split shift, and minimum fragment lengths.
- Unsplittable sessions spanning an entire break window.
- Rejection of manual breaks in automatic mode.
- Inapplicable breaks outside the day; permanent omission based on provisional
  activity end, including Interactive reallocation and rebuilt placements.
- Interactive subtracting only placed breaks, with no stale fit or split
  diagnostics after a restart.
- Warning behavior and strict-mode failures.
- Atomic output behavior on failure.

PDF checks cover page count, expected text, layout failures, and visual
inspection of representative schedules, including multi-trainer stripes. PNG
checks cover size, transparency, and one file per day. Markdown checks compare
exact output for the example courses.

## Main implementation risks

Automatic-break allocation and fitting both views legibly on portrait A4 are the
highest-risk parts. Prototype the Gantt chart early, before polishing the CLI
or branding.
