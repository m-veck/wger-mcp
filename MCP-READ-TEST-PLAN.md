# MCP read-test plan

**Doel:** verifiëren dat de wger-MCP **lees**-tools alles teruggeven wat écht in wger staat. Grondwaarheid = de ruwe wger REST API. Documenteer **alleen de problemen** (tools die leeg/incompleet/fout teruggeven t.o.v. de ruwe API).

**Aanleiding:** `mcp__wger__list_workout_logs` gaf `[]` voor 2026-06-05/06, terwijl sessie 5 (Push, 6-06) wél in wger staat (zichtbaar in web-UI + ruwe API). Alleen via de ruwe API gevonden. Ook de L-sit-data (seconden-unit, voet-ondersteund, reps=0 voor niet-gedane oefeningen) werd verkeerd geïnterpreteerd.

## Grondwaarheid (read-only GETs)
```bash
TOKEN=$(ssh maxe@172.16.1.83 "grep -oE 'WGER_API_TOKEN=.*' /opt/wger-mcp/.env | cut -d= -f2")
ssh maxe@172.16.1.83 "curl -s -H 'Authorization: Token $TOKEN' 'https://wger.lan.exod.nl/api/v2/<endpoint>/?format=json&...'"
```

## Te testen lees-tools (MCP-output vergelijken met ruwe API)
- **Workout:** `list_workout_logs` (date_from/date_to: vandaag-same-day, laatste 3 dgn, deze week, geen filter, + exercise-filter), `get_workout_log`, `list_workouts`
- **Routines:** `list_routines`, `get_routine`, `list_routine_days`, `get_routine_day`, `list_slots`, `list_slot_entries`, `list_slot_entry_configs`, `get_slot_entry`
- **Analytics:** `weekly_summary`, `volume_trend`, `exercise_history` (L-sit 1852, handstand 711, dips 194), `personal_records`, `compare_periods`
- **Body:** `get_body_weight_history`
- **Nutrition:** `list_nutrition_plans`, `get_nutrition_plan`, `nutrition_summary`, `list_log_items`
- **DB:** `search_exercises`, `get_exercise`, `list_categories`, `list_muscles`, `list_equipment`

## Focus-cases (de vermoedelijke bugs)
1. **`list_workout_logs` datumfilter** — dé hoofdverdachte. Probeer diverse date_from/date_to-combinaties en vergelijk de **counts** met de ruwe API. Pin exact welke datum-input faalt (vermoeden: same-day / datetime-vs-date in `date__gte`/`date__lte`). Bekend: 6-03 werkte, 6-05/06 gaf leeg.
2. **Sessie 5 (6-06 Push)** — kan de MCP die sessie/logs überhaupt ophalen? (Exercises: 537, 1185, 454, 1336 = reps 0/niet gedaan; 1852/1284/711/194 = wél gedaan.)
3. **Tijd-unit-logs** (L-sit/handstand, repetition_unit=3 seconden) + reps=0-entries — leest de MCP die correct, of verwart het seconden met reps?
4. **Analytics** — pakken `weekly_summary`/`volume_trend`/`exercise_history` de recente sessies mee, en gaan ze goed om met 0-rep + seconden-entries?

## Deliverable
`MCP-READ-TEST-REVIEW.md` met **alleen de problemen**: per geval → welke tool, welke input, wat de MCP gaf, wat de ruwe API gaf (grondwaarheid), en de geconstateerde gap. **Geen fixes** — puur documenteren voor later.
