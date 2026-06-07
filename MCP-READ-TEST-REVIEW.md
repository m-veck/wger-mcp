# MCP read-test review — problems found

Test date: 2026-06-07. Ground truth = raw wger REST API (`https://wger.lan.exod.nl/api/v2`, read-only GETs).
Only problems are documented below. Tools that matched the raw API exactly are listed at the end.

Key data fact that underlies several bugs: in this wger version, `workoutlog.date`, `weightentry.date` and `nutritiondiary.datetime` are **full datetimes** (e.g. `2026-06-06T16:35:31+02:00`), not date-only. `workoutsession.date` is date-only. Several MCP tools assume date-only and/or do arithmetic on the API's string-typed `repetitions`/`weight`/`value` fields.

---

## 🔴 Data missing / empty / tool unusable

### 1. `list_workout_logs` — `date_to` drops all logs from its own day (THE main bug)
The `date_to` filter is sent to the API as `date__lte=<date>` (date-only). Because `workoutlog.date` is a datetime `…T16:35…`, every log on `date_to` itself is `> date_to 00:00:00` and gets excluded. Net effect: **any query whose `date_to` is the last/only day containing logs returns those logs as empty.**

Exact input → output (ground truth in parentheses):

| date_from | date_to | exercise_id | MCP returned | Raw API truth |
|---|---|---|---|---|
| 2026-06-06 | 2026-06-06 | – | `[]` | 33 logs |
| 2026-06-05 | 2026-06-06 | – | `[]` | 33 logs |
| 2026-06-03 | 2026-06-03 | – | `[]` | 16 logs |
| 2026-06-06 | 2026-06-06 | 1852 | `[]` | 8 logs |
| 2026-06-06 | 2026-06-07 | – | 33 logs ✓ | 33 logs |
| 2026-06-01 | 2026-06-07 | – | 63 logs ✓ | 63 logs |

Note: the original report that "6-03 worked but 6-05/06 was empty" is misleading — `date_from=date_to=2026-06-03` is **also** empty. The real rule: it only "works" when `date_to` is strictly *after* the last day that has logs. So same-day queries and "ending today/last training day" queries silently lose data. This is the highest-impact bug because the coach uses same-day / recent-range queries constantly.

Raw API confirmation: `workoutlog/?date=2026-06-06` and `?date__lte=2026-06-06` also return 0; only `?date__gte=2026-06-06&date__lte=2026-06-07`, `?date__date=2026-06-06`, or datetime bounds return the 33. So the fix lives in how the MCP builds the date filter, not in wger.

### 2. `weekly_summary` — crashes, never returns data
Input: `days=7`. MCP error: `unsupported operand type(s) for +=: 'int' and 'str'`.
Raw API has 63 logs in window (33 on 6-06, 14 on 6-04, 16 on 6-03). The tool adds the API's string `repetitions`/`weight` (`"60.00"`) without casting to float. Completely unusable. Same crash at any `days` value tested.

### 3. `exercise_history` — crashes for every exercise
Inputs tried: `exercise_id=1852`, `711`, `194` (each with default/90 days). MCP error: `unsupported operand type(s) for +=: 'int' and 'str'`.
Raw API has plenty of logs for all three (e.g. 1852 = 9 logs on 6-06, 194 = logs on both 6-03 and 6-06). Same string-vs-float aggregation bug. Tool never returns anything.

### 4. `personal_records` — crashes (single exercise and all-exercises)
Inputs: `exercise_id=1852`; and no exercise_id (all). MCP error: `'>' not supported between instances of 'str' and 'int'`.
The PR comparison (`value > best`) compares the API's string `weight`/`repetitions` against numeric seeds. Never returns records.

### 5. `compare_periods` — crashes
Input: `window_days=7`. MCP error: `can't multiply sequence by non-int of type 'float'`.
Volume = `reps * weight` where `reps` is still the API string (`"10.00"`), so `str * float`. Never returns.

### 6. `volume_trend` — silently returns empty series (no error, no data)
Inputs tried: `days=60` (week buckets); `days=10, bucket=day`; `days=60, exercise_id=194`. Every call returns `"series": []` with correct `since`/`until` window but zero data, despite 63 in-window logs.
Unlike #2–#5 this one fails **silently** — the most dangerous variant, because the coach would conclude "no training volume" rather than seeing an error. (Likely the same date-window/string handling, but swallowed.)

### 7. `list_workouts` — returns a 404 error blob
Input: none. MCP returns `{"result":[{"error":true,"status":404,"detail":"<full wger 'Page not found' HTML page>"}]}`.
Raw API: `/api/v2/workout/` → HTTP 404 (endpoint does not exist on this wger version; the new model uses `/routine/`). The tool targets a non-existent endpoint and surfaces a raw HTML error page instead of an empty list or clean message.

---

## 🟡 Incomplete / misleading

### 8. `list_routine_days` — `routine_id` filter has no effect
Input: `routine_id=3`. MCP returns 4 days belonging to routines 2, 3, 4 **and** 5 (Push A, Core A, Pull A, Legs A) — not just routine 3's day.
Ground truth: the raw API `day/?routine=3` *also* returns all 4 (each routine happens to own exactly one day), so the root cause is that **wger ignores the `routine` query param** on `/day/`. But the MCP presents the result as "days of routine 3", which is misleading — callers will attribute other routines' days to routine 3. Needs client-side filtering on the `routine` field of each returned day.

### 9. `list_slot_entry_configs` — `slot_entry_id` filter has no effect
Input: `slot_entry_id=7`. MCP returns config rows (`sets`/`reps`/`weight`) for slot_entries 7,8,9,…,30 — i.e. every config in the database, not just entry 7.
Ground truth: raw `repetitions-config/?slot_entry=7` also returns all 24 rows, so wger ignores the `slot_entry` filter here too. Root cause is wger, but the MCP presents an unscoped dump as if it were one entry's config — misleading and makes the tool unusable for reading a single entry's per-set scheme. Needs client-side filtering by `slot_entry`.

### 10. `get_routine` — does not include the day structure its description promises
Input: `routine_id=3`. Tool description says "Fetch a single routine **with its day structure**", but the output is only the flat routine object (name/description/start/end/flags) with no days/slots/entries. Not wrong data, but the contract is unmet; callers must additionally call `list_routine_days` (which is itself broken, see #8).

---

## 🟢 Minor

### 11. Seconds-unit and reps=0 entries are passed through raw, with no interpretation
`list_workout_logs` / `get_workout_log` faithfully return `repetitions_unit: 3` (seconds) and `repetitions: "25.00"` for L-sit (1852) and handstand (711), and `repetitions: "0.00"` for skipped exercises (537/1185/454/1336 on 6-06). The data is *correct*, but no tool resolves `repetitions_unit`/`weight_unit` codes to labels (1=reps, 3=seconds, 4=?) — a consumer can easily misread "25" seconds as 25 reps. Because the analytics tools that *would* aggregate these all crash (#2–#6), it's currently impossible to even check whether seconds/0-rep entries are handled sanely downstream. Flagging for when those tools are fixed: ensure seconds-unit volume and reps=0 sets are treated correctly (e.g. not multiplied as reps, not counted as completed sets).

---

## Tools verified OK (matched raw API)
- `get_workout_log` (id 56) — exact match.
- `list_workout_logs` with no filter, and with windows whose `date_to` is after the last log day — correct (63 / 33).
- `list_workout_logs` with `exercise_id` only (no dates) — correct (1852 → 8 logs).
- `get_body_weight_history` — 3 entries, matches `weightentry/`.
- `list_routines` (4 routines) and `get_routine` payload fields — match.
- `list_routine_days` *contents* are correct (the bug is only the missing filter, #8).
- `list_slots` (day 3 → 8 slots), `list_slot_entries` (slot 7 → entry 7), `get_slot_entry` (7) — correctly scoped & accurate.
- `list_nutrition_plans` / `get_nutrition_plan` (plan 1) — match.
- `nutrition_summary` / `list_log_items` — correctly empty (raw `nutritiondiary/` count = 0).
- `search_exercises` ("dips"), `get_exercise` (1852) — full, correct detail.
- `list_categories`, `list_muscles`, `list_equipment` — complete and accurate.
- `whoami` — returns the `madmax` profile.
