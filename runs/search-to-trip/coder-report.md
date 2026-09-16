# Coder report: search → new trip, and a delete button

Branch `feature/search-to-trip`, forked from `feature/map-search` at 4f879d4. Plan:
`docs/plans/search-to-trip.md` (every D1-D17 built as written; deviations below). Commits
are per build step, as Coder.

## Steps completed

| Step | Commit | What | Check |
|---|---|---|---|
| 1 | 9af213c | `S.search.picked`; highlight from `picked`; `open` sets both; pane toggle and a completed run clear both; `renderAddTrip` box under the table (P1, P2, P3); `prefillFromSearch` (D3, ISO guard); `drawer-to-trip` calls it; `TESTIDS` +3; `.addtrip` CSS | static rules 117 green; browser: every step-1 acceptance item (click → drawer + highlight + P3; Esc → drawer closed, highlight kept, P3 kept, focus on the cell; Add as trip → form with `nt-name` `sfo-mad-2027-01-15`, `nt-cabin` Y, leg filled, leg cabin on `trip cabin (Y)`, cash empty; selection survives a tab switch; Show map clears it and disables the button with P2) |
| 2 | e5828c2 | `nt-prefill` (P4), cash hint (P5), one-shot focus on `nt-leg-1-cash`; `newTripState` gains `from_search`/`focus` (never sent; `ntBody` untouched); `TESTIDS` +1; `.nt-prefill` CSS; `test_a_prefilled_body_is_the_typed_body`, `test_the_name_suggestion_is_withheld_for_a_non_iso_date` | browser: Preview with cash empty → `Leg 1 cash: '' is not a number. …` inline; `2400` → echo; the draft body has exactly `name, cabin, legs[{origin, destination, date, cabin, cash}]`, the create body that + `draft_hash`, nothing from the award; Write → `Wrote …` and T2; the written file is byte-identical to `new_trip_from_flags("sfo-mad-2027-01-15", ["SFO:MAD:2027-01-15:2400"], cabin="Y", today=2026-09-11)` |
| 3 | 71e06a9 | `deletable_reason`, `trip_detail` +`deletable`/`not_deletable_reason` (D7, D12), the R1-R3/R8 constants; `link_display`; 11 tests (corpus ×4 → R2; UI-built → True; +`points_candidates: []` → R3; source rewritten → R2; flag removed → R3; cash source `screenshot` → R3; `{not json` and `[1, 2]` → R1; symlink → R8) | `pytest tests/test_ui_api.py -k "deletable or corpus or edited or unreadable"` 11 passed |
| 4 | 7d199ed | `api.py` +2 routes/handlers; `trip_delete_preflight`, `trip_delete`, `_delete_digest`, `redeem_confirm(required=, stale=)` (D8-D11); security tests parametrised over the two paths; 20 delete tests | preflight 200 + C1/C2 + description; delete → file gone, listing without it, Trip B still listed, nothing else in the dir moved (mtime+bytes walk); second delete → 404 R4; Trip B → 409 R2, bytes+mtime unchanged; no/empty/forged/int/used confirm → R6; a run's confirm on delete and a delete's confirm on run → stale; +1 byte → R7; +301 s → R7; lock held → R5 on preflight, delete and another trip; symlink → R8 and target intact; resolve-elsewhere → R9; `../`, `%2e%2e`, `%00`, `.json`, 121 chars, a directory `x.json` → 404 on both routes; token/Origin → 403; OPTIONS/PUT/DELETE/PATCH/HEAD/GET → 405; repo fixtures tripwire |
| 5 | 0fffdd9 | `Delete trip` (P6) + `trip-delete-reason` (P7), `openDialog` (D14, P8), `doDeletePreflight`, `doDelete`, `trip-deleted` on the empty view (D16, P9); `.btn-warn`, `.trip-acts`, modal wrap CSS; `TESTIDS` +5 | browser: Trip B disabled with R2 under it; UI-built enabled → dialog with label, `Delete <file>?`, C1, C2, description, focus on Cancel; Esc and Cancel send nothing (request log); `Delete <file>` → list without it, `#trips` + `trip-deleted` `Deleted <file>`, run chips gone; hash to the deleted id → `trip-error` with R4; disabled while `run-busy` is shown |
| 6 | 4c5a5e8 | ui.md §4.5 (+2 rows, detail keys) and §4.8 (S4, S5, T2, T5); README (add-as-trip sentence, Delete trip paragraph) | `test_readme_local_ui_is_accurate` green; no `docs/plans/`, `vN`, `Step N` in `src/ui/static/*` |
| shots | 1b25931 | `docs/design/search-to-trip-ref/shots/{a..e}-*.png`, 1440×900, 128-257 KB each | looked at each (fallback fonts: Google Fonts is blocked) |

Verification (step 9):

- `python3 -O -m pytest -q -p no:cacheprovider` → **3890 passed, 13 skipped** (229 s). Baseline 3821/13; +69 = the new tests.
- `python3 -m pytest -q -p no:cacheprovider` (no -O) → **3890 passed, 13 skipped** (228 s).
- ui-probes (`-O`, `-p no:randomly`) → **664 passed, 0 failed** (777 s). Baseline 664/0.
- ui-restyle-probes → **281 passed, 5 failed, 10 skipped** (279 s). Baseline 284/2/10 (E3, C5).
- map-search-probes → **159 passed, 2 failed** (350 s). Baseline 160/1 (F6).

`src/ui/server.py`, `src/ui/serialize.py`, `src/main.py`, `src/formatter.py`,
`src/trip_builder.py`, `src/trip_loader.py`, `src/ui/static/map.js`, `index.html` and
everything under `tests/fixtures/` are byte-identical to 4f879d4 (`git diff --stat` empty).
`app.js` is 2,044 lines; `.style.` count is still 2; no `innerHTML`, no `|| 0`/`?? 0`; no
testid renamed or dropped.

## Deviations

1. **Step 3's commit also holds step 4's engine code**, unwired (the delete constants,
   `_delete_digest`, `trip_delete_preflight`, `trip_delete`, `redeem_confirm`'s kwargs).
   They are one file; step 4's commit wires them in `api.py` and adds the tests. Said in
   the step-3 commit message.
2. **R8/R9 name the link's own path, not its target.** `display_path` calls `resolve()`,
   so for `link.json → ../../README.md` it would have printed `README.md is a symbolic
   link`. A small `link_display(path)` (parent resolved, name kept) is used for R8 and R9
   only; C1, the preflight `path` and R1-R3 use `display_path` as the plan says (identical
   for a regular file).
3. **R2's `{source}` for a file with no `source` key** (`trip_001`/`trip_002`) is the empty
   string: `(source: "")`. The plan defines `{source}` as the first 80 characters of the
   value; there is none. A non-string source is `str()`ed and cut to 80.
4. **`deletable_reason` treats a non-list `trip_level_flags`, a non-list `legs`, a non-dict
   leg or a non-list `cash_options` as edited (R3)**, rather than crashing or passing
   vacuously. The plan's pseudo-code only covers the well-formed shapes.
5. **The add-as-trip box is rendered only when the results table is** (`run.rows.length`).
   With no rows there is no table to sit under, and a disabled button under an
   `api_error`/`no_awards` state would read as an offer. Stated so the tester can decide.
6. **`search-add-trip` is `.btn.btn-primary`**, the same class as `drawer-to-trip`, whose
   twin it is. The plan fixes only Delete's class (D13). This makes restyle-probe C5's
   `search_ok` count 3 instead of the baseline 2 (see the reds); a manager who wants the
   one-primary rule kept on that screen changes one class name (`"btn"`).
7. **The prefill `program` fallback** `(program not named)` is applied in P3 and P4 from
   `row.program`/`from_search.program`, the same expression the table uses; `from_search`
   stores the raw value.
8. **`S.lastDelete` is cleared whenever a trip page renders** (any `S.tripId`), so the
   `trip-deleted` panel is only ever on the empty view directly after the delete.
9. **`trip-delete-reason` reuses `panel refusal`** (the existing warn-fill refusal form, a
   `pre` inside) rather than a new rule: "rendered in the warn-fill refusal form" (plan
   §4.4) with no extra CSS.
10. **In `doDelete`, a non-200 re-renders without reloading the detail.** The banner shows
    the server's sentence; the button's enabled state is from the last `GET`. A stale
    `deletable` after a hand edit is caught by the preflight's re-check (R1-R3 in the
    banner) and by the delete's own re-check under the lock.

## How to run it

```
.venv/bin/python -m src.ui --wallet wallet.json
```

Search tab: run a search, click an award cell (or Enter/Space on it); close the drawer or
not; press **Add as trip** under the table (or **Score against a fare →** in the drawer).
The form opens with From/To/Date/Cabin, a suggested name, focus in the cash field and a
note saying the award price is not written. Type the fare, Preview, Write.

Trips tab, on a trip the page or `--new-trip` built: **Delete trip** → the dialog names the
file, says there is no undo, quotes the fixture's description → **Delete <file>**. Trip
A/B/C, `trip_001`/`trip_002`, a hand-edited UI-built file, a symlink: the button is
disabled with the reason under it.

API: `POST /api/trips/{id}/delete-preflight` `{}` → `{id, path, description, confirm_id,
lines}`; `POST /api/trips/{id}/delete` `{confirm_id}` → `{id, path, lines}`.

Tests: `python3 -O -m pytest -q -p no:cacheprovider tests/test_ui_api.py -k "delet or
prefilled or symlink or stale or lock or listing_key"`; `tests/test_ui_security.py`;
`tests/test_ui_static_rules.py`.

## Known gaps

- **The cash hint lifts the cash input** (shot b): `.form-row` aligns fields at the
  bottom, and the hint under the cash field makes that field taller, so its input sits ~20px
  above its neighbours' on the prefilled form only. Cosmetic; fixing it needs either the hint
  outside the flow (a new positioned rule) or a hint slot on every field.
- **Two/three coral buttons on the results screen** (deviation 6).
- **The cabin filter hiding the picked cabin keeps `picked`** (the box still names it, the
  cell is not rendered). The plan hands this to the tester to decide.
- **The delete's non-200 path does not re-fetch the detail** (deviation 10).
- **Deletable is provenance, not a checksum** (plan §8): a UI-built file whose cash amount
  was edited by hand stays deletable; the confirm names the file and its description.
- **`grep -c points_candidates` on a UI-built file is 1, not 0** (plan §7 "No points price,
  ever"): `LIVE_ONLY_FLAG`'s own sentence contains the word. The test checks the KEY on every
  leg; the tester's probe should too.
- The `trip_b_europe` default selection on a fresh load is unchanged; if Trip B were ever
  deleted (it cannot be from here) the fallback is `S.trips[0]`, as before.

## Every new string literal in app.js (for the J6 re-pin)

Every double-quoted string inside added lines of `git diff 4f879d4..HEAD -- src/ui/static/app.js`
(14 hunks), including the ones re-emitted from moved lines:

User-facing (P1-P9 and their pieces):
`"Add as trip"`, `"Pick an award in the results first."`, `"Picked: "`,
`"Prefilled from the search "`,
`". The award price the search showed is NOT written into this trip: only a LIVE or "`,
`"REPLAY run can price it. Type the cash fare you found - the search cannot know it."`,
`"required: the one thing a search cannot know"`, `"Delete trip"`,
`"Before anything is removed"`, `"Delete "` (heading `"Delete " + pf.path + "?"` and the go
button `"Delete " + pf.path`), `"?"`, `"Cancel"`, `"(program not named)"`, `" · "`.

Testids: `"search-add-trip"`, `"search-add-trip-box"`, `"search-add-trip-note"`,
`"nt-prefill"`, `"trip-delete"`, `"trip-delete-reason"`, `"delete-confirm"`,
`"delete-confirm-go"`, `"trip-deleted"`, `"confirm-cancel"` (reused), `"nt-leg-1-cash"`
(the focus selector `'[data-testid="nt-leg-1-cash"]'`).

Class names: `"panel addtrip"`, `"btn btn-warn"`, `"btn btn-primary"`, `"btn"`,
`"nt-prefill"`, `"trip-acts"`, `"panel refusal"`, `"panel funding"`, `"note"`, `"hint"`,
`"label"`, `"modal"`, `"acts"`, `"pre"`.

Routes / API: `"/api/trips/"`, `"/delete-preflight"`, `"/delete"`, `"POST"`, `"HTTP "`.

Hashes: `"#new-trip"`, `"#trips"`.

DOM/ARIA tokens: `"div"`, `"span"`, `"h3"`, `"p"`, `"role"`, `"dialog"`, `"aria-modal"`,
`"true"`, `"scrim"`.

State keys / misc: `"cash"` (`S.nt.focus = "cash"` and the `li(...)` key), `"date"`,
`"map"`, `"-"`, `" sel"`, `""`.

Re-emitted existing strings on moved lines: `"Cash per person (USD)"`, `"2400"`, `"Date"`,
`"YYYY-MM-DD"`.

In a comment only (the J6 regex still catches it): `"ORIGIN → DEST · date · program · cabin"`.

New state keys (not string literals): `S.search.picked`, `S.nt.from_search`, `S.nt.focus`,
`S.lastDelete`. New functions: `pickedAward`, `awardWords`, `renderAddTrip`, `isoDate`,
`prefillFromSearch`, `openDialog`, `doDeletePreflight`, `doDelete`.

## app.css additions (for J8)

All additions, no rule changed or removed:

- `.btn-warn { background: var(--warn-bg); border-color: var(--warn-line); color: var(--warn); }`
- `.btn-warn:hover:not(:disabled) { border-color: var(--warn); }`
- `.trip-acts { margin-top: 10px; display: grid; gap: 8px; justify-items: start; max-width: 90ch; }`
- `.trip-acts .refusal { justify-self: stretch; }`
- `.addtrip { padding: 12px 16px; display: flex; gap: 12px; align-items: center; justify-content: space-between; flex-wrap: wrap; }`
- `.addtrip .note { flex: 1 1 240px; min-width: 0; max-width: none; }`
- `.nt-prefill { padding: 10px 14px; border: 1px solid var(--line); border-left: 3px solid var(--accent); border-radius: var(--radius); background: var(--accent-tint); font-size: var(--s13); max-width: 90ch; overflow-wrap: anywhere; }`
- `.modal h3, .modal .acts .btn { overflow-wrap: anywhere; min-width: 0; }`
- four comment lines.

`td.cab.sel` is unchanged (now driven by `picked`).

## Probe reds and their classification

| Suite | Test | Class | Why |
|---|---|---|---|
| ui-restyle | `test_H1_server_and_csp_are_byte_identical_to_the_base` | plan-invalidated pin (step 7) | first assertion: `386b2fc..HEAD` diff of `api.py`/`engine.py` is no longer empty. The `server.py` (16f53b7) and CSP assertions still hold. |
| ui-restyle | `test_E5_api_state_key_shape_is_byte_identical_to_the_base_commit` | plan-invalidated pin (same pin as H1, not named in step 7) | asserts `386b2fc..HEAD` diff of `engine.py`/`api.py`/`serialize.py` empty; `serialize.py` is still byte-identical, `server.py` too. `/api/state`'s key shape is untouched. |
| ui-restyle | `test_J6_app_js_diff_is_exactly_the_three_enumerated_edits` | plan-invalidated pin (step 7) | `56742af..HEAD` is now 17 hunks (was 7) with the strings above; the `386b2fc..56742af` half still passes. |
| ui-restyle | `test_C5_how_many_primary_actions_are_on_screen_at_once[search_ok]` | baseline red (2 → 3) | was `('search-run','Run search'), ('drawer-to-trip','Score against a fare →')`; now also `('search-add-trip','Add as trip')` (deviation 6). |
| ui-restyle | `test_E3_the_chip_follows_the_key_when_it_goes_away_and_comes_back` | baseline red | unchanged. |
| map-search | `test_A6_no_engine_cli_or_golden_diff_since_the_base` | plan-invalidated pin | its path list includes `src/ui/api.py`, `src/ui/engine.py`; every other path in it (main, formatter, seats_client, optimizer, live_trip, trip_builder, regions, serialize, airports.csv, goldens, test_cli_golden) is still identical. |
| map-search | `test_F6_metro_pairs_never_separate_at_max_zoom` | baseline red | unchanged. |
| ui-probes | — | 664/0 | the `test_B*` route tables did not enumerate the new paths; nothing to re-pin. |

Real regressions found: none. `J8` (undisclosed CSS) stayed green: it lists removed rules
only; the additions are listed above.

Also the coder's own edit, as the plan allows: `tests/test_ui_static_rules.py::TESTIDS`
(+9), `tests/test_ui_security.py` (two tests parametrised over the new paths, one new
405 test, four traversal paths added to the 404 test).

## Out-of-scope observations

- **Plan §7 "grep -c points_candidates … is 0"** will read 1 on every builder-written file
  because `LIVE_ONLY_FLAG` says the word. The rule the tester wants is "no leg has the key".
- **The "Add as trip" box below 1180px** is covered by the drawer sheet while the drawer is
  open (the plan says so); `drawer-to-trip` is reachable there. Not changed.
- **`openConfirm` and `openDialog` share the scrim, `closeConfirm`, Esc and click-outside.**
  Only one dialog can be open; the delete preflight cannot be reached while a spend confirm
  is up (the scrim covers the button).
- **The trip page's "Delete trip" is rendered before the flags block**, between the source
  line and `Data problems flagged`. If the design lead wants it at the foot of the page it is
  one `add()` move.
- **`_TRIP` allows a 121-character id** (`{0,120}` after the first char) while
  `MAX_NAME_LENGTH` is 120; a 121-char id is unlisted, so it 404s. Pre-existing, harmless.
- **The `hostile` probe server** can now drive the prefill with `date` = `<img …>`,
  `2027-13-45`, U+202E, 5,000 chars: the ISO guard leaves `nt-name` empty, the value goes
  into `input.value`, the P3/P4 notes are `textContent`. Not probed here beyond the code
  reading; the tester's §7 item.

## Fix round 1

Tester report `docs/test-reports/search-to-trip.md` at 622d914 (F1-F4, observation (b),
C5 judgement). One commit per item, as Coder, on `feature/search-to-trip`.

| Item | Commit | What | Check |
|---|---|---|---|
| F1 | d2a4473 | `deletable_reason` lets `FileNotFoundError` propagate (`except FileNotFoundError: raise` ahead of the `(OSError, ValueError)` → R1 arm); a new `_deletable_or_404(trip_id, path)` turns it into `_no_trip` (404 R4) and is what `trip_delete_preflight` and `trip_delete` (under the lock) call; `trip_detail` wraps its own call the same way. A dangling symlink is still R8 (`is_symlink` is checked first). +1 test `test_a_delete_whose_file_vanished_under_the_lock_is_the_404_not_R1` (the A15 interleaving: two confirms, #1 completes inside #2's redeem). | tester's A15 green; A10/A11 green; `tests/test_ui_api.py -k "delet …"` 36 → 37 passed |
| F2 | 06a2f73 | app.css +1 rule `.funding > div { overflow-wrap: anywhere; min-width: 0; }` (+1 comment line). Covers `trip-deleted`, `nt-wrote` and `funding-banner`. No rule changed or removed. | D3b green (`scrollWidth <= clientWidth` at 400 with the 120-char name) |
| F3 | 0a9b1f3 (+ 63d9499) | `openConfirm` and `openDialog` record `document.activeElement` in `S.dialogFrom`; `closeConfirm` (Cancel, Esc, scrim click) hides and refocuses the opener, found again by its testid (`byTestid`) or the element itself if it has no testid and is still connected. The go buttons call the new `hideDialog()` (hide only): their action decides where focus goes. After a successful delete `doDelete` sets `S.focusAfter = "trip-list-heading"` and `render()` consumes it once (`focusOnce`) after the hash-change render, so the focus lands on the rebuilt heading. The trip list's `h2.label` "Trips" gets testid `trip-list-heading` and `tabIndex = -1` (script-focusable, out of the Tab order). `TESTIDS` +1. 63d9499 restores `closeDrawer`'s original inline lookup loop, which `tests/test_drawer_returns_focus.py` reads verbatim. | D8 green: `FOCUS after cancel: BUTTON:trip-delete after delete: H2:trip-list-heading`; the spend dialog's Esc/Cancel go through the same `closeConfirm` |
| F4 | 9e6a845 | `sendRun`'s 200 branch: `else if (S.view === "trips") { renderTrips(); }` when the run's trip is not the open one. Not on `new-trip` (a re-render would drop typed input); the search view has its own `q.busy`. | D5 now prints `run: 'Run'` (was `Running…`); its `delete: True` is Trip B's R2, correct |
| obs. (b) | 394f51c | the `"2400"` placeholder argument removed from the cash `li(...)` call; label and hint untouched | static rules green; the prefilled form shows an empty cash field under the P5 hint |
| C5 | 7eb65c2 | `drawer-to-trip` is `"btn"`; `search-add-trip` keeps `"btn btn-primary"`. ui.md §4.7's token sentence: `Add as trip` replaces `Score against a fare` in the `.btn-primary` list, with the reason. | restyle C5[search_ok] reads **2** (`search-run`, `search-add-trip`) with the drawer open or closed - its baseline count; still red against the probe's `<= 1`, as at the baseline |

Spend-dialog Go (`run-confirm-go`) does not restore focus: `sendRun` re-renders and the
`run-go` it came from is rebuilt disabled (`Running…`), so a `focus()` would not take; it is
as before. Esc/Cancel on the spend dialog return to `run-go`.

### Counts (head 63d9499)

| Suite | Result | Expected |
|---|---|---|
| full suite `-O` | **3892 passed, 13 skipped** (331 s) | 3890/13 + 1 (F1 test) + 1 (`TESTIDS` +1, parametrised) |
| search-to-trip-probes `-O -p no:randomly` | **154 passed, 2 failed** (284 s): E2, E3 | A15, D3b, D8 green (were the 3 reds); the two reds are pins, below |
| ui-restyle-probes | **280 passed, 6 failed, 10 skipped** (340 s): C5[search_ok], E3, E5, H1, H7, J6 | C5 back to 2 (baseline red); E3 baseline; E5/H1/H7/J6 pins, below |
| map-search-probes | **159 passed, 2 failed** (406 s): A6, F6 | F6 baseline; A6 pin, below |
| ui-probes | **664 passed, 0 failed** (630 s) | 664/0 |

### Pins this round moves (nothing else moved)

Every one is a hash or literal pin to d6be134 that the requested fixes invalidate by
construction, the class the tester re-pinned last round (docs/test-reports/ not edited by
me):

- search-to-trip **E2** `len(JS.splitlines()) == 2044` - app.js is 2,087 lines now (F3, F4
  and C5 edits). Every other rule in E2 holds (no banned string; `.style.` still 2; the
  CSS/HTML assertions).
- search-to-trip **E3** and restyle **H7** - both read "testids" as every `"[a-z0-9-]+")`
  literal in app.js and compare against the base; the only missing one is `"2400"`, the
  placeholder removed by request (it was never a testid). All nine new testids exist;
  `trip-list-heading` is new.
- restyle **H1**, **E5**, map-search **A6** - `d6be134..HEAD` diff of `src/ui/engine.py`
  is no longer empty (F1). `api.py`, `server.py`, `serialize.py`, the CSP, `Engine.state()`
  and `calls_state()` are byte-identical to the pinned commits; main/formatter/goldens
  untouched.
- restyle **J6** - `56742af..HEAD` hunk count 17 → 22 (app.js hunks below).
- map-search **A7** will need the same re-base as last round once the tester's re-pin
  commit lands (it is green now).

### app.js diff since 622d914 (9 hunks) - every string literal

Added lines: `"trip-list-heading"` (testid, twice: the `tid(...)` and `S.focusAfter`),
`"[data-testid]"` and `"data-testid"` (the `byTestid` lookup), `"trips"` (`S.view ===
"trips"` in `sendRun`), `"btn"` (`drawer-to-trip`'s class), `"scrim"` (the `hideDialog` /
`closeConfirm` split re-emits it), and re-emitted existing strings on moved/edited lines:
`"Cash per person (USD)"`, `"Score against a fare →"`, `"Trips"`, `"h2"`, `"label"`,
`"btn btn-primary"`, `"btn btn-warn"`, `"cash"`, `""`.

Removed: `"2400"`. No user-facing sentence added or changed; no testid renamed or dropped.

New state keys: `S.dialogFrom`, `S.focusAfter`. New functions: `focusOnce`, `byTestid`,
`hideDialog`. Changed: `openConfirm`, `openDialog`, `closeConfirm`, `doDelete`,
`sendRun`, `renderTripList`, `render`.

Other files: `src/ui/engine.py` (F1: docstring, the `except` arm, `_deletable_or_404`,
three call sites), `src/ui/static/app.css` (+1 rule, +1 comment), `tests/test_ui_api.py`
(+1 test), `tests/test_ui_static_rules.py` (`TESTIDS` +`trip-list-heading`),
`docs/plans/ui.md` (one sentence, §4.7 tokens). `server.py`, `serialize.py`, `api.py`,
`index.html`, `map.js`, `tests/fixtures/` untouched.

## Fix round 2 + pin cleanup

Two commits on `feature/search-to-trip` from `0d1e2fb`.

### a022cf4 - F5: a run finishing elsewhere no longer wipes the new-trip form

`sendRun` navigated to the run result whenever `S.tripId === id`. `S.tripId` survives on
every other view, so a run returning while the user was typing in the new-trip form
navigated the page away, and `+ New trip` then started a fresh `S.nt`: the typed name,
origin, destination and cabin were gone. The F4 fix's `else if (S.view === "trips")` guard
was on the branch below, so it never protected the form.

One line, as the tester's Location note asks - `if (S.tripId === id && S.view === "trips")`
- plus a five-line comment above it. A run that finishes while the user is anywhere but
the trips view now lands as a chip, exactly as it already did when another trip was open;
`S.nt` is untouched, and so is the search view's own state.

`test_st_f_retest.py::test_F4b` green. `::test_F4a` still green (see the flake note below).
No new string literal, no testid, no CSS, no server change.

### 9dea889 - the brittle pins become behaviour assertions

Agreed with Tsuki: pins on exact bytes, line counts, diff-hunk counts and literal lists
went red on every unrelated change, cost a re-pin round each time, and have never caught a
defect. Each is replaced in place, keeping its id and file; every converted probe carries
the agreed docstring sentence.

| Probe | Pin dropped | Behaviour asserted instead |
|---|---|---|
| st **E2** | `len(JS.splitlines()) == 2087` | unchanged: every banned construct, the `.style.` budget, the CSS/HTML rules |
| st **E3** | `old - new == {"2400"}` | new `testids()` bracket-matches `tid()`/`btn()` argument lists and reads `setAttribute("data-testid")`, `[data-testid="…"]` and the `testid:`/`goid:` keys - 76 real testids in the base, all must survive; the new ones still asserted by name |
| st **E5** | hunk count + "the report's bullet list equals the diff" | deleted (bookkeeping on a document, not on the product); E5b carries the rule |
| st **E5b** | literal string-set for one frozen diff | no sentence-shaped literal exists in app.js that the plans do not write (comments excluded) |
| restyle **H1** | api.py/engine.py bytes pinned to the last head | unchanged: server.py at 16f53b7, the CSP literal, serialize.py/main.py/formatter.py at 386b2fc |
| restyle **E5** | same two byte pins | unchanged: serialize.py, server.py, and `state()`/`calls_state()` byte-identical to the base |
| restyle **H7** | `old - new == {"2400"}` | same `testids()` conversion as E3 |
| restyle **J6** | both app.js hunk counts (6 and 22) | no banned construct; no testid of the base lost; every string literal **new to the file** on an added line still inside the enumerated allow-lists (the left side of each range is subtracted, so a moved line re-emitting an existing literal is not read as new wording) |
| map **A6** | api.py/engine.py byte pins | unchanged: the CLI, the engine's data sources, airports.csv, the goldens |
| map **A7** | "the other probe trees are unchanged since `<commit>`" | dropped entirely - it pinned the tester's own future commits and no product change can touch those paths; the goldens half stays |

Nothing that asserts behaviour, security, a refusal sentence, a marker, layout or data
integrity was touched, and nothing protecting §2 of `docs/design/UI-BRIEF.md` was weakened.
`tests/` was not edited at all.

### Counts

| Suite | Before (at 0d1e2fb + the F5 fix) | After |
|---|---|---|
| full `-O` | 3892 passed, 13 skipped | 3892 passed, 13 skipped |
| full (no `-O`) | 3892 passed, 13 skipped | 3892 passed, 13 skipped |
| search-to-trip-probes | 1 red (E2 line count) | 0 red except the F4a flake below |
| ui-restyle-probes | 2 red (C5[search_ok], E3) + J6/H1/E5 would have gone red on the next commit | 2 red: C5[search_ok], E3 - both documented |
| map-search-probes | 2 red (A7, F6) | 1 red: F6 - documented design limit |
| ui-probes | 0 red (664 passed) | 0 red (664 passed) |

### One red I cannot get green: `test_F4a` is flaky, and was before this round

`test_st_f_retest.py::test_F4a` fails intermittently at line 284 - the first assert of its
second half - with `delete_state(pg)["disabled"] is None`, i.e. `trip-delete` is gone from
the DOM a moment after `wait_for_selector` found it. It is a probe race, not the F5 fix:

- Run in isolation (`-k F4a`), 10 runs each: **3/10 red with `0d1e2fb`'s app.js, 3/10 red
  with the fix.** Identical.
- Run in file context (the whole `test_st_f_retest.py`), 4 runs each: red in both, if
  anything more often on the unfixed app.js.
- A standalone replay of just the second half (fresh page, same clicks) passes every time,
  so what makes it race is the state the first half leaves behind, not the guard.

The second half exercises a trip the run is *not* on, so the branch the F5 fix touched is
not even taken there; the F5 fix only narrows when `go(...)` fires. The probe needs a
settle (`wait_for_function` on `trip-delete` being present *and* disabled) rather than a
single `wait_for_selector`, but that is the tester's file and I have not edited it.

Everything else in that suite, including `F4b`, is green.

**F6 (fix round 2b, `sendRun` non-200 branch).** Smaller of the two shapes the tester
offered: `if (S.view !== "new-trip") { renderTrips(); }` - one line, the same guard shape as
the 200 branch, rather than a `cameFrom`-style save-and-restore, because nothing the
new-trip view renders follows `S.busy`, so skipping the rebuild loses nothing; the banner is
written straight to `#banner-error` by `showBanner` and still shows. `test_st_g_f5.py::test_G5`
now reports `focus INPUT:nt-leg-1-destination` with the 409 banner and all values intact, and
fails only on its own line 254 `assert where == "BODY:null"` - the assertion that RECORDS the
defect, which I have not edited (the tester's file, the tester's call). Everything else green:
`search-to-trip-probes` 175 passed / 1 failed (that line only), full suite `-O` 3892 passed /
13 skipped.
