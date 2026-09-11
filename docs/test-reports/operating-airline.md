# Tester report: `feature/operating-airline` (round 1)

Branch `feature/operating-airline` at `99de884` (build `843a929..99de884` on
master `a17497d`, plan `5b3a9b5`). Tester pass, 2026-09-11. Everything below was
run, not inferred. I changed no application code and no existing test.

**No network call was made.** Every live path ran against a URL-routing stub in
place of `requests.get`. Every probe also arms a socket canary (`net_canary` in
the probe conftest) that records and refuses any real `connect` /
`create_connection`. It recorded zero attempts across the whole suite, in both
modes.

Probes: `docs/test-reports/operating-airline-probes/` (outside `testpaths`). Each
probe asserts the CORRECT behaviour, so a RED probe is a defect that exists and a
GREEN probe is an attack that held up.

```
.venv/bin/python -m pytest -q -p no:cacheprovider docs/test-reports/operating-airline-probes
```

Recorded state: **36 red / 260 green**, with the same red set under `python -O`.
The probes use only pytest features that exist in 7.4.0 (fixtures, monkeypatch,
tmp_path, capsys, parametrize). I also scanned the Coder's new tests and found no
feature newer than 7.4.0.

## Baseline checks (finding #1 would have been a failing suite; there is none)

| Check | Result |
|---|---|
| `pytest -q` | **1362 passed, 13 skipped** (the Coder's claim holds) |
| `python -O -m pytest -q` | **1362 passed, 13 skipped** |
| v5-probes red set | 19, identical by id to `baseline/v5-probes.red` |
| adversarial-probes red set | 40, identical by id |
| known-failures-probes red set | 0 |
| Tree after the full suite | clean. No test writes into the repo |
| Existing tests edited | none. The only modified file under `tests/` is `tests/fixtures/seats_aero/README.md` |

## Summary

| # | Sev | Title |
|---|---|---|
| 1 | **High** | The YQ loader accepts a record whose own verdict line says the opposite (or "inconclusive") of the CSV row it backs |
| 2 | Medium | A yq-check record for one source backs a verdict for a different source |
| 3 | Medium | Some lines derived from the trips parse print with no UNVERIFIED label and no "marketing carrier" caveat: the same-metal alternatives and the yq-check comparison block |
| 4 | Medium | The availability-id check accepts a trailing newline. That id sends a request, splits the trips manifest row, and makes every later replay of the corpus refuse |
| 5 | Medium | `trips_coverage` reads fewer pagination signals than the search transport does, so a list the response calls partial comes out KNOWN |
| 6 | Low | The stored `incomplete` flag (the way-nine union) is computed and then thrown away by the metal pass |
| 7 | Low | A `count` larger than the `data` list is not treated as incomplete |
| 8 | Low | The cap stops counting when an exception outside `TripsLookupError` escapes after the request was sent |
| 9 | Low | An HTTP 429 on a search does not stop the trips lookups; the README overstates what 429 does |
| 10 | Low | Some lookups are paid for and their result is printed nowhere: 2-traveller legs in auto mode, and awards that are not the chosen one |
| 11 | Low | A single-carrier policy award now prints "Nothing is known about which airline flies it" while the model treats that carrier as known metal |
| 12 | Low | A truncated trips MANIFEST.md, or any file that is not a manifest, is read as "no lookups recorded" |
| 13 | Low | The replay transport never checks that the envelope it opens is a lookup of the requested id |
| 14 | Low | Admitted replay gaps (duplicate id: the later row wins; a row for a leg not in the trip is still used). They are as small as claimed |
| 15 | Low | Admitted gaps (cap checked before cache; a 429 also skips cached lookups) are as small as claimed. The README still says "A cache hit is free" and lists neither gap |
| 16 | Low | The label-flip checks are weaker than the README says (a forged capture passes, an inferred-route capture passes, and the cents flip checks no provenance) |
| 17 | Low | `capture` promises "at most 2" calls, but the search it runs can follow pagination |
| 18 | Low | `capture` builds its filename from unsanitized airport codes in the API response; a `/` crashes it after the trips call is spent |
| 19 | Low | `capture` with a closed stdin exits with an EOFError traceback |
| 20 | Low | `yq-check` offers a row tax figure the tool itself does not believe (negative, or below UK APD) for comparison |
| 21 | Low | `MixedCabinPct > 0` under `min_cabin_pct=100` is not recorded as drift (D16) |
| 22 | Low | `--trips-cap ²` gives "invalid literal for int()" instead of the named reason |

No Critical. **1 High, 4 Medium, 17 Low.**

Headline: I could not make any lookup outcome move a verdict, score, floor,
break-even, trip range, margin, withholding or exit code with the committed
header-only `data/yq_inclusion.csv`, and none of the 24 lookup endings I produced
prints a carrier, "no trips" or "no flights". The weak spots are the first
**YQ verdict**, which is the only thing that will let metal move money, and the
pieces around it: the loader (1, 2), the comparison block (3, 20) and the flip
checks (16).

---

## Findings

### 1. High: a YQ record that says `excludes_yq` or `inconclusive` backs an `includes_yq` row

**Repro:**
`test_oa_i_legacy_and_misc.py::test_a_record_whose_own_verdict_disagrees_with_the_row_is_refused[excludes_yq]`
and `[inconclusive]`. Write a record under `docs/yq-checks/` with the marker, no
`____`, and the line
`- verdict (includes_yq / excludes_yq / inconclusive): excludes_yq`. Then add
`virginatlantic,includes_yq,2026-09-10,<that record>,x` to the CSV and call
`yq_inclusion.load`.

**Expected:** `YqInclusionError`. The record itself says the opposite, or says
nothing can be recorded. **Actual:** the table loads, and every trusted
Virgin Atlantic award scores at taxes only (case B, no band). That undercount is
the v0 bug.

**Why High:** the module's own docstring says "A wrong row here moves scores, so
a mistake must stop the run rather than score quietly." Deviation 5 makes this
exact mistake easy: `yq-check` prints **both** candidate CSV rows one above the
other ("add EXACTLY ONE of these"), and the loader never compares the row it gets
with the verdict line in the record the tool wrote. Copying the wrong line of two
adjacent lines is a realistic input.

**Location:** `src/yq_inclusion.py` `_check_evidence` (it checks existence,
directory, marker and blanks only). `src/trips_tools.py` `run_yq_check`, lines
651-657.

### 2. Medium: a record for one source backs a verdict for another

**Repro:** `test_oa_d_numbers.py::test_a_record_for_one_source_cannot_back_a_verdict_for_another`.
One `virginatlantic` record is the evidence for both a `virginatlantic` row and a
`flyingblue` row.

**Expected:** refused. D23's undercount guard is "no source leaves E without a
row backed by evidence", and the record names its source in its title and
`program:` line. **Actual:** it loads, and Flying Blue scores move. This is
option (b) of T1, "generalise one check to every source", which the plan
recommends against, done by copy-paste.

**Location:** `src/yq_inclusion.py` `load` / `_check_evidence`.

### 3. Medium: some parse-derived claims carry no UNVERIFIED label and no marketing-carrier caveat

**Repro:**
- `test_oa_d_numbers.py::test_an_alternative_built_from_trips_metal_carries_the_unverified_caveats`.
  A VS award (row "VS, AF, DL") with a KNOWN `AF83` itinerary. `print_alternatives`
  prints "Air France-KLM Flying Blue on AF metal ... Air France-KLM Flying Blue can
  ticket AF metal and carries $75-$125". The line has no UNVERIFIED label and does
  not say "by flight number" or "marketing".
- `test_oa_g_tools.py::test_the_yq_block_carrier_line_carries_the_unverified_label`.
  The `yq-check` comparison block, which is the part Tsuki reads against the
  airline's site and which is copied into the evidence record, prints
  "flight-number carrier: VS" and the flights with no label. The drift report
  above it does carry the label.

**Expected:** every line derived from the trips parse carries the label.
`src/seats_trips.py`'s docstring promises this: "every line derived from this
module carries a label". **Actual:** these two places drop it. Step 11 routed
trips metal into alternatives, but the alternative's note was never updated.

**Location:** `src/alternatives.py` lines 140-146 (the note).
`src/trips_tools.py` `_yq_block`, lines 514-535.

### 4. Medium: the availability-id check accepts `"<id>\n"`

**Repro:** `test_oa_b_parser.py::test_an_id_with_a_trailing_newline_is_refused[...]`
(two ids) and
`test_oa_e_cache_keys.py::test_a_newline_id_builds_no_request_and_no_manifest_row`.
`AVAILABILITY_ID_RE = ^[A-Za-z0-9]{10,64}$` is used with `re.match`, and Python's
`$` also matches before a final `\n`. `seats_client.parse_availability_row` keeps
`row.get("ID")` verbatim.

**What happens end to end** (a stub row whose `ID` ends in `\n`, run with a tmp
cache and corpus):
1. A trips request is sent to `.../trips/<id>\n` and one call is spent.
2. The live line reads `AVAILABILITY_ID_MISMATCH`, because the parser strips the
   itinerary's id but not the requested one.
3. The trips manifest row is written across two lines
   (`| ... | trips:<id>` / ` | 2027-01-27 | ...`).
4. **Every later `--from-snapshot` of that corpus exits 1** with three garbled
   problems (`trip_id_unknown`, `snapshot_missing .../trips_endpoint/trip_b_europe`,
   `route cell '0'`) until someone hand-edits the manifest.

**Expected:** "The id is validated ... before any URL is built. A failure is
NOT_LOOKED_UP (AVAILABILITY_ID_INVALID), and no request is made" (§4.3).
**Actual:** as above. It needs an unusual id from Seats.aero (or one typed into
`capture --availability-id`). The check is the plan's designated guard against
path injection, and this is the hole in it.

**Location:** `src/seats_trips.py` lines 87 and 125-126. `re.fullmatch` or `\Z`
would close it.

### 5. Medium: a paginated trips list can read KNOWN when the search transport would call it incomplete

**Repro:** `test_oa_b_parser.py::test_pagination_signals_the_search_transport_honours_are_honoured_here[hasMore-1|hasMore-str1|has_more-yes|skip-str]`.
A VS itinerary plus `hasMore: 1`, `hasMore: "1"`, `has_more: "yes"` or
`skip: "100"` gives **KNOWN (VS)**.

**Expected:** INCOMPLETE. The search transport reads `hasMore` through
`_as_bool` (True for 1, "1", "yes") and `skip` through `_as_int`.
`trips_coverage` accepts only `True` or `"true"` and an int `skip`. So the same
signal marks a search INCOMPLETE and leaves a trips list "complete". This is the
"KNOWN on partial data" class the plan (§7 item 3) asked me to attack. The shapes
are unusual for a JSON API, which is why this is Medium and not High.

**Location:** `src/seats_trips.py` `trips_coverage`, lines 288-311.

### 6. Low: the way-nine union of stored and recomputed coverage is computed and then dropped

**Repro:** `test_oa_e_cache_keys.py::test_a_cache_entry_that_records_incomplete_never_reads_known`.
A cached trips envelope whose `_meta` records `incomplete: true` over a clean
payload is served from cache (0 requests) and reads **KNOWN**.

`trips_raw` (and `SnapshotTransport.trips_raw`) put the union into
`RawTripsResult.incomplete` ("neither can answer complete for the other").
`metal_pass.from_entry` then ignores that field and re-parses only the payload.
Today the two can differ only if the meta is edited or a future `trips_coverage`
changes, so this is Low. The guard exists, but nothing reads it.

**Location:** `src/live_trip.py` `metal_pass.from_entry`, which never reads
`entry.incomplete`.

### 7. Low: a `count` larger than the list is not incomplete

**Repro:** `test_oa_b_parser.py::test_a_count_larger_than_the_list_is_incomplete`.
`{"data": [1 itinerary], "count": 3}` gives KNOWN. `count` is in
`PAGINATION_KEYS` and appears in the drift report, but it never marks the list
incomplete. The plan did not require this and the search path ignores `count`
too, hence Low.

### 8. Low: the cap stops counting when an unexpected exception escapes after sending

**Repro:** `test_oa_c_budget.py::test_a_request_that_fails_outside_the_named_errors_still_counts_against_the_cap`.
`.json()` raises `TypeError`. With `trips_cap=1` and 3 qualifying ids, **3
requests are sent**. The budget counter moved each time, but `state["sent"]` did
not, because the `except Exception` branch in `fetch` never increments it. Real
`requests` errors are all mapped, so this needs an odd exception, hence Low.

**Location:** `src/live_trip.py` `metal_pass.fetch`.

### 9. Low: a 429 on a search does not stop the lookups

**Repro:** `test_oa_c_budget.py::test_a_search_429_stops_trips_requests_in_the_same_run`.
B4's search gets HTTP 429, and phase 2 still sends 2 trips requests.

The README's Known limitations say "Seats.aero's real limit shows up as HTTP 429,
which stops further lookups". Only a 429 on a trips request does that. At worst
this wastes one extra request per run (the first trips request then gets its own
429), hence Low.

### 10. Low: some lookups are paid for and then printed nowhere

**Repro:**
- `test_oa_d_numbers.py::test_a_couple_leg_that_can_never_be_scored_spends_no_trips_call`.
  B4 with 2 travellers is refused scoring whatever its metal, and auto mode still
  sends a trips request for it. The result (here KNOWN) appears only as a banner
  count. (The couple trip is still WITHHELD with exit 3; that part held up.)
- `test_oa_i_legacy_and_misc.py::test_a_lookup_that_was_paid_for_is_shown_somewhere`.
  B4 has an Aeroplan award (the chosen one) and a VS award. The VS lookup is sent,
  gets HTTP 404, and "HTTP 404" appears nowhere in the output.

This follows D25 (metal lines and counters are for the chosen award only), so it
is Low. But plan §7 item 2 asked for exactly this attack, and the banner's "NOT
KNOWN 1" cannot be traced to any leg.

### 11. Low: a single-carrier policy award now reads "nothing known" while the model treats it as known metal

**Repro:** `test_oa_i_legacy_and_misc.py::test_a_single_carrier_row_the_scorer_treats_as_known_metal_is_not_printed_as_nothing_known`.
A United award whose row lists only `UA`. Before this feature the per-leg detail
printed `metal: UA (source: seats_aero)`, and `has_known_metal` is True: the
surcharge and alternatives use UA. The CLI now always engages the lookup, and the
award reads `NOT_NEEDED_POLICY`. The legacy line is **replaced**
(`formatter.print_leg_detail`, `if metal is not None ... elif operating_carrier`)
by "Nothing is known about which airline flies it; the possible carriers are UA."
The output now contradicts the model it prints. The error is in the safe
direction, hence Low.

### 12. Low: a truncated trips manifest is read as "no lookups recorded"

**Repro:** `test_oa_f_replay.py::test_a_truncated_trips_manifest_is_refused_not_read_as_no_lookups`.
A tool-written corpus with `trips_endpoint/MANIFEST.md` truncated to 0 bytes, and
its snapshot still on disk, replays with exit 0. Every lookup reads NOT RECORDED
and the hash silently loses its `trips|` lines.

Deviation 3 was argued for a manifest whose rows were deleted. The code
implements it as "`parse_manifest` raised, the file is readable, so rows = []".
That also swallows a zero-byte file, a merge-conflicted file, or any text that is
not a manifest. The banner says "0 recorded row(s)" and no number moves under the
committed table, hence Low. A file without the manifest header should refuse.

**Location:** `src/snapshot_replay.py` `load_trips_replay_set`.

### 13. Low: the replay transport does not check that it opened a lookup of this id

**Repro:** `test_oa_f_replay.py::test_a_row_pointing_at_another_ids_recording_is_not_attributed_to_this_id`.
The B4 row is re-pointed (with a consistent hash cell) at another id's recording
whose list is empty. The replay prints "Seats.aero returned an EMPTY itinerary
list for availability `<B4's id>`", which describes a request that was never made
for that id. A non-empty list from another id is caught (`AVAILABILITY_ID_MISMATCH`);
an empty one is not. `_meta.availability_id` and `_meta.request.availability_id`
are on the envelope and are never compared.

**Location:** `src/snapshot_replay.py` `SnapshotTransport.trips_raw`.

### 14. Low (admitted, sized): duplicate trips rows and rows for a leg not in the trip

**Repro:** `test_oa_f_replay.py::test_two_rows_for_one_availability_id_are_not_resolved_silently`.
A second, later recording of the same id under B3, saying DL, replaces the VS
answer on B4 with no disclosure.

`::test_a_trips_row_for_a_leg_not_in_the_trip_is_not_used_silently`: B4's row
relabelled `B9` still drives B4's KNOWN line.

Both are as small as the Coder claims. The same id is recorded under two legs
only if the fixture changed between runs. Neither can print a carrier the bytes
do not name.

### 15. Low (admitted, sized): cap before cache, and 429 skipping cached lookups; the README is not updated

**Repro:** `test_oa_c_budget.py::test_admitted_gap_a_free_cache_hit_after_the_cap_reads_not_looked_up`
(B4 is on disk; cap 1 is spent on B2; B4 reads CAP_REACHED) and
`::test_admitted_gap_a_429_also_skips_lookups_the_cache_could_answer`.

Both are as small as claimed. The next run inside the TTL reads the cached answers
first for free, so a warm cache fills up over runs. It is never wrong, only
NOT LOOKED UP.

`test_oa_h_guards_docs.py::test_the_readme_does_not_promise_a_free_cache_hit_the_code_does_not_give`:
the README says "Cache hits are free" (flag table) and "A cache hit is free", and
its Known limitations mention none of the admitted trips gaps. Those gaps are this
one, finding 14, and the inferred route in 16.

### 16. Low: the label-flip checks are weaker than the README describes

**Repro:**
- `test_oa_g_tools.py::test_a_forged_real_capture_from_the_synthetic_example_cannot_flip_the_label`.
  The published example's page is re-wrapped with `synthetic: false`,
  `captured_by: "src.trips_tools capture"`, a recomputed content hash and an
  **empty** `.raw.txt`. `schema_verification_problems` returns `[]`. The
  `.raw.txt` sibling is checked for existence only and is never compared with
  the page it vouches for.
- `::test_an_inferred_route_capture_cannot_flip_the_label` (the admitted
  `--availability-id` gap, and it is bigger than claimed). A capture with no
  availability row, no matcher run, and `route_inferred_from_itineraries: true`
  passes the flip check. The Coder says "the route check is then not evidence",
  but the flip check never looks at that field.
- `::test_the_cents_flip_rests_only_on_a_genuine_capture`.
  `totaltaxes_unit_problems("cents")` accepts a hand-written `real/` file with no
  `captured_by`, a wrong content hash and no `.raw.txt`.

**Answer to "can a synthetic file flip the label?":** not by accident. Copying
`synthetic/openapi_example.json` into `real/` is refused. By deliberately
rewriting `_meta`, yes. These cases need someone to forge or hand-edit a file,
hence Low.

### 17. Low: `capture` says "at most 2 calls", but its search can paginate

**Repro:** `test_oa_g_tools.py::test_the_promised_maximum_holds_when_the_search_paginates`.
The search's first 4 pages say `hasMore` with a cursor, so **6 requests** are sent
after Tsuki agreed to at most 2. `search_raw` follows pagination up to
`MAX_PAGES = 25`. A one-day, one-route search is unlikely to paginate.

### 18. Low: `capture`'s filename uses unsanitized API strings

**Repro:** `test_oa_g_tools.py::test_api_supplied_airport_codes_cannot_steer_or_crash_the_capture_write`.
In `--availability-id` mode with no local row, the file name comes from the
itineraries' `OriginAirport`/`DestinationAirport` (the inferred route). A `/` in
one of them raises `FileNotFoundError` out of `main()` as a traceback, after the
trips call was spent. Route mode uses the row's `Route` airports the same way.
Trips snapshot names are sanitized (`re.sub(r"[^A-Za-z0-9_.-]", "-", ...)`);
capture names are not.

**Location:** `src/trips_tools.py` `run_capture`, line 368.

### 19. Low: a closed stdin at the prompt is a traceback

**Repro:** `test_oa_g_tools.py::test_a_closed_stdin_at_the_prompt_is_a_clean_refusal`.
Without `--yes`, `input()` raising `EOFError` (piped or closed stdin) escapes
`main()`. No call is made, so this is cosmetic.

### 20. Low: `yq-check` offers figures the tool itself does not believe

**Repro:** `test_oa_g_tools.py::test_yq_check_refuses_a_tax_figure_the_tool_itself_does_not_believe[-100]`
and `[500]`. `JTotalTaxes` of -100 (corrupt) or 500 (GBP 5.00 on an LHR departure
in J, below the UK APD owed) goes through the check, which refuses only 0, "0" and
None. The record is written as evidence and the exit code is 0. Scoring treats
both figures as unknown. The check exists to settle what that figure contains, so
comparing a figure the tool does not trust makes the check meaningless.

### 21. Low: `MixedCabinPct > 0` under `min_cabin_pct=100` is not drift

**Repro:** `test_oa_i_legacy_and_misc.py::test_mixed_cabin_itineraries_under_min_cabin_pct_100_are_recorded_as_drift`.
D16 says: "Under min_cabin_pct=100 its presence is also drift." The parser counts
presence and writes a drift line only for the value 0. An itinerary with a
nonzero value means the server did not honour the request parameter, and that is
exactly what a first capture should flag. Exit 0 and "CAPTURED CLEAN" still
follow.

### 22. Low: `--trips-cap ²` gives no named reason

**Repro:** `test_oa_h_guards_docs.py::test_every_bad_flag_exits_1_with_a_named_reason_and_no_traceback[argv_extra10-1 to 50]`.
`"²".isdigit()` is True and `int("²")` raises. The run exits 1 with
"Error: invalid literal for int() with base 10: '²'" instead of the
"--trips-cap ... is not a whole number from 1 to 50" line. (`"５"` is accepted
as 5, which is fine.)

---

## The Coder's deviations, checked

| # | Deviation | Verdict |
|---|---|---|
| 1 | Three sentences reworded to pass the word check | Safer. Meaning kept, and the forbidden-phrase check holds on all 23 end-to-end cases (A) |
| 2 | `--trips-cap` refused wherever `--trips` is | Safer. It refuses with a named reason (except finding 22) |
| 3 | A trips manifest with its rows deleted replays as NOT RECORDED | The rule is reasonable, but the implementation is broader than the rule (finding 12) |
| 4 | Exit 5 only for drift that blocks the flip | Consistent with D14, but it is the *more permissive* reading of D27 ("5 captured with drift"). Combined with finding 21, a capture that shows the request parameter was ignored still says "CAPTURED CLEAN ... set TRIPS_SCHEMA_VERIFIED_BY" |
| 5 | yq-check prints both CSV rows | **Not** safe on its own. It is the trap in finding 1 unless the loader checks the record's verdict line |
| 6 | "Could match" read literally | Safer (probe `test_an_unreadable_itinerary_of_another_program_cabin_and_price_does_not_block` confirms only fully readable, non-matching itineraries are ruled out) |
| 7 | Small field additions | Fine |
| 8 | Unknown-surcharge note reworded when metal is named | Text only. The equivalence probes show no number moves |

(The Coder calls these "seven deviations" in the brief, but the report lists eight.)

## The Coder's known gaps, sized

- Parser never saw a real response: as stated. Every drift shape I tried degrades to a named UNKNOWN.
- Cap checked before cache, and 429 skipping cached lookups: as small as claimed (finding 15).
- Budget per process: as stated. Finding 9 is a related gap it does not cover.
- Replay duplicate ids and rows for other legs: as small as claimed (finding 14).
- `capture --availability-id` route inference: **bigger than claimed**, because the flip check does not see it (finding 16).
- `--trips` with `--new-trip` and no `--live` is a no-op: not probed further. It spends nothing.
- The flag-key guard covers trips writes only: confirmed for trips (env, .env and flag keys are all kept out of every trips file). Search writes are unchanged and outside this feature.
- The capture's search archives nothing to the corpus: confirmed.

## What I could not break

- **Every ending of a lookup, end to end** (`test_oa_a_statuses.py`, 72 green).
  Tested: KNOWN, AMBIGUOUS, 18 fetch/parse UNKNOWN codes produced through the
  real transport and parser, NO_AVAILABILITY_ID, AVAILABILITY_ID_INVALID and
  TRANSPORT_HAS_NO_TRIPS (budget, cap, 429 and NOT RECORDED are in C and F). Each produces a line on the chosen award that says NOT
  KNOWN / NOT LOOKED UP / NOT RECORDED and names the domain ("possible carriers
  are VS, DL", or "not bounded"). Each is counted in the trip block. None contains
  "no trips", "no flights", "not available", "no itineraries" or "operated by
  <code>". The UNVERIFIED label is on exactly the KNOWN, AMBIGUOUS and
  parse-derived lines, and not on transport failures. The banner counts add up.
- **Numbers do not move with the committed table**
  (`test_with_the_committed_table_no_lookup_outcome_moves_a_number`, 36 green).
  Tested 9 outcomes (KNOWN VS, KNOWN DL, KNOWN VS+DL, AMBIGUOUS, 404, empty, no
  match, per-trip TotalTaxes of 10^9 and 0) in `auto` and `all`, with row lists
  "VS, DL" and "VS". All 21 numeric fields per leg, plus every totals key except
  the metal ones, are identical to a run that is not engaged. The CLI exit code
  and the saving/margin rows are identical with `--trips off`.
- **D29 against master's own code.** With `trips_mode=None`, apply_live and
  evaluate render **byte-identically** to master `a17497d` (extracted with
  `git archive`) for both row lists.
- **Couple trips.** A 2-traveller B4 with `--trips all` and a KNOWN lookup still
  prints `cash (multi-traveller points not priced)`, WITHHELD, exit 3.
- **Temporary YQ tables.**
  - `includes_yq` never adds a band: 4 outcomes x 2 row lists, and APD is not
    added.
  - `excludes_yq` adds exactly one band ($200-$350 on VS) to the taxes.
  - AMBIGUOUS VS|DL and a VS+DL itinerary stay SURCHARGE_UNKNOWN.
  - Untrusted taxes (0, 1 cent, negative, below APD) and an unconvertible
    currency are unscoreable under both verdicts.
  - The validator refuses an unreported source, "unverified", a future date, an
    unknown source, `..`, a path outside `docs/yq-checks/`, an absolute path and
    the README.
- **Budget.** Every search is sent before any trips request. The cap is honoured.
  A counter at 0 sends 0 requests (BUDGET_EXHAUSTED, counted). With 1 call left,
  exactly 1 request is sent. A trips 429 costs one request. 404, 429, 500 and
  timeouts are never cached or archived. Two cabins of one row cost 1 request.
  Off-date findings are never looked up. `auto` skips the policy programs and
  `all` does not.
- **Keys.** A key from the environment, from `.env` or from `--api-key` never
  appears in any cache, snapshot or manifest file, nor in stdout. A trips body
  that reflects the key is refused ("COULD NOT ARCHIVE") and nothing is written,
  in both the CLI and `capture`. A padded env key is stripped before it can reach
  a header error. The request is exactly `?include_filtered=false&min_cabin_pct=100`
  with only `Partner-Authorization` and `Accept`.
- **Cache and snapshot.** A wrong-shape 2xx reads as the same SHAPE_ERROR twice.
  Trips files stay out of the search globs. A failed archive (disk full) is
  reported out loud and the line still prints. Rich markup in API strings is
  printed literally.
- **Replay.** Zero `requests.get` calls, no key needed, and the label is kept. An
  old corpus with no `trips_endpoint/` prints **the hash master's own
  `manifest_hash` computes** and NOT RECORDED, counted on B4.
- **Parser.** Carriers and FlightNumbers disagreeing, mismatched ids, extra
  segments, bad MixedCabinPct values, float/bool costs, empty Source, a broken
  chain, out-of-order segments (sorted correctly), and 11 hostile payloads: none
  gives KNOWN, and nothing raises. The D19 flight-number table and the D15 cabin
  words behave exactly as specified. Per-trip taxes are inert while the unit is
  unverified.
- **Tools.** Every request comes after the call-count line. Anything but y/yes
  makes 0 calls and exits 1. Qatar, Turkish and Singapore are refused before any
  call. A tax figure of 0, "0" or None is refused before the trips call. DL metal
  warns INCONCLUSIVE. The record has its `____` blanks. The usage exit codes are
  1, and `--help` is 0.
- **The -O guards.** With the tree copied into tmp, each of three edits refuses at
  import under `python -O` with a ValueError:
  - dropping the `METAL_UNKNOWN` way-ten row;
  - adding a new `*_UNKNOWN` `add_reason` code;
  - removing `provenance` from the MetalLookup storage classification.

  The real tree imports cleanly under `-O`.
- **Flags.** Each conflict with `--offline`, `--from-snapshot` and single-route
  search exits 1 with a named reason and sends no request. Single-route search
  prints the NOT LOOKED UP footer.
- **Existing contract tests.** `test_verdicts_are_documented`,
  `test_exit_codes_are_documented`, `test_live_first_defaults` and
  `test_no_changelog_in_user_output` are unchanged.

## Notes for Tsuki's Mac (not findings)

- The first `capture` will probably drift (the plan expects this). Its file lands
  in `tests/fixtures/seats_aero/trips_endpoint/real/`, and
  `test_every_committed_real_capture_parses_without_required_field_drift` then
  turns his local suite red until the parser is fixed. That is by design (D14),
  but worth telling him before he runs it.
- His `live_trip_b/` corpus has no `trips_endpoint/`. Its replay hash is unchanged
  (checked against master's code above).
