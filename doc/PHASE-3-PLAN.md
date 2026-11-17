# Phase 3: Automatic breaks and session splitting

**Status: Implemented.**

## Outcome and boundaries

Schedule days whose effective break mode is `automatic`. Insert coffee and
lunch breaks from a fixed policy and place them at session boundaries where
possible. Split eligible sessions when no boundary is close enough, and fail
with an actionable error when neither works. Manual-mode days keep their
phase-2 behavior.

Extend the renderer-independent `ScheduleResult` with automatically inserted
breaks and session fragments. Phase 4 renders this result and must not
recalculate any of these rules.

Phase 3 does not render PDF, PNG, or Markdown output. It does not make the
break policy configurable in YAML. It does not reorder, move, or resize fixed
sessions, or move sessions to another day. Parallel tracks remain out of
scope. The `automatic_breaks_not_supported` error is removed.

## Agreed behavior

### Break policy

The policy is defined in code, not YAML:

| Break | Kind | Preferred start | Duration | Allowed start shift |
|---|---|---:|---|---:|
| Morning coffee | `coffee` | 10:30 | `defaults.coffee_break` | ±30 min |
| Lunch | `lunch` | 12:15 | `defaults.lunch` | ±15 min |
| Afternoon coffee | `coffee` | 15:00 | `defaults.coffee_break` | ±30 min |

- Preferred starts and windows are course-local wall-clock times. A break's
  window is `[preferred - shift, preferred + shift]`, inclusive.
- Durations come from the course `defaults` (default 15 and 45 minutes).
  Automatic breaks have no per-break override.
- A policy break applies to a day only when its preferred start lies strictly
  after the effective day start and strictly before the effective day end. For
  example, a day starting at 10:30 gets no morning coffee break.
- Convert the preferred start to an elapsed offset from the day start. Dated
  days cannot cross an offset transition, so the same wall-clock arithmetic
  works for dated and floating days.
- Automatic mode continues to reject manual break entries (phase-1
  `manual_break_in_automatic`).

### Interactive allocation and final omission decisions

Start with all applicable policy breaks as the active set. Before each
placement pass, lay out the original sessions sequentially from the day start,
in input order:

- Fixed sessions use their declared durations.
- If there is an Interactive session, allocate
  `max(15, available_minutes - fixed_session_minutes - active_break_minutes)`.
  Break durations are reserved here, but breaks are not yet in the timeline.
- Without an Interactive session, the initial timeline contains only fixed
  sessions.

This timeline is provisional: activities can end before the configured day
end even with Interactive, because some reserved breaks have not been placed.
The omission test uses this provisional timeline, including breaks already
placed in the current pass, not a hypothetical final timeline.

**An omission is final.** When the placement algorithm omits a break, remove
it from the active set permanently. Recompute Interactive's duration from the
smaller break total and restart placement from the original, unsplit sessions.
Do not reintroduce or retest an omitted break, even if reallocation makes
activities extend past its preferred start or creates a valid boundary for it.
Omission therefore can occur on a day with Interactive.

Rebuild all remaining break placements and splits on a restart: Interactive
may appear before or between fixed sessions, so changing its duration can move
previous boundaries. Do not simply extend it in an already timed schedule.
Placements are final only within a pass; omissions remain final across passes.

Each restart removes one active break, so at most four passes are needed for
the three-rule policy. A successful final pass places every remaining active
break. Interactive then subtracts only actually placed breaks and fills the
day, or receives its 15-minute minimum with an overflow warning. Recompute the
minimum and fit diagnostics from that final allocation; do not blindly add a
break's duration to a minimum-clamped Interactive session.

### Placement algorithm

Within each pass, place active breaks one at a time, in chronological policy
order: morning coffee, lunch, afternoon coffee. Every later activity moves by
the placed break's duration before the next break is considered. Previously
omitted breaks are skipped.

For each break with preferred offset `p`:

1. **Boundary.** A valid boundary is the point between two consecutive
   sessions or session fragments. It is never the day start, the end of the last
   session, or a point adjacent to another break. Choose the valid boundary
   within the window that is nearest `p`; on a tie, choose the earlier
   boundary. Insert the break there.
2. **Split.** Otherwise, find the session or fragment that strictly contains
   `p` (`start < p < end`). If it is splittable (`Presentation`, `Exercise`),
   split it at `p`. Every fragment must be at least 15 minutes:
   - If the fragment before `p` would be shorter than 15 minutes, move the
     split and the break 15 minutes later (`p + 15`).
   - If the fragment after `p` would be shorter than 15 minutes, move the split
     and the break 15 minutes earlier (`p - 15`).
   - Shift only once. If either fragment would still be shorter than 15
     minutes, splitting is impossible. A session shorter than 30 minutes can
     never be split.
   A ±15 minute shift stays inside every policy window. An already split
   fragment can be split again by a later break.
3. **Omit and restart.** Otherwise, omit the break when the day's provisional
   activities, after earlier placements in this pass, end at or before `p`.
   Record an `automatic_break_omitted` info diagnostic with this activity end
   and the preferred start. Remove the break from the active set and restart
   allocation and placement as described above, even without Interactive.
4. **Fail.** Otherwise, report an `automatic_break_placement` error and produce
   no schedule. The message names the break and its allowed window, plus the
   blocking session's title, type, and current times. It explains why the
   session cannot be split (`Excursion`/`Interactive` are not splittable, or a
   fragment would be shorter than 15 minutes). It suggests reordering or
   resizing sessions so that one ends inside the window, or setting
   `break_mode: manual` for the day.

Do not search alternative placements or omission sets after a placement error.
In particular, a restart can make a previously placeable break impossible;
report that error rather than returning stale timings or undoing a final
omission.

Collect placement errors from all days before raising a single
`SchedulingError`, as phase 2 already does for per-day errors.

### Diagnostics

New codes in `DiagnosticCode`:

| Code | Severity | Location | Condition |
|---|---|---|---|
| `automatic_break_placement` | error | blocking session, or the day | No boundary, split, or omission is possible |
| `session_split` | info | split session | A break split a session; message names break and time |
| `automatic_break_omitted` | info | day | Provisional activities ended at or before the preferred start, with no valid placement |

Remove `automatic_breaks_not_supported`.

Return one info diagnostic per final omission. Its message explains that the
decision used the provisional activity end and that any reserved time is
reallocated to Interactive. Retain these omission diagnostics across restarts,
but discard split diagnostics from abandoned passes. Report only splits
present in the final schedule.

Fit diagnostics keep their phase-2 codes and are evaluated after the final
placement pass:

- The used time includes the placed automatic breaks.
- `schedule_overflow` points at the first session crossing the configured day
  end. If an automatic break crosses it, the warning points at the day schedule.
- `unused_time` applies only without an Interactive session.
- `missing_manual_breaks` remains a manual-mode warning only.

Info diagnostics never change exit codes, including with `--strict`.
`ScheduleResult.warnings` stays warning-only. Add an `infos` property with the
same shape.

### Result model changes

`ScheduledEntry` changes:

- `schedule_index: int | None` remains the source YAML entry index. It is
  `None` for automatically inserted breaks.
- Add `part: int` and `parts: int`: the 1-based fragment number and total
  fragments of the source session (`1`/`1` when not split). Breaks are always
  `1`/`1`.
- Add a `part_label` property returning `"part 1/2"`-style text for split
  sessions and `None` otherwise.
- Add an `is_automatic` property (`schedule_index is None`).
- Automatic breaks use a synthetic `Break` source with `kind` set and no
  duration override. Existing `kind`, `title`, `break_kind`, and `trainer_ids`
  properties therefore work unchanged.
- Fragments share the original session object as `source` and keep its
  `schedule_index`, which preserves session identity across fragments.

`ScheduledDay.entries` remains in chronological order. Elapsed offsets remain
the canonical arithmetic values. Local and aware times are derived as in phase 2.

## Worked example

### Acceptance fixture

`testdata/Kleppen_auto_breaks.yaml` is the acceptance fixture. Day 1 runs from
09:00 to 17:00. It has 360 fixed minutes and 75 applicable break minutes, so
Interactive receives 45 minutes:

| Step | Result |
|---|---|
| Morning coffee, window 10:00-11:00 | Boundary 10:30 (distance 0): coffee 10:30-10:45 |
| Lunch, window 12:00-12:30 | Boundary 12:30 (Cavitatie / Hydrohal, distance 15): lunch 12:30-13:15 |
| Afternoon coffee, window 14:30-15:30 | Boundaries 14:45 and 15:30; nearest 14:45: coffee 14:45-15:00 |
| End | Discussion 16:15-17:00; exact fit, no warnings |

Day 2 runs from 09:00 to 16:00. It has 345 fixed minutes and 75 break minutes,
so the remainder is 0. Interactive receives its 15-minute minimum and causes
15 minutes of overflow:

| Step | Result |
|---|---|
| Morning coffee | Boundary 10:30: coffee 10:30-10:45 |
| Lunch, window 12:00-12:30 | Nearest boundary 12:45 is outside the window. "terugslagkleppen dynamisch" (10:45-12:45, Presentation) is split at 12:15 into 90 + 30 minutes: lunch 12:15-13:00; `session_split` info |
| Afternoon coffee | Boundary 15:00 (WANDA / Drukbegrenzers): coffee 15:00-15:15 |
| End | Discussion 16:00-16:15; `interactive_minimum_overflow` warning (15 minutes) |

### Omission followed by Interactive reallocation

A 09:00-15:05 day contains two 90-minute Presentations followed by Interactive,
using the default break durations. All three policy breaks apply.

| Pass | Reserved breaks | Interactive | Placement and outcome |
|---|---|---:|---|
| 1 | 75 minutes | 110 minutes | Morning coffee 10:30-10:45; second Presentation 10:45-12:15; lunch 12:15-13:00; Interactive 13:00-14:50. No afternoon boundary or split exists, and activities end before 15:00: permanently omit afternoon coffee and restart. |
| 2 | 60 minutes | 125 minutes | Morning coffee and lunch keep the same times; Interactive is now 13:00-15:05. Afternoon coffee stays omitted, without retesting its placement. |

The result ends at 15:05 with one `automatic_break_omitted` info diagnostic
explaining the provisional 14:50 end, and no fit warning. It neither loses
15 minutes nor rejects the schedule because Interactive now spans 15:00.

Reallocation must also work when Interactive is not last. For the same day,
Interactive followed by Presentations of 100 and 90 minutes initially gets
100 minutes. Morning coffee is at 10:40, lunch splits the first Presentation
into 80 + 20 minutes, and activities end at 14:50, omitting afternoon coffee.
On restart Interactive gets 115 minutes, morning coffee moves to 10:55, and
lunch splits that Presentation into 65 + 35 minutes. The final day ends at
15:05. Keeping the first pass's break times or split lengths would be wrong.

## Public interfaces

### Python API

`schedule_course(course)` now schedules manual and automatic days. Its signature
does not change. Callers distinguish fragments and automatic breaks with
`part`, `parts`, `part_label`, and `is_automatic`. `ScheduleResult.infos` exposes
info diagnostics.

### CLI

`plan schedule` handles automatic days. Its help text no longer says "manually
placed breaks".

- The preview shows fragments as `Title (part 1/2)` and automatic breaks as
  `Coffee break` / `Lunch break`.
- Warnings and info diagnostics are printed to stderr in their severity colors.
- `--strict` fails only on warnings.

## Implementation sequence

### 1. Diagnostics and result model

- Add the three new diagnostic codes and remove
  `automatic_breaks_not_supported`.
- Extend `ScheduledEntry` (`schedule_index` optional, `part`, `parts`,
  `part_label`, `is_automatic`) and add `ScheduleResult.infos`.
- Generalize `_build_scheduled_entries()` to accept an ordered list of internal
  segments instead of zipping `day.schedule` with durations. Each segment holds
  source, schedule index, duration, part, and parts. Manual mode produces one
  segment per input entry.

### 2. Break policy

- Define a frozen `AutomaticBreakRule` (name, kind, preferred `time`, shift
  minutes) and an ordered `AUTOMATIC_BREAK_POLICY` tuple in a new
  `automatic_breaks.py` module.
- Resolve each rule's duration from `Defaults` by kind and its applicability
  from the effective day start and end.

### 3. Placement engine

- Implement placement as pure functions over elapsed-minute segments. Keep them
  independent from time zones, YAML, and the CLI.
- Implement, in order: boundary selection with tie-breaking, splitting with the
  single ±15 minute shift, omission, and error construction.
- Add the bounded placement-pass loop: allocate from the active break total;
  permanently remove the first omitted break, then restart from original
  sessions. Never reintroduce omitted breaks. At most four passes are needed.
- Publish only final segments and split diagnostics, plus one info diagnostic
  for each omission. Run fit diagnostics on the final allocation and segments.
- Wire automatic mode into `_schedule_day()` and remove the up-front
  automatic-mode rejection in `schedule_course()`.

### 4. CLI and documentation

- Print info diagnostics, update the preview labels, and update the `schedule`
  help text.
- Update the README sections on automatic breaks, the scheduling API, and
  diagnostics.
- Mark milestone 3 complete in `IMPLEMENTATION-PLAN.md`.

## Acceptance tests

Use pytest, Ruff, and `ty check`. Use deterministic fixtures and no network
access.

| Area | Acceptance cases |
|---|---|
| Applicability | Preferred start strictly inside the day; day starting at 10:30 has no morning coffee; day ending at 12:15 has no lunch; half-day courses |
| Durations | Breaks use `defaults.coffee_break` and `defaults.lunch`, including non-default values |
| Boundary | Exact preferred boundary; nearest boundary inside the window; tie chooses the earlier boundary; boundaries on the window edges are inclusive; day start and last-session end are never used |
| Splitting | Split at `p`; leading fragment < 15 moves split to `p + 15`; trailing fragment < 15 moves split to `p - 15`; session < 30 minutes cannot be split; fragment split again by a later break; `Excursion` and `Interactive` never split |
| Omission | No boundary or split and provisional activities ending at or before `p` omit the break; this includes Interactive days; the decision stays final even when reallocation crosses `p` or creates a valid boundary; one info diagnostic names the provisional end |
| Errors | Excursion spanning a window fails with `automatic_break_placement`; message names break, window, and session; a restart that makes another break impossible fails rather than retaining stale placements; errors from multiple days are collected; no partial schedule |
| Interactive | Final duration subtracts only placed breaks; the 09:00-15:05 omission example allocates 125 minutes and ends at 15:05 without fit warnings; minimum 15 minutes with final overflow warning; Interactive before, between, and after fixed sessions |
| Restarts | Rebuild boundaries and fragments after reallocating Interactive, including the 100/90-minute example; no abandoned split diagnostics; recompute minimum/overflow warnings rather than adding omitted minutes blindly; multiple omissions terminate within four passes |
| Fit | Overflow and unused time include automatic breaks; overflow location rules |
| Result model | Fragments share `schedule_index` and source; `part`/`parts`; automatic breaks have `schedule_index is None`; chronological order; dated and floating days |
| Worked example | `Kleppen_auto_breaks.yaml` produces exactly the times and diagnostics in the worked example |
| CLI | Automatic days preview; part labels; info on stderr; `--strict` ignores info and fails on warnings |
| Regression | All phase-1 and phase-2 tests pass; manual-mode days are unchanged |

## Definition of done

1. Automatic-mode days schedule deterministically by the rules above, and
   manual-mode days are unchanged.
2. Every applicable break is placed, split into a session, omitted with an info
   diagnostic, or rejected with an actionable error.
3. The result exposes fragments and automatic breaks without renderers having
   to reinterpret scheduling rules.
4. The CLI preview and diagnostics show fragments, automatic breaks, and info.
5. The worked example matches, and the full test, lint, format, and type
   checks pass.
6. PDF, PNG, and Markdown rendering are not implemented in this phase (see
   [PHASE-4-PLAN.md](PHASE-4-PLAN.md)).
