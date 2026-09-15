# Plan: search → new trip, and a delete button

Branch `feature/search-to-trip`, forked from `feature/map-search` at 4f879d4. Architect's
plan; no application code was written for it. Tsuki said "just decide", so every open
question below is decided in place and marked **D<n>**.

Tsuki's request, verbatim: "connect the search and add trip pages — maybe make it so that
when you search a flight you can click on the flights and maybe there is a button on the
bottom of the search that says add new trip and it will lead to the add-as-trip page with
all of the information it has filled, with the user still needing to input the price; also
I want a delete trip button as well."

## 1. Summary

Two halves, one round.

**A. Search → trip.** A search result cell (date × program × cabin = one award) is already
clickable and opens the drawer, whose `Score against a fare →` button (`drawer-to-trip`)
already prefills the new-trip form with From/To/Date/Cabin and an empty cash field. This
round makes that the *selection*: the picked cell stays highlighted after the drawer
closes, and a second button, `Add as trip` (`search-add-trip`), sits under the results
table and does the same thing from the same selection. The form now arrives with a
suggested name (`sfo-mad-2027-01-15`), focus on the cash field, and a note saying what was
prefilled and that the award price seen in the search is **not** written into the trip.
Nothing about the request body, the engine, the builder or the fixture changes: a trip
built from a search is byte-identical to the same trip typed by hand, and carries no
points price, because `--new-trip` refuses to write one and the UI's writer is that
builder.

**B. Delete trip.** A `Delete trip` button on the trip page, enabled only for fixtures
whose own contents say `--new-trip` (or this page) built them and nobody edited them since.
Trip A/B/C, `trip_001/002` and anything hand-written are refused by name. Two POST routes
behind the existing Host/token/Origin gates plus a single-use confirm token bound to the
file's bytes; the delete takes the run lock, refuses symlinks and anything that does not
resolve inside the trips directory, and unlinks. No trash folder, no git at runtime.

## 2. Assumptions & decisions

What reading the code changed about the brief:

- **The UI's write path is not argv → dispatch.** `engine.trip_create` calls
  `trip_builder.build_fixture` + `write_fixture` directly (ui.md D14), the same functions
  `run_new_trip` calls; `--new-trip` is never dispatched by the UI. The brief's "every write
  still goes argv → build_parser → dispatch → run_new_trip" describes runs, not trip
  writes. This plan adds **no** writer and touches neither `trip_builder.py` nor
  `src/main.py`: the prefill is purely client-side state that lands in the same
  `/api/trips/draft` and `/api/trips/create` bodies a typed form sends.
- **`drawer-to-trip` is already the per-row "add as trip".** It sets `S.nt.cabin` and one
  leg with `cash: ""` and navigates to `#new-trip`. It is kept, not duplicated: both
  buttons call one function.
- **A search has exactly one route.** Multi-select would give N variants of one leg
  (dates/programs of SFO→MAD), not N legs of a trip, and a fixture with the same leg on
  two dates would double the fare in the totals. So selection is one award.
- **The fixture cannot carry provenance without a builder change.** `build_fixture`
  hardcodes `source` and `description`; there is no notes field, and `--new-trip` has no
  flag for one. Adding one would change the builder (interactive == flags == UI
  byte-identity tests, probe E9, the G-new-trip parity gap in to-do #8). Not this round.
- **Deletability is decidable from the file alone.** Every fixture `build_fixture` writes
  has `source` starting `user_entered_via_new_trip`, `trip_level_flags == [LIVE_ONLY_FLAG]`,
  no `points_candidates` key on any leg, and every `cash_options[].source ==
  "user_entered_via_new_trip"`. None of the committed corpus has any of these.
- **DELETE the HTTP method is 405 today and the Origin gate is on POST only.** Using POST
  routes keeps `server.py` byte-identical (restyle-probe H1 pins it against 16f53b7).

Decisions:

| # | Decision | Why / rejected |
|---|---|---|
| D1 | Selection = one award cell, kept in `S.search.picked` separate from the drawer's `S.search.sel`. Click/Enter/Space on a cell sets both; closing the drawer (×, Esc) clears `sel` only; `Show map`, a new run, and a cell click elsewhere replace/clear both. | Multi-select rejected (one route per search). Without `picked`, Esc would lose the selection and a keyboard user could never reach the bottom button with a selection held. |
| D2 | One button under the results table, label `Add as trip`, testid `search-add-trip`, in a small panel `search-add-trip-box` that names the selection. Disabled with the note `Pick an award in the results first.` when nothing is picked. `drawer-to-trip` keeps its label and calls the same function. | "at the bottom of the search" = under the results; it is visible at every width, where the drawer is a sheet below 1180. Renaming `drawer-to-trip`'s text is churn with no user gain. |
| D3 | Prefill = origin, destination (from `run.route`, which echoes the *validated* request), date (the row's `date`), trip cabin = the cell's cabin, leg cabin `""` (= trip cabin, so the form shows `trip cabin (J)`), travellers 1 (fixed already), cash `""`, name suggested `{o}-{d}-{date}` lowercased **only** when the date matches `^\d{4}-\d{2}-\d{2}$`; otherwise the name is left empty. | Route codes are builder-validated before the search ran; the date and program come from Seats.aero and are hostile by assumption - the date goes into an `input.value` and is re-validated by `validate_date` on preview; the program never enters the body. |
| D4 | The award price (miles, taxes) is **never** carried. Not into the body, not into a hidden field, not into the name. The form shows a note (`nt-prefill`) that says so in words. | ui-brief §2; `LIVE_ONLY_FLAG`. A LIVE/REPLAY run re-observes it. |
| D5 | No provenance written into the fixture this round. `S.nt.from_search = {run_id, origin, destination, date, program, cabin}` exists only for the note on the form and is dropped on write. | Builder change rejected (above). If Tsuki wants it, it is a `--new-trip --note TEXT` flag with its own review, golden and parity impact. |
| D6 | The suggested name is not de-duplicated against the list. A second add of the same award hits the writer's own refusal (`… already exists and --force was not given …` + `Choose another name.`). | The writer is the only authority on names; guessing `-2` suffixes invites two trips for one award. |
| D7 | Deletable ⇔ the raw JSON file parses as an object AND `source` is a string starting with `user_entered_via_new_trip` AND `LIVE_ONLY_FLAG` is in `trip_level_flags` AND no leg has a `points_candidates` key AND every `cash_options[].source == "user_entered_via_new_trip"`. Anything else is refused with a named sentence (§4.3). An unloadable file is refused (its provenance cannot be read). | The committed corpus is test data; git removes it. A UI-built file someone edited by hand may hold captures nobody can reproduce (the writer's own words). |
| D8 | Real `os.unlink`, no `.trash/`. The confirm names the file and says there is no undo in this app. | A trash folder inside `tests/fixtures/trips/` is one glob away from being read as a fixture; outside it is a second user-data location to explain. D7 already protects everything that is not the app's own scratch. |
| D9 | No git at runtime. A UI-built trip that has since been committed is deletable; the confirm says git can restore a committed file. | A `git ls-files` dependency in the server is a new runtime dependency on the Mac's git and on the cwd being a checkout. |
| D10 | Routes: `POST /api/trips/{id}/delete-preflight` → confirm; `POST /api/trips/{id}/delete` with `{confirm_id}` → unlink. Both behind token + Origin (POST). The confirm digest is `{kind:"delete", trip_id, fixture_sha256}`, same TTL and single use as run confirms, with delete-specific refusal sentences (the existing `CONFIRM_REQUIRED` text talks about spending calls). | `server.py` stays byte-identical. Reusing the confirm store means no new token machinery. |
| D11 | The delete takes the run lock non-blocking: 409 while any run is in flight, whatever trip it is on. | The engine does not know which trip a run holds open at every moment; "any run" is the honest rule and a run lasts seconds to a minute. The button is also disabled client-side while `S.busy`. |
| D12 | `GET /api/trips/{id}` gains two keys added by `engine.trip_detail` **after** `serialize.fixture_detail` returns: `deletable: bool`, `not_deletable_reason: null|str`. The listing (`GET /api/trips`) is unchanged. | The page needs the reason before the click; `serialize.py` stays untouched. |
| D13 | `Delete trip` is a `.btn-warn` (warn-fill form: `--warn-bg`, `--warn-line`, text `--warn`), never `.btn-primary`. | Coral is the one primary action (ui.md §4.7). Destruction is caution-coloured and its shape (a filled warn button) carries it too. |
| D14 | The delete confirm is its own small dialog builder `openDialog(c, onGo)` (title, lines, go/cancel, testids), not a rework of `openConfirm`, whose heading hardcodes "Seats.aero calls". | Smallest diff; the spend dialog's pinned text stays byte-identical. |
| D15 | `app.js` stays one file (~2,050 lines after this round). | J6/H4/H7 pins are diffs against 386b2fc/56742af of *this* file; splitting it invalidates every one and the static-rules `SCRIPTS` map for a cosmetic gain. Recommend a split in a round of its own. |
| D16 | After a successful delete: `delete S.runs[id]`, `loadTrips()`, `go("#trips")`, and a panel `trip-deleted` shows the server's lines. The engine's in-memory run store keeps its last-20 entries (a stored run of a deleted trip is still a real run that happened). | The trip-empty view is the honest landing; a hash to a deleted trip renders the existing `trip-error` panel with the 404 message. |
| D17 | Selection and the prefilled form survive tab switches: `S.search.picked/sel` live in `S.search`; `S.nt` persists until Cancel or Write, as today. | Existing behaviour; stated so the tester can pin it. |

## 3. Out of scope

Hotel legs in the prefill; more than one traveller; multi-leg prefill from several
searches; editing an existing trip; undo/trash; renaming; a scratch (unsaved) trip;
`--new-trip --note`; the HTTP `DELETE` method; deleting `*_answer.json` or unloadable files;
phone-width work beyond "nothing widens the page"; any wording that is shared with the
CLI; anything that puts a hand-typed number where an observation belongs.

## 4. Architecture

### 4.1 State machine (client)

```
S.search.picked: null | {row:int, cabin:"Y"|"W"|"J"|"F"}     the selection
S.search.sel:    null | {row, cabin}                          the drawer (as today)

cell click / Enter / Space  → picked = sel = {row, cabin}; drawer opens; focus → drawer
drawer × / Esc              → sel = null; picked unchanged; focus → the cell (cameFrom)
click another cell          → picked = sel = the new cell
Show map                    → picked = sel = null (existing toggle handler, extended)
new run completes           → picked = sel = null
Add as trip / drawer-to-trip→ prefillFromSearch(picked) → S.nt built → go("#new-trip")

prefillFromSearch(pick):
  run = S.search.run; row = run.rows[pick.row]; cabin = pick.cabin
  S.nt = newTripState()
  S.nt.cabin = cabin
  S.nt.legs = [{origin: run.route.origin, destination: run.route.destination,
                date: row.date, cabin: "", cash: ""}]
  S.nt.name = isoDate(row.date) ? (run.route.origin + "-" + run.route.destination + "-" + row.date).toLowerCase() : ""
  S.nt.from_search = {run_id: run.run_id, origin, destination, date: row.date,
                      program: row.program, cabin}
  S.nt.focus = "cash"          // renderNewTrip focuses nt-leg-1-cash once, then clears it

new-trip form → Preview → POST /api/trips/draft (body unchanged) → echo → Write →
POST /api/trips/create (body unchanged) → "Wrote …" → #trips/<id>   (all as today)
```

`ntBody()` is untouched: `from_search` and `focus` are never sent.

Delete:

```
#trips/<id> (T2)  →  [Delete trip] enabled iff trip.deletable && !S.busy
   click → POST /api/trips/<id>/delete-preflight
        200 {id, path, confirm_id, description, lines[]} → openDialog(delete-confirm)
        409 not_deletable / busy, 404 → showBanner(message)
   [Delete <file>] → POST /api/trips/<id>/delete {confirm_id}
        200 {id, path, lines[]} → delete S.runs[id]; S.lastDelete = lines; loadTrips(); go("#trips")
        409 confirm_required / confirm_stale / busy / not_deletable, 404 → showBanner(message); re-render
```

### 4.2 Routes (src/ui/api.py)

Two rows appended to `ROUTES`, both POST, both using the existing `_TRIP` group (so an id
can never contain `/`):

| Method, path | Body | 200 | Refusals |
|---|---|---|---|
| `POST /api/trips/{id}/delete-preflight` | `{}` (any object; ignored) | `{id, path, description, confirm_id, lines:[L1, L2]}` where `path` is `display_path(file)`, `description` is the fixture's own `description` string, L1/L2 are §4.4 #C1/#C2 | 404 `not_found` (existing sentence); 409 `not_deletable` §4.4 #R1-#R4; 409 `busy` #R5 |
| `POST /api/trips/{id}/delete` | `{confirm_id}` | `{id, path, lines:[ "Deleted <path>" ]}` | 404; 409 `confirm_required` #R6; 409 `confirm_stale` #R7; 409 `busy` #R5; 409 `not_deletable` #R1-#R4 (re-checked); 409 `not_a_regular_file` #R8/#R9 |

`GET /api/trips/{id}` adds `deletable` and `not_deletable_reason` (D12). Nothing else
changes shape.

### 4.3 Engine (src/ui/engine.py)

New module constants (verbatim strings in §4.4): `DELETE_CONFIRM_REQUIRED`,
`DELETE_CONFIRM_STALE`, `DELETE_BUSY`.

```
def deletable_reason(self, path: Path) -> Optional[str]:
    # None = deletable. Reads the RAW json, never the loaded object: the rule is
    # about what is literally in the file (an absent points_candidates KEY).
    lstat: symlink → R8
    json.loads(read_bytes) fails or not a dict → R1
    source = d.get("source"); not str or not source.startswith("user_entered_via_new_trip") → R2
    LIVE_ONLY_FLAG not in d.get("trip_level_flags", []) → R3
    any "points_candidates" in leg for leg in d.get("legs", []) if dict → R3
    any c.get("source") != "user_entered_via_new_trip" for every cash_options entry → R3
    return None

def _delete_digest(self, trip_id, path) -> str:
    canonical_digest({"kind": "delete", "trip_id": trip_id,
                      "fixture_sha256": sha256(path.read_bytes())})

def trip_delete_preflight(self, trip_id, body) -> dict:
    path = self.trip_path(trip_id)                       # current listing only; 404 otherwise
    if self._lock.locked(): raise ApiError(409, "busy", DELETE_BUSY)   # advisory; re-checked at delete
    reason = self.deletable_reason(path)
    if reason: raise ApiError(409, "not_deletable", reason)
    description = json.loads(path.read_bytes()).get("description")   # str or None
    return {"id": trip_id, "path": display_path(path), "description": description,
            "confirm_id": self.issue_confirm(self._delete_digest(trip_id, path)),
            "lines": [C1.format(path=display_path(path)), C2]}

def trip_delete(self, trip_id, body) -> dict:
    path = self.trip_path(trip_id)
    self.redeem_confirm(body.get("confirm_id"), self._delete_digest(trip_id, path),
                        required=DELETE_CONFIRM_REQUIRED, stale=DELETE_CONFIRM_STALE)
    if not self._lock.acquire(blocking=False): raise ApiError(409, "busy", DELETE_BUSY)
    try:
        reason = self.deletable_reason(path)             # again, under the lock
        if reason: raise ApiError(409, "not_deletable", reason)
        if path.is_symlink(): raise ApiError(409, "not_a_regular_file", R8)
        if path.resolve().parent != self.trips_dir or not path.is_file():
            raise ApiError(409, "not_a_regular_file", R9)
        os.unlink(path)                                  # FileNotFoundError → 404 (raced)
    finally:
        self._lock.release()
    return {"id": trip_id, "path": display_path(path), "lines": [f"Deleted {display_path(path)}"]}
```

`redeem_confirm` gains two keyword arguments `required=CONFIRM_REQUIRED,
stale=CONFIRM_STALE` used in its two raises; existing callers are unchanged.
`trip_detail` becomes:

```
out = serialize.fixture_detail(...)
reason = self.deletable_reason(path)
out["deletable"] = reason is None
out["not_deletable_reason"] = reason
return out
```

The `trips_dir` used for the parent check is `self.trips_dir`, already `.resolve()`d in
`__init__`. Nothing in these functions joins anything from the URL: `trip_path` is the
listing lookup that already exists.

### 4.4 New user-facing strings (verbatim; the tester pins these)

None of these is shared with the CLI. `{file}` is `display_path(path)` (e.g.
`tests/fixtures/trips/sfo-mad-2027-01-15.json`); `{dir}` is `display_path(trips_dir)`;
`{source}` is the first 80 characters of the file's `source` value.

Server refusals (engine.py):

- **R1** `NOT DELETABLE - {file} cannot be read as a trip, so where it came from cannot be checked. Nothing was deleted; remove it by hand if you know what it is.`
- **R2** `NOT DELETABLE - {file} was not built by --new-trip or this page (source: "{source}"). The trips that came with the repository are test data; remove them with git, not from here. Nothing was deleted.`
- **R3** `NOT DELETABLE - {file} was built by --new-trip but has been edited since: it carries points prices, a cash source it did not write, or the flag that says it holds no points prices is gone. It may hold captures nobody can reproduce. Nothing was deleted; remove it by hand if you mean to.`
- **R5** `Nothing was deleted: a run is in progress and may be reading this trip. Wait for it to finish, then delete.` (`DELETE_BUSY`)
- **R6** `Nothing was deleted: a delete needs a confirmation from its own preflight, and this request carried none or one that was already used.` (`DELETE_CONFIRM_REQUIRED`)
- **R7** `Nothing was deleted: the trip file changed since you confirmed, or the confirmation expired. Confirm again.` (`DELETE_CONFIRM_STALE`)
- **R8** `Nothing was deleted: {file} is a symbolic link, and this page only deletes the trip files it wrote.`
- **R9** `Nothing was deleted: {file} does not resolve to a regular file inside {dir}.`

(R4 is the existing 404 `No trip {id!r} in {dir}.` from `trip_path`, which a second delete
of the same id returns.)

Server confirm lines (preflight `lines`):

- **C1** `This removes {file} from disk.`
- **C2** `There is no undo in this app. If the file is committed, git can restore it; if it is not, it is gone.`

Page (app.js):

- **P1** button `Add as trip` (`search-add-trip`)
- **P2** note when nothing is picked: `Pick an award in the results first.` (`search-add-trip-note`)
- **P3** note when picked: `Picked: {ORIGIN} → {DEST} · {date} · {program} · {cabin}` (`search-add-trip-note`; uses the existing `routeEl`; program falls back to `(program not named)` exactly as the table does)
- **P4** prefill panel on the form (`nt-prefill`): `Prefilled from the search {ORIGIN} → {DEST} · {date} · {program} · {cabin}. The award price the search showed is NOT written into this trip: only a LIVE or REPLAY run can price it. Type the cash fare you found - the search cannot know it.`
- **P5** cash hint under `nt-leg-1-cash` when prefilled: `required: the one thing a search cannot know`
- **P6** button `Delete trip` (`trip-delete`)
- **P7** the reason line under a disabled Delete (`trip-delete-reason`): `not_deletable_reason` verbatim (R1/R2/R3)
- **P8** dialog (`delete-confirm`): label `Before anything is removed`, heading `Delete {file}?`, then the preflight `lines` verbatim, then the fixture `description` verbatim (when not null), buttons `Cancel` (`confirm-cancel`, reused testid) and `Delete {file}` (`delete-confirm-go`)
- **P9** panel after a delete (`trip-deleted`): the server's `lines` verbatim (`Deleted {file}`)

No marker word from ui-brief §2 is used in a new sense; `NOT DELETABLE` is a new
uppercase marker in the refusal register and is rendered in the warn-fill refusal form.

### 4.5 Testids

Reused: `cell-<date>-<source>-<cabin>`, `drawer-to-trip`, `new-trip-form`, `nt-name`,
`nt-cabin`, `nt-leg-1`, `nt-leg-1-origin/-destination/-date/-cabin/-cash`, `nt-echo`,
`nt-write`, `nt-wrote`, `trip-detail`, `trip-empty`, `trip-error`, `confirm-cancel`,
`banner-error`.

New: `search-add-trip`, `search-add-trip-box`, `search-add-trip-note`, `nt-prefill`,
`trip-delete`, `trip-delete-reason`, `delete-confirm`, `delete-confirm-go`,
`trip-deleted`. Nothing renamed, nothing dropped (restyle-probe H7).

### 4.6 Files

| File | Change | Size reasoning |
|---|---|---|
| `src/ui/api.py` | +2 `ROUTES` rows, +2 handlers, +2 `_HANDLERS` entries | ~12 lines |
| `src/ui/engine.py` | 3 constants; `deletable_reason`, `_delete_digest`, `trip_delete_preflight`, `trip_delete`; `redeem_confirm` kwargs; `trip_detail` +3 lines; `import os` | ~95 lines |
| `src/ui/static/app.js` | `S.search.picked`; `renderSearchTable` highlight from `picked`, `open` sets both; `closeSearchDrawer` clears `sel` only; pane toggle and `doSearch` completion clear both; `renderSearchResult` adds the add-as-trip box after the table; `prefillFromSearch`; `drawer-to-trip` calls it; `renderNewTrip` renders `nt-prefill`, the cash hint and the one-shot focus; `renderTripHead` renders the Delete button + reason; `doDeletePreflight`, `doDelete`, `openDialog`; `renderTrips` shows `trip-deleted` on the empty view; `onHash`/tab handler unchanged | ~150 lines net → ~2,050. No new `.style.` use (H4 pins the count at 2). No `innerHTML`, no `\|\| 0`/`?? 0`. |
| `src/ui/static/app.css` | `.btn-warn` (3 rules), `.addtrip` box, `td.cab.sel` unchanged (now driven by `picked`), `.nt-prefill` note | ~12 lines; every addition listed in the coder report (restyle-probe J8 lists undisclosed CSS changes) |
| `tests/test_ui_api.py` | delete section (§6 steps 3-5) | ~150 lines |
| `tests/test_ui_static_rules.py` | `TESTIDS` += the nine new ids | 3 lines |
| `tests/test_ui_security.py` | `test_other_methods_are_405` unchanged; add: the two new POST paths need token and Origin (parametrize the existing origin test over them) | ~10 lines |
| `docs/plans/ui.md` | §4.5 table +2 rows and the detail keys; §4.8 S4/S5 (selection, `search-add-trip`), T2 (`trip-delete`), T5 (`nt-prefill`, name suggestion) | prose |
| `README.md` "Local UI" | one sentence in "What you can do" for add-as-trip; one paragraph for delete (what is deletable, no undo) | prose; `test_readme_local_ui_is_accurate` needles all still present |
| `docs/test-reports/ui-probes/…` | the tester's, not the coder's | — |

Untouched, and asserted so: `src/ui/server.py`, `src/ui/serialize.py`, `src/main.py`,
`src/formatter.py`, `src/trip_builder.py`, `src/trip_loader.py`, `src/ui/static/map.js`,
`index.html`, every golden, every fixture under `tests/fixtures/`.

## 5. Tech choices

| Choice | Why | Rejected |
|---|---|---|
| POST routes for delete | `server.py` byte-identical; Origin gate already on POST; `Only GET and POST are accepted.` stays true | HTTP `DELETE` (new method gate, new Origin rule, new server pin) |
| Confirm token from the run store, delete-specific sentences | one token mechanism; single use, TTL, bound to bytes already proven | client-side `confirm()`; a second lock |
| Provenance from the raw JSON | the rule is about literal keys (`points_candidates` absent); the loader normalises them away | `load_trip_fixture` output; git |
| `os.unlink` under the run lock | one line, one syscall, no second location | `.trash/` (globbed by the suite or a second data dir) |
| Client-only prefill | body unchanged ⇒ fixture byte-identical to typed; no server change ⇒ no builder change | a `/api/trips/draft?from_run=` server-side prefill (a new code path with nothing to validate that the client does not already send) |
| One `openDialog` for delete | `openConfirm`'s heading is the spend sentence; keep its bytes | generalising `openConfirm` |

## 6. Build steps

Do not run the full suite until step 9. `tests/test_ui_static_rules.py`,
`tests/test_ui_security.py` and `tests/test_ui_api.py -k delete` are cheap and run after
each step. No step touches `tests/fixtures/`; every delete test runs in a `copy_trips`
tmp directory.

1. **Selection state + Add as trip (app.js, app.css).** D1, D2, D3 (`prefillFromSearch`,
   the name suggestion with the ISO guard), the box under the table, `drawer-to-trip`
   calling the shared function, `TESTIDS` in the static-rules test.
   *Accept*: static rules green; in a browser (the coder's own check, not a committed
   probe): click a cell → drawer opens, cell highlighted, box reads P3; Esc → drawer closes,
   cell still highlighted, box still P3, focus on the cell; `Add as trip` → `#new-trip` with
   `nt-name` = `sfo-mad-2027-01-15`, `nt-cabin` = the cell's cabin, leg 1 From/To/Date
   filled, leg cabin select on `trip cabin (…)`, cash empty and focused; `Show map` clears
   the highlight and disables the button with P2.
2. **Prefill note + cash hint (app.js).** D4, D5: `nt-prefill` (P4), P5 under the cash
   input, focus once. `ntBody()` unchanged.
   *Accept*: Preview from the prefilled form with cash empty → the builder's own `Leg 1
   cash …` refusal inline; with `2400` → echo identical to the same trip typed by hand
   (compare `nt-echo` text); Write → `Wrote …` and the T2 page; `git diff` of the written
   file against one written by `python -m src.main --new-trip sfo-mad-2027-01-15 --leg
   SFO:MAD:2027-01-15:2400 --cabin Y` on the same pinned day is empty. Add
   `tests/test_ui_api.py::test_a_prefilled_body_is_the_typed_body`: the JSON body the page
   would send (constructed in the test from the G9 search JSON the way `prefillFromSearch`
   does) posted to `/api/trips/create` writes bytes equal to `trip_builder.new_trip_from_flags`.
3. **Engine: `deletable_reason` + `trip_detail` keys.** D7, D12; strings R1-R3.
   *Accept*: `tests/test_ui_api.py`: Trip A/B/C and `trip_001` → `deletable False` with R2;
   a fixture written by `write_fixture` → `True`; the same file with a `points_candidates:
   []` key added → R3; with `source` rewritten → R2; with the flag removed → R3; a
   `cash_options[0].source` of `screenshot` → R3; `{not json` → R1; a symlink → R8.
4. **Routes + preflight + delete (api.py, engine.py).** D8-D11; `redeem_confirm` kwargs;
   security-test parametrisation over the two new paths.
   *Accept*: preflight of a UI-built trip → 200 with a `confirm_id`, C1/C2, the
   description; delete with it → 200, file gone, `GET /api/trips` no longer lists it, the
   listing still has Trip B; delete again → 404 R4; delete of `trip_b_europe` → 409 R2 and
   its bytes unchanged (mtime too); delete without `confirm_id` / with a forged one / with a
   used one → 409 R6 and the file present; preflight, then append a byte to the file, then
   delete → 409 R7; preflight, `time.monotonic` +301 s, delete → R7; preflight while a run
   holds the lock (the existing `test_one_run_at_a_time` pattern) → 409 R5; delete
   without `Origin` → 403 (server test); without the token → 403; a symlink in the trips
   dir pointing at a file outside it → 409 R8 and the target intact; `../` and `%2e%2e` ids
   → 404 (existing test, extended with the `/delete` suffix); a trip id that is a
   directory named `x.json` → not listed (existing `is_file` rule) → 404.
5. **Trip page: Delete button, dialog, result (app.js, app.css).** D13, D14, D16; P6-P9.
   *Accept*: on Trip B the button is disabled with P7 = R2 under it; on a UI-built trip it
   is enabled; click → `delete-confirm` with `Delete <file>?`, C1, C2, the description;
   Esc/Cancel → nothing sent (network log); `Delete <file>` → the list re-renders without
   it, `#trips` shows `trip-deleted` with `Deleted <file>`, the run chips of that trip are
   gone from `S.runs`; a hash to the deleted trip → `trip-error` with R4; while `run-busy`
   is shown the button is disabled.
6. **Docs.** ui.md §4.5/§4.8; README; `tests/test_readme_local_ui_is_accurate.py` green;
   no `docs/plans/`, `vN`, `Step N` strings in `src/ui/static/*`.
7. **Pins this round knowingly invalidates - enumerate in the coder report, do not edit
   the probes.** The tester re-pins:
   - restyle-probes `test_H1_server_and_csp_are_byte_identical_to_the_base`: the first
     assertion (`386b2fc..HEAD` diff of `api.py`, `engine.py` empty) is now false; the
     `server.py`/CSP assertions stay true and must.
   - restyle-probes `test_J6_app_js_diff_is_exactly_the_three_enumerated_edits`: the
     `56742af..HEAD` hunk count and string allowlist. The coder report lists every new
     string literal in app.js (P1-P9, the testids in §4.5, class names `btn-warn`,
     `addtrip`, `nt-prefill`, state keys `picked`, `from_search`, `focus`) so the re-pin is
     a copy, not a hunt.
   - restyle-probes `test_J8_undisclosed_css_changes_the_coder_did_not_list`: the app.css
     additions, listed.
   - `tests/test_ui_static_rules.py::TESTIDS` (coder's own edit, step 1).
   - ui-probes `test_ui_b_security.py` route/method tables if they enumerate `/api/trips/`
     POST paths (check `test_B*`); map-search probes `test_ms_h_*` traversal list: add the
     `/delete` suffix cases.
   - The `ROUTES` table in ui.md §4.5 and the README route sentence.
   Not invalidated, and asserted: server.py byte pin; every golden; the 69-scenario parity
   suite; E9 fixture byte-identity; H4 `.style.` count; H7 testid survival.
8. **Coder report** with the string list from step 7, the CSS list, and the line count.
9. **Verification.** Full suite and under `-O`; the four probe suites by test id with the
   step-7 re-pins expected red until the tester re-pins them; then the tester's round.

## 7. Testing strategy (what the tester attacks)

- **Delete path traversal.** Ids `../trip_b_europe`, `..%2f..%2fsrc%2fconfig`,
  `trip_b_europe%00`, `trip_b_europe.json`, a 121-char id, a directory `x.json` in the
  trips dir, a symlink `link.json → ../../README.md`, a symlink to a *deletable* file
  outside the dir, a hard link to Trip B (provenance says R2 - and a hard link to a
  UI-built file outside the dir is deletable by design: the inode survives; note it).
  Assert: nothing outside the tmp trips dir changes (walk the tmp tree with mtimes before
  and after), the repo's `tests/fixtures/trips/` is never touched (mtime of the directory).
- **Delete during a run.** Start a LIVE run against a stub with a slow transport (the
  `test_one_run_at_a_time` barrier), preflight and delete the *same* trip and a
  *different* trip while it runs → both 409 R5; after the run, the delete succeeds and the
  run's stored JSON is still served by `GET /api/runs/{id}`.
- **Delete of the committed corpus.** Every committed fixture and both `_answer` files
  (which are not listed → 404) → refused, bytes identical. A copy of Trip B whose `source`
  is edited to start with `user_entered_via_new_trip` but which carries `points_candidates`
  → R3. A fixture built by `write_fixture` then committed in a tmp git repo → deletable
  (D9, by design; assert the sentence C2 in the preflight).
- **Double delete / races.** Two threads redeem one confirm → exactly one 200; the other
  R6 or R4. Preflight twice → two confirms, the second delete after the first succeeds →
  R4 (file gone) not R7.
- **Forged / expired confirms.** A run confirm's id used on delete → R7 (digest
  mismatch); a delete confirm's id used on `/run` → `confirm_stale`; an id from a previous
  server launch → R6; TTL boundary at 300 s.
- **Gates.** Missing/foreign Origin, missing/wrong token, `Content-Type: text/plain`, 65 KB
  body, `OPTIONS`/`DELETE`/`PUT` on the two new paths → 405 with `Only GET and POST are
  accepted.`; the request log is method-path-status only; no body ever carries the key.
- **Prefill from a hostile search row.** The `hostile` probe server's rows: `date` =
  `<img src=x onerror=…>`, `2027-13-45`, U+202E, 5,000 chars; `program` hostile. Assert:
  `nt-name` empty when the date is not ISO; the date input's `.value` is the literal
  bytes; P3/P4 render as text (`window.__pwned` false, no `img`); Preview → the builder's
  own date refusal; `nt-cabin` is always one of Y/W/J/F.
- **Byte identity.** Prefill → edit cash → Write vs. typed form vs. `--new-trip` flags on
  the pinned day: three identical files; the JSON body sent by the page (intercept the
  request in Playwright) has exactly the keys `name, cabin, legs[{origin, destination,
  date, cabin, cash}]` (+ `draft_hash` on create) and nothing from `from_search`.
- **The 22 `--new-trip` refusals through the prefilled form.** Drive each
  (`test_trip_builder.py`'s list) by editing the prefilled fields and Preview/Write; each
  refusal appears verbatim inline and nothing is written. Cash `0`, `-1`, `1e9`, `abc`,
  `2,400` in particular.
- **No points price, ever.** After a prefilled write: `grep -c points_candidates` on the
  file is 0; `GET /api/trips/{id}` shows `live_only True`; `LIVE_ONLY_FLAG` in the flags
  panel; OFFLINE run says `this trip has no points prices…` and the headline is the F-2
  `0.00%  (none)` with its qualifier (unchanged behaviour, must still hold).
- **The trip list after delete.** The row is gone without a reload; the deleted trip's
  run chips are gone; the `trip_b_europe` default selection on a fresh load still works;
  deleting the trip that is `aria-current` lands on `trip-empty` + `trip-deleted`.
- **Selection and keyboard.** Tab to a cell, Space → drawer; Esc → focus back on the cell,
  highlight kept, `search-add-trip` enabled; Tab to it, Enter → the form with focus in
  cash; Trips tab → Search tab → the highlight and P3 are still there; `Show map` clears
  them; a new run clears them; the cabin filter `search-cabin` hiding the picked cabin
  keeps `picked` (the box still names it) - decide whether that is a defect and file it.
- **Widths.** 400 / 899 / 1180 / 1440: the box does not widen the page; at <1180 the
  drawer sheet covers the box but `drawer-to-trip` is reachable; the delete dialog fits at
  400 with a 120-char file name (`overflow-wrap: anywhere`).
- **Static rules.** No `innerHTML`, no `.style.` beyond 2, no `|| 0`/`?? 0`, no
  `javascript:`, no changelog strings; every new testid unique (`test_ui_round1_lows`).

## 8. Open risks

- **D11's "any run" lock is coarse.** A minute-long LIVE run blocks every delete. Fine
  today (one user), stated in R5.
- **A UI-built trip Tsuki later hand-edits *without* touching the three checked
  properties** (e.g. changes a cash amount) stays deletable. The check is provenance, not
  a checksum; the confirm names the file and quotes its description, which is the
  mitigation. A stricter rule (re-derive the fixture from its own fields and compare) is
  possible later and needs the builder's `today`.
- **The name suggestion can collide with the CLI's own naming habits** (`sfo_lhr_jan`).
  The writer refuses; the user renames. D6.
- **`picked` vs `sel` is two pieces of state for one cell.** Small, but the tester's
  state-machine probes should try every ordering (§7).
- **The delete confirm's `description` line is fixture text rendered verbatim.** It is
  the builder's own sentence for UI-built files (the only deletable kind), but it is still
  file content: `textContent` only, as everything is.
- **Probe re-pins.** Three restyle-probe tests and possibly two security tables go red by
  design until the tester re-pins them; the coder report must make that a copy job.

## 9. The 3 decisions most likely to be wrong

1. **D1/D2: single selection and the button under the results.** Tsuki said "click on the
   flights" (plural) and "a button on the bottom of the search". If he meant "tick several
   awards, build one trip with several legs", this design refuses that on purpose (one
   route per search). Reversal: `picked` becomes a list and `prefillFromSearch` emits one
   leg per pick; the fixture side needs nothing. Half a day, no server change.
2. **D7/D8: provenance-gated hard delete, no trash.** If Tsuki wants to delete a broken or
   hand-made file from the page, R1/R2 will annoy him; if he deletes a UI-built trip he
   had a good run on, it is gone. Reversal for the first: widen the rule to "anything not
   git-tracked" (D9 flips, git at runtime); for the second: move to
   `data/deleted-trips/` instead of unlink (one line, plus a README sentence).
3. **D5: no provenance in the fixture.** The trip built from a search says only "Built by
   --new-trip on …" and forgets which search it came from. If that matters, it is a
   `--new-trip --note TEXT` flag, a builder change with byte-identity and golden impact,
   and its own round.
