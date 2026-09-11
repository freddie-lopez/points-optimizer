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

---

# Re-test 2 (fixes `f3f0869..1990866`, fix report `77f548f`)

Tester pass, 2026-09-11. As in round 1, I changed no application code and no
existing test, and every probe runs behind the socket canary. It recorded zero
connection attempts in both modes.

New probes are in `docs/test-reports/operating-airline-probes/test_oa_r2_retest.py`
(81 tests). The round-1 probes are unchanged.

```
.venv/bin/python -m pytest -q -p no:cacheprovider docs/test-reports/operating-airline-probes
```

Recorded state: **10 red / 367 green**, with the same red set under `python -O`.
All 296 round-1 probes are green, and all 10 reds are new round-2 probes.

## Invariants, re-verified

| Check | Result |
|---|---|
| `pytest -q` / `python -O -m pytest -q` | **1460 passed, 13 skipped** in both (the Coder's claim holds) |
| Tree after the full suite | clean |
| v5 / adversarial / known-failures red sets | 19 / 40 / 0, identical by id to the saved baselines |
| Nothing moves with the header-only `yq_inclusion.csv` | holds. The 36 round-1 equivalence probes pass, and exit code and headline are identical to `--trips off` |
| Couple trip | still WITHHELD, exit 3, `cash (multi-traveller points not priced)` |
| `-O` structural guards | hold. Dropping a way-ten row, adding an `*_UNKNOWN` code, or unclassifying `provenance` each still refuse at import under `-O`. `NOT_NEEDED_PARTY` is classified and documented |
| Key leakage | none. A key from env, from `.env` or from `--api-key` appears in no cache, snapshot, manifest, capture or `.raw.txt` file, and not in stdout. A body that reflects the key is refused |
| Network | none. The canary recorded 0 attempts over 377 probes, and replays make 0 `requests.get` calls |
| Search path unchanged | `_next_page_params` gives **byte-identical answers to master `a17497d`** on a 14-payload grid (both run in subprocesses). The old-corpus replay hash still equals master's own `manifest_hash`. Step 0's pinned hashes pass |
| D29 | output when lookups are not engaged is still byte-identical to master |

## The Coder's edits to three test files this branch added

| File | Edit | Verdict |
|---|---|---|
| `tests/test_trips_flags.py` | two expected strings changed to the new 429 wording | follows the intended change (`db2e5ca`, `1990866`); nothing weakened |
| `tests/test_trips_tools.py` | expects `<VERDICT>` in the printed row, plus a new assertion that no row with a verdict is printed | follows the intended change and adds an assertion. **But** its fill step, `.replace("____", "GBP 450.00")`, replaces *every* `____` in the file, including the one in the record's own intro prose. That hides new finding R2-1 |
| `tests/test_yq_inclusion.py` | `GOOD_RECORD` is now a full record; the marker and blanks tests derive from it and keep their `match=` strings | follows the intended change; nothing weakened |

## New findings

| # | Sev | Title | Probe |
|---|---|---|---|
| R2-1 | Medium | A record written by `yq-check`, with every field filled, is refused as "still has ____ blanks" | `test_oa_r2_retest.py::test_the_whole_verdict_flow_a_record_the_tool_wrote_filled_by_hand_loads` |
| R2-2 | Medium | A replay of an honest corpus is refused when two different ids got byte-identical responses | `::test_two_lookups_that_returned_identical_bytes_still_replay` |
| R2-3 | Low | A refused `capture` leaves a one-page INCOMPLETE search in the runtime cache, and the next trip run is served it | `::test_a_refused_capture_does_not_leave_a_truncated_search_for_the_next_trip_run` |
| R2-4 | Low | `capture` still says "CAPTURED CLEAN ... set TRIPS_SCHEMA_VERIFIED_BY" (exit 0) for a capture the flip check refuses | `::test_capture_never_says_clean_and_set_the_constant_for_a_capture_the_flip_check_refuses` |
| R2-5 | Low | The committed synthetic page, re-wrapped with three more fields, still verifies the parser | `::test_the_committed_synthetic_page_cannot_verify_the_parser_under_any_wrapper` |
| R2-6 | Low | "This lookup established nothing further" is printed on NOT LOOKED UP and NOT RECORDED lines, where no lookup was made | `::test_a_line_about_a_lookup_that_was_never_made_does_not_speak_of_this_lookup[4 params]` |
| R2-7 | Low | Two more lines still name the looked-up metal without the parser label (the class behind M3) | `::test_M3_class_every_line_that_names_trips_metal_carries_the_parser_label` |

No Critical, no High. **2 Medium, 5 Low.**

### R2-1. Medium: a legitimately filled yq-check record cannot load

**Repro:**
1. Run `yq-check` (stubbed).
2. Fill the five blank fields exactly as the record asks: date checked, flights
   shown, taxes, separate carrier-charge line, and the verdict line
   `includes_yq`.
3. Add the printed row with `<VERDICT>` replaced by `includes_yq`.
4. Call `yq_inclusion.load`.

**Expected:** the row loads. **Actual:** `YqInclusionError: ... still has ____
blanks`. The intro paragraph `_write_record` puts in every record says "fill
every ____ from the program's own site before adding a row", and the loader's
`BLANK in body` check counts that prose.

This happens on every record the tool writes, so the only path to the first YQ
verdict fails until Tsuki edits the tool's own sentence. It fails closed and
loud, so no number moves, hence Medium. It has been there since Step 12. I missed
it in round 1, and round 2's flow makes it certain to hit.

**Location:** `src/trips_tools.py` `_write_record` (the intro line) and
`src/yq_inclusion.py` `_check_evidence` (the `BLANK` check over the whole body).
The Coder's test hides it (see the table above).

### R2-2. Medium: identical bytes for two ids make an honest corpus unreplayable

**Repro:** a stubbed live run with a tmp corpus. B3 (Flying Blue) and B4 (Virgin
Atlantic) both qualify, and both lookups return `tp.payload([])`. Live, both
correctly read `EMPTY_DATA`. Then `--from-snapshot` exits 1 with
`[trips_snapshot_is_another_lookup] ... is a lookup of availability B3..., not of
B4.... Its bytes say nothing about B4...`.

**Cause:** `_archive_snapshot` de-duplicates snapshots by **content hash across
requests**, so the second id's manifest row is written as "(re-fetch,
identical)" and points at the first id's file. The new L13 check
(`_recorded_id` in `load_trips_replay_set` and `SnapshotTransport.trips_raw`)
then reads the first id from that file and refuses the whole replay. Its message
is also false: those bytes are exactly what Seats.aero sent for B4.

It needs two lookups with byte-identical bodies. An empty list with the same
route coordinates, or the same wrong-shape 2xx, is plausible. The remedy the tool
offers (delete a row) throws away honest evidence.

**Location:** `src/response_cache.py` `_archive_snapshot` / `_snapshot_with_content`
combined with `src/snapshot_replay.py` `_recorded_id`. This is a regression
introduced by fix 7.

### R2-3. Low: `capture`'s one-page search stays in the cache for the next trip run

**Repro:**
1. `capture --origin LHR --destination SFO --date 2027-01-27 --source
   virginatlantic`, with a first search page that says `hasMore` and a cursor.
   It is refused (fix 9), correctly.
2. A Trip B run inside the TTL, using the same `config.CACHE_DIR`, sends **no**
   LHR-SFO search.
3. B4 prints "COVERAGE IS INCOMPLETE: the 1-page safety cap was reached".

`search_raw` caches an incomplete result; only a budget failure is kept out. The
capture's request key (origin, destination, one day) is the key a trip leg with
flex 0 uses. The failure is loud and `--refresh` clears it, and it needs a
one-day search that paginates, hence Low. Before fix 9 the capture followed the
pages and cached a complete result, so this is a regression introduced by fix 9.
A capture that sets MAX_PAGES=1 should not write the shared cache (or should
refuse before caching).

### R2-4. Low: `capture`'s "clean" verdict disagrees with the flip check

**Repro:** `capture --availability-id <id>` with no local row. The route is
inferred, and `schema_verification_problems` now refuses the file, correctly.
The tool still exits 0 and prints "CAPTURED CLEAN. To flip the UNVERIFIED label,
commit both files and set TRIPS_SCHEMA_VERIFIED_BY = '...'". Following that
advice turns the label test red. It is loud, hence Low. `run_capture` should end
with the flip check's own verdict.

### R2-5. Low: a crafted capture can still flip the label

**Repro:** take the committed `synthetic/openapi_example.json` page and wrap it
with `synthetic: false`, `captured_by`, a recomputed content hash, a `.raw.txt`
equal to the page, an `availability_row` carrying the page's own id, and
`route_inferred_from_itineraries: false`. `schema_verification_problems`
returns `[]`.

Fix 8 closed every honest-mistake path I could find, including an empty
`.raw.txt`, an inferred route, another id's row, and the synthetic example
re-wrapped the round-1 way. No unsigned check can stop deliberate forgery. The
one cheap exact closure is missing: refuse a page whose content hash is a
committed `synthetic/` page's. Hence Low.

### R2-6. Low: "This lookup established nothing further" where nothing was looked up

**Repro:** a `MetalLookup` for CAP_REACHED, TRIPS_OFF or RATE_LIMITED_EARLIER
(NOT LOOKED UP), or NO_TRIPS_SNAPSHOT (NOT RECORDED), whose row names one
carrier, renders "... NOT LOOKED UP - the per-run cap ... was reached. **This
lookup established nothing further**; the award's own carrier list names one
carrier ...".

Fix 6's single-carrier domain clause is applied to every non-KNOWN status. The
domain is still right, so this is cosmetic.

**Location:** `src/models.py` `MetalLookup._domain_clause`.

### R2-7. Low: two lines that name the looked-up metal still have no parser label

**Repro:** B4 with a KNOWN `VS19` itinerary. The band note ("modelled carrier
surcharge for VS metal under Virgin Atlantic Flying Club: $200-$350 ... NOT
ADDED") and the surcharge note ("... the itinerary lookup names VS by flight
number, and that metal is NOT used ...") carry no UNVERIFIED parser label.

Fix 2 labelled the two places round 1 named: the alternatives and the yq-check
block. Neither of these lines moves a number and the band note sits directly
under the labelled operating-airline line, hence Low. They are the rest of M3's
class.

## Round-1 findings: closure

Each round-1 probe is green. I also attacked each fix beyond the exact input its
probe used. The class checks below are new round-2 probes, all green unless
stated.

| # | Round-1 finding | Closed? | Class checked beyond the probe |
|---|---|---|---|
| 1 H | Record's verdict not cross-checked | **Closed** | 12 near-miss verdict lines are refused. These include `includes_yq.`, `includes-yq`, `` `includes_yq` ``, `**includes_yq**`, two verdicts, `inconclusive` with a comment, and `excludes_yq`. `Includes_YQ`, padded values and CRLF records load. The refusals cite the right reason. The end-to-end flow is blocked by R2-1 |
| 2 M | Record for another source | **Closed** | A title naming two sources, a second `(source flyingblue)` program line, and a `(source X)` elsewhere in the body: all refused for flyingblue |
| 3 M | Parse-derived lines unlabelled | **Closed for the two named places** | The alternatives note and the yq-check flights and carrier lines carry the label and the marketing caveat. The rest of the class is R2-7 |
| 4 M | `"<id>\n"` passes the id gate | **Closed** | `\r`, `\t`, space, `\x0b`, `\x0c`, NBSP, line separator, NUL and `\n\n`, before or after the id, and full-width digits or dotless-ı: all refused. The transport sends nothing for `<id>\r` |
| 5 M | Pagination signals narrower than search | **Closed** | 11 spellings the shared reader knows block KNOWN, and 7 "nothing more" values do not. The search path answers exactly as master on the grid. (Spellings neither path knows, such as `hasNextPage` or `total`, still only appear as drift lines. That is D12, not a defect) |
| 6 L | Stored `incomplete` dropped | **Closed** | |
| 7 L | `count` larger than the list | **Closed** | `count: "2"` and `2.0` block; `count: 1` and `"abc"` do not |
| 8 L | Cap stops counting on an odd exception | **Closed** | |
| 9 L | Search 429 does not stop trips | **Closed** | After a search 429, a lookup the cache can answer is read (KNOWN, from cache) and nothing is sent |
| 10 L | Paid lookups printed nowhere | **Closed** | Auto sends no call for a party leg (`NOT_NEEDED_PARTY`, not counted). `--trips all` looks it up and prints it. The couple trip stays WITHHELD, exit 3 |
| 11 L | Single-carrier "nothing known" | **Closed**; wording issue R2-6 | The legacy `metal:` line is kept, and D29 byte identity holds |
| 12 L | Truncated trips manifest read as none | **Closed** | An old corpus with no `trips_endpoint/` still replays, and its hash equals master's |
| 13 L | Replay never checks the file's id | **Closed**; introduced R2-2 | |
| 14 L | Duplicate id / foreign leg | **Closed** | The same id with the same bytes under two legs is not a conflict (checked on `load_trips_replay_set` directly: a twin flight leg cannot be built into a replayable corpus because the *search* manifest refuses a leg whose search was a cache hit, which predates this feature) |
| 15 L | Cap before cache; README | **Closed** | `--refresh` after the cap correctly does not read the cache |
| 16 L | Flip checks weaker than README | **Closed for honest mistakes** | Harmless byte changes to a genuine `.raw.txt` (CRLF, trailing newlines, re-indented, compact, re-ordered keys) still flip. Crafted forgery: R2-5. Tool advice: R2-4 |
| 17 L | Capture promise vs pagination | **Closed**; introduced R2-3 | |
| 18 L | Unsanitized capture filename | **Closed** | |
| 19 L | Closed stdin traceback | **Closed** | |
| 20 L | yq-check compares untrusted figures | **Closed** | |
| 21 L | MixedCabinPct not drift | **Closed** | A live lookup still reads KNOWN (VS) with the mixed itinerary excluded; only the flip is blocked |
| 22 L | `--trips-cap ²` | **Closed** | |

## What I could not break in round 2

Beyond the class checks above:
- The shared pagination reader did not change the search path.
- A search 429 stops every trips request, while cached answers are still read.
- `--refresh` is honoured after the cap.
- The party skip is free in auto mode and still available under `--trips all`.
- Mixed-cabin drift blocks only the flip.
- The new replay refusals let an old corpus, and a same-bytes duplicate, through.
- A genuine capture flips despite harmless byte changes.
- Every invariant in the table at the top of this section holds.

---

# Re-test 3 (fixes `b9d7db2..347e6d7`, fix report `918cf83`)

Tester pass, 2026-09-11. As before, I changed no application code and no
existing test, and every probe runs behind the socket canary. It recorded zero
connection attempts in both modes.

New probes are in `test_oa_r3_retest.py` (53 tests), plus a render helper,
`_r3_cli_render.py`, which is not collected as a test. One round-2 probe was
revised and a companion was added; see the ruling below.

Recorded state: **0 red / 431 green**, the same under `python -O`.

## Ruling on the disputed probe: the Coder is right, and I revised the probe

`test_two_lookups_that_returned_identical_bytes_still_replay` asserted, as a
precondition, that the second manifest row read "(re-fetch, identical)". In
other words, it required the archive to **share one file across two ids**. That
pinned an implementation, not the behaviour I meant to protect.

A shared file is byte-for-byte the round-1 L13 attack (a row pointed at another
id's file). The only difference is a typed note in an unhashed manifest cell. So
no id rule can accept one and refuse the other, and R2-2 and L13 could not both
pass under that precondition.

The revised probe asserts the behaviour:
- each id has a snapshot file of its own that names it;
- the corpus replays (not exit 1);
- both B3 and B4 print their own EMPTY_DATA line.

A new companion, `test_a_row_pointed_at_another_ids_identical_file_is_still_refused`,
asserts the other half: a B4 row pointed at B3's identical-bytes file is still
refused (`trips_snapshot_is_another_lookup`, exit 1).

The round-2 intent (R2-2: an honest corpus with identical bytes replays) is
unchanged. Only the precondition went. Both tests are green.

## Invariants, re-verified

| Check | Result |
|---|---|
| `pytest -q` / `python -O -m pytest -q` | **1513 passed, 13 skipped** in both (the Coder's claim holds) |
| Tree after the full suite | clean |
| v5 / adversarial / known-failures red sets | 19 / 40 / 0, identical by id to the saved baselines |
| Nothing moves with the header-only `yq_inclusion.csv` | holds (the 36 equivalence probes); exit code and headline are identical to `--trips off` |
| Couple trip | WITHHELD, exit 3 |
| `-O` structural guards | hold (all 3 import-time edits refuse) |
| Keys / network | no key in any file or in stdout (env, `.env`, `--api-key`); 0 connection attempts |
| Output with lookups off (D29) | still byte-identical to master `a17497d` |
| Search path | pagination grid identical to master; old-corpus replay hash equals master's own `manifest_hash` |
| **R2-7 escaping** | full `main()` output at 3000 columns, HEAD vs `9bf7c90`, same stub, 6 scenarios (KNOWN, 404, `--trips off`, `--trips all` with cap 1, a one-carrier row, AF alternative): **identical line for line** once the parser label and the R2-6 wording are removed. No text was lost or changed beyond the label (`test_the_full_cli_output_is_the_round_2_output_plus_the_label_and_nothing_else[...]`). No string in `src/` or in the trip fixtures contains lowercase-led `[...]` text, the only kind rich would treat as markup, so the new `escape()` calls cannot remove anything |

## The Coder's two edits to `tests/test_trips_tools.py`

- **The fill step (R2-1).** It now fills only the five field lines and asserts
  each appears exactly once. That is stricter than before, and it no longer hides
  a record that cannot load. Good.
- **`test_a_capture_writes_the_envelope_and_the_raw_body_with_no_key`.** Its
  stub's raw body (`{"data": "verbatim"}`) is deliberately not the page, so under
  R2-4 the expected exit is now 5 plus the "CANNOT FLIP" line. The envelope, the
  file-name and the no-key assertions are unchanged. A clean capture exiting 0 is
  still pinned elsewhere (`test_trips_flip_evidence.py::test_a_genuine_capture_still_flips_both`
  and my `test_an_honest_capture_flips_...`). This follows the intended change
  and weakens nothing.

## New behaviour, attacked (all held up)

- **One trips snapshot file per id (R2-2).**
  - A same-id re-fetch with the same bytes still adds no file (3 runs give 1 file
    and 3 rows).
  - Search snapshots are still de-duplicated across requests.
  - Disk growth is one small file per distinct id; nothing else changes.
  - A corpus written by the *round-2* code, with a cross-id shared file, is
    refused by name (`trips_snapshot_is_another_lookup`; the companion probe is
    exactly that shape). No such corpus exists: the branch has never run live
    outside this sandbox, and Tsuki's corpus has no `trips_endpoint/`.
- **A refused capture deletes its own cache entry (R2-3).**
  - A capture served a fresh, complete, two-page entry a trip run wrote reads it
    with 0 calls and leaves it in place.
  - A refused capture deletes only its own key; every other leg's entry survives.
  - A complete one-page capture search stays cached.

  Two cases I could not turn into a defect:
  - An entry *expired* under the 6-hour default is overwritten by the capture's
    fetch and then removed. A trip run with a longer `--cache-ttl` would have
    reused it, so it re-fetches (costs a call, never wrong data).
  - A concurrent run writing the same key between the capture's write and its
    unlink loses that entry to a re-fetch in the same way.
- **capture ends with the label check (R2-4) and the R2-5 checks: false refusals.**
  An honest capture flips (exit 0, CAPTURED CLEAN) whatever form its body arrives
  in:
  - compact;
  - CRLF and pretty-printed;
  - with a trailing newline;
  - `\u`-escaped Unicode (a "Zürich" aircraft name);
  - exponent floats (`5e3`);
  - server-sorted keys.

  The canonical comparison parses both sides the same way, so int and float
  formatting cannot diverge on a real capture. Key material is refused before
  anything is written, never redacted, so redaction cannot change the bytes.
  `--cabin W` on a row where only J matches still flips (the check accepts a
  match in any cabin of the row). A capture outside a `real/` directory says so
  (exit 5).
- **R2-6, whole class.** Every NOT_LOOKED_UP code (all 10) on a one-carrier row
  says "No lookup was made on this run". Every UNKNOWN code (all 19) keeps "This
  lookup established nothing further".
- **R2-7, whole class.** Four scenarios, rendered unwrapped:
  - `excludes_yq` with KNOWN VS (case C);
  - `excludes_yq` with AMBIGUOUS VS|DL;
  - the AF alternative;
  - a paid lookup on a non-chosen award.

  No line naming looked-up metal lacks the label. In case C the `Surcharge:`
  rule line carries it. (A surcharge rule's own citation from `surcharges.csv`,
  "CONFIRMED BY SOURCE: ... Upper Class on VS metal", describes the table row
  and is correctly left alone.)

## Observations (not findings)

- **An honest capture whose recorded row has no matching itinerary cannot flip
  the label** (R2-5's `_row_matches`). For example, the row's price is not among
  the itineraries because `min_cabin_pct=100` filtered the one behind it, which
  is plan risk 3. The capture exits 5 and names the reason
  (`test_an_honest_capture_whose_row_price_has_no_itinerary_is_refused_with_its_reason`).
  This is stricter than D14 and may cost Tsuki an extra capture, but flipping on
  such a capture would verify a parser whose matcher never matches.
- **The flip check now runs the search parser** (`parse_availability_row`) on the
  recorded row. A later, unrelated search-parser change could make a committed
  capture stop verifying. That would be loud (the label test goes red), never
  silent.

## New findings

**None.** No Critical, High, Medium or Low.

## Every earlier finding: closure

- **Round 1: all 22 closed.** All 296 round-1 probes are green, plus the round-2
  class checks.
- **Round 2: all 7 closed.**

| # | Finding | Closed? | Evidence |
|---|---|---|---|
| R2-1 | A filled record cannot load | **Closed** | `test_the_whole_verdict_flow_a_record_the_tool_wrote_filled_by_hand_loads` (fields only, prose untouched) is green |
| R2-2 | Identical bytes make a corpus unreplayable | **Closed** | the revised probe and its companion are green |
| R2-3 | Truncated capture search left in the cache | **Closed** | round-2 probe green, plus 3 new cache probes |
| R2-4 | capture's "clean" disagrees with the flip check | **Closed** | round-2 probe green, plus honest-flip and outside-`real/` probes |
| R2-5 | Synthetic page re-wrapped flips | **Closed for the cheap paths**; deliberate hand-built forgery remains, as stated in the README and fixtures READMEs | round-2 probe green; 8 honest-capture probes show no false refusal |
| R2-6 | "This lookup" where none was made | **Closed** | all 10 NOT_LOOKED_UP codes plus NOT_RECORDED |
| R2-7 | Metal lines without the label | **Closed** | 4 scenarios; escaping changed nothing but the label |

---

# Re-test 4 (fix round 3 from the Manager review: `b3bc640..ea79ce9`, report `ea79ce9`)

Tester pass, 2026-09-11. As in every round, I changed no application code and no
test under `tests/`, and every probe runs behind the socket canary. It recorded
zero connection attempts.

New probes are in `test_oa_r4_retest.py` (50 tests). The existing probes were
updated to the round-3 contract; the full list, with reasons, is below.

Recorded state: **1 red / 495 green**, with the same red set under `python -O`.
The red is new finding R4-1.

## Must-fix 1, verified myself: the full suite in every label state

Setup:
1. A scratch `git archive HEAD` export.
2. `python -m src.trips_tools capture` (stubbed, route mode, LHR-SFO
   `virginatlantic` J) wrote an honest capture into its
   `tests/fixtures/seats_aero/trips_endpoint/real/`. It exited 0 with
   "CAPTURED CLEAN".
3. Then `TRIPS_SCHEMA_VERIFIED_BY`, and then `TRIPS_TOTALTAXES_UNIT = "cents"`,
   were edited **in source**.

| State | normal | `python -O` |
|---|---|---|
| committed tree (no capture) | **1580 passed, 13 skipped** | **1580 passed, 13 skipped** |
| capture committed, constants not flipped | **1580 passed, 13 skipped** | - |
| `TRIPS_SCHEMA_VERIFIED_BY` = the capture | **1571 passed, 22 skipped** | **1571 passed, 22 skipped** |
| flipped + `TRIPS_TOTALTAXES_UNIT = "cents"` | **1570 passed, 23 skipped** | **1570 passed, 23 skipped** |

These match the Coder's figures exactly. The extra skips are tests that describe
the unflipped constants.

**Must-fix 3's messages, checked the same way** (scratch exports, full suite):
- A **drifting** capture (cabin word "premium economy") exits 5 and says "exactly
  one test is red ... `test_every_committed_real_capture_parses_without_required_field_drift`".
  Committed, the suite gives **1 failed** (exactly that test), 1579 passed, 13 skipped.
- A capture the **label check refuses** (`--availability-id` with no local row)
  exits 5 and says "Committing these files turns no test red". Committed, the
  suite gives **1580 passed, 13 skipped**.

**My own probes had the same problem as must-fix 1.** In the flipped-plus-cents
tree, 45 probes went red because they assumed the unverified state. I added an
autouse fixture to the probe conftest that pins both constants for every
in-process probe. In the flipped tree that leaves only the probes that shell out
to `git` (the master and round-2 comparisons, the contract-tests diff), which
need a git checkout of the committed tree, plus R4-1.

## The 38 red probes: each confirmed, then updated

Every one of the 38 was red only because of a change the Manager asked for. I
confirmed this for each group before editing, by checking that the probe passes
under the new contract with nothing else relaxed.

| Probe(s) | Why red | Change | What still guards |
|---|---|---|---|
| `test_oa_d_numbers.py::test_includes_yq_never_adds_a_band[...]` (8) | `YqVerdict` gained `airline` (TypeError). Under D1(b), `includes_yq` on 404, AMBIGUOUS or DL metal is now deliberately inert | Replaced by `test_includes_yq_on_known_vs_scores_at_taxes_only_and_adds_no_band` (both row lists), plus **`test_a_verdict_off_its_airline_scores_exactly_as_no_row[...]` (18)**: 404, AMBIGUOUS, KNOWN DL, KNOWN VS+DL and an empty list, x row lists, x both verdicts. Every one of the 21 numeric fields per leg and every totals key must equal the run with no table | Stronger. "Never adds a band" still holds on the only metal a verdict now reaches, and "inert everywhere else" is new |
| `::test_excludes_yq_adds_exactly_one_band_on_known_single_metal`, `::test_excludes_yq_on_carriers_with_different_bands_stays_unknown[2]`, `::test_untrusted_taxes_win_over_every_verdict[8]`, `::test_unconvertible_currency_wins_over_every_verdict[2]` | TypeError (positional `YqVerdict`) | The `table()` helper builds `YqVerdict(airline="VS")` keyed by `(source, airline)`. The untrusted-tax and unconvertible probes gained a precondition that the metal is KNOWN VS, so the verdict *would* apply and the untrusted-taxes rule is what refuses | Unchanged assertions (one $200-$350 band; `inf` plus TAXES_UNKNOWN) |
| `::test_the_committed_table_is_still_header_only` | header is now 6 columns | expects `source,airline,verdict,verified_on,evidence,notes` | loader returns `{}` |
| `test_oa_i_legacy_and_misc.py::test_not_engaged_output_is_byte_identical_to_master[2]` | should-fix 5a reworded the "which is the v0 bug" sentence | **Coordinator's D29 ruling:** normalises exactly four old-to-new pairs (the v0 sentence and the three `surcharges.csv` "v1 modeled band" notes), with a precondition that master still carries the old sentence. It compares whitespace-unwrapped text, because the reworded sentence moves the line wrap | Every other word must equal master's |
| `test_oa_r2_retest.py::test_H1_a_legitimately_filled_verdict_line_loads[4]`, `::test_H1_a_record_saved_with_crlf_line_endings_loads` | records lacked the D1 and must-fix-2 lines; 5-column CSV | records carry "itinerary lookup status: KNOWN", "checked airline", and "operated by VS itself ... : yes"; 6-column CSV; result keyed `("virginatlantic", "VS")` | the same verdict spellings and CRLF |
| `::test_the_whole_verdict_flow_a_record_the_tool_wrote_filled_by_hand_loads` | the site half and the row template changed | fills the new five fields (operated-by: yes, the TOTAL), uses the `virginatlantic,VS,<VERDICT>,...` template, and asserts no `____` remains | a tool-written, honestly filled record still loads |
| `test_oa_r3_retest.py::test_every_line_naming_looked_up_metal_carries_the_label[excludes_known, excludes_ambiguous]` | TypeError | table keyed by `(source, airline)` | unchanged: every metal line carries the label |
| `::test_the_full_cli_output_is_the_round_2_output_plus_the_label_and_nothing_else[6]` | should-fix 5a wording, and D1's band note ("... no row for 'virginatlantic' **on VS metal**") | normalises exactly those (the same four sentences, plus that one band-note clause) | every other line must equal round 2's |

**Probes that were still green, but only because a 5-column CSV now fails on its
header.** Each would have passed for the wrong reason, so each now uses the
6-column CSV and a full record, and asserts its **refusal reason** with `match=`:
- `test_oa_d_numbers.py::test_a_record_for_one_source_cannot_back_a_verdict_for_another`
  (it also asserts the same good row loads on its own);
- `::test_the_validator_refuses_bad_rows[...]`: the 8 old cases with their
  reasons, plus 2 new ones (a blank airline and a 3-letter airline), plus
  `::test_the_validator_accepts_the_same_good_row_it_refuses_variants_of`;
- `test_oa_i_legacy_and_misc.py::test_a_record_whose_own_verdict_disagrees_with_the_row_is_refused[2]`;
- `test_oa_r2_retest.py::test_H1_class_every_near_miss_verdict_line_is_refused[12]`;
- `::test_M2_class_a_record_naming_another_source_never_backs_it[3]`.

`conftest.py` gained the YQ contract helpers (`yq_record_body`,
`yq_write_record`, `yq_load`, `yq_table`, `YQ_HEADER`) and the label-constant
pin.

## New finding

| # | Sev | Title | Probe |
|---|---|---|---|
| R4-1 | **Medium** | A yq-check record that says it "cannot tell includes from excludes" still backs a verdict, and D1 then applies it to every cabin and route on that airline | `test_oa_r4_retest.py::test_a_record_that_says_the_check_cannot_tell_includes_from_excludes_backs_no_verdict` |

### R4-1. Medium: a check with no modelled band still produces a loadable verdict

**Repro (stubbed):**
1. Run `yq-check --origin JFK --destination LHR --cabin W --source virginatlantic`
   on a row with W space and a KNOWN `VS4` itinerary.
2. The surcharge table has no VS row for W (it has only VS/NA-EU/J), so the block
   and the record say "modelled carrier surcharge band: **NONE MODELLED for VS
   metal, so the site total cannot tell includes from excludes**". The record
   also carries "WARNING: likely INCONCLUSIVE".
3. The tool **still prints the row template** `virginatlantic,VS,<VERDICT>,...`,
   and its rule still says "site total about equal to the row figure:
   includes_yq".
4. Fill the five fields honestly (the site total equals the row figure) and paste
   the row: `yq_inclusion.load` returns `("virginatlantic", "VS") -> includes_yq`.

**Why it matters:**
- With no band, a site total equal to the row figure is also what a fare with
  **no** carrier surcharge shows, so it is not evidence of inclusion. The
  record's own words say so.
- Because D1 keys a verdict by (source, airline) only, that one W check then
  scores every Virgin Atlantic award on VS metal, in every cabin and region
  (J included), at taxes alone.

The recommended check (JFK-LHR J) has a band and is unaffected. The failure
needs a cabin or route with no modelled row, which is likely if J has no space on
the date Tsuki picks. Hence Medium: this is the one step that can move money.

**Location:** `src/trips_tools.py` `run_yq_check` prints the template whenever an
airline is KNOWN, whatever `band_text` says. `src/yq_inclusion.py` never reads
the band line or the INCONCLUSIVE warning.

**Fix direction:** print no template (as for "no KNOWN airline") when the band is
NONE MODELLED, and have the loader refuse such a record.

## New behaviour, attacked: what held up

- **D1(b) scoping** (`test_oa_r4_retest.py`, plus the 18 off-airline equivalence
  probes):
  - A `(virginatlantic, VS)` verdict does not reach KNOWN DL, KNOWN AF, KNOWN
    VS+DL (one segment on VS), AMBIGUOUS VS|DL, a 404, or `--trips off`. In every
    case the numbers equal the no-table run.
  - The award's line names the recorded check and the exact reason it does not
    apply ("a different airline", "several airlines", "AMBIGUOUS",
    "UNKNOWN (HTTP_404)", "NOT LOOKED UP").
  - A valid but different airline (DL) does not reach VS.
  - Two rows for one source each reach only their own metal.
  - **Case C is keyed by (source, airline):** `excludes_yq` on VS adds exactly
    $200-$350; a `flyingblue`/VS row does not reach a Virgin award; `excludes_yq`
    on AF under Virgin applies and stays SURCHARGE_UNKNOWN (no band), never
    taxes-only.
- **Loader:**
  - The airline column normalises case and whitespace (`vs`, ` VS `, `Vs\t`), as
    do the record's airline codes.
  - It refuses a record whose status is not KNOWN (AMBIGUOUS, UNKNOWN, NOT LOOKED
    UP), whose checked airline differs or is `NONE`, whose operated-by code
    differs, or whose operated-by answer is `no`, `y`, `yes.`,
    `yes (VS3 operated by Virgin)` or `n/a`. It also refuses a record with two
    operated-by lines. `Yes`, `YES` and padded `yes` load.
- **yq-check arithmetic:**
  - VS J from JFK prints "$200-$350 (pt $275) one way (VS metal, cabin J)" and
    "row figure + band: $450.00-$600.00 (row $250.00 + band)", which is half the
    round-trip row, added to the USD row figure.
  - Flying Blue on KL J to AMS prints "$75-$125 one way (KL metal)" and
    $325.00-$375.00.
  - With no single KNOWN airline, no template is printed.
  - A band-bearing check, filled honestly, loads end to end.
- **NOT_NEEDED_TAXES_UNREPORTED:** a `singapore` award in auto costs 0 trips
  calls and is not counted as a missing lookup; `--trips all` looks it up.
  (`qatar` and `turkish` already stop at NOT_DIRECT_PARTNER.)
- **`--refresh`:** a warm cache plus `capture --refresh` sends exactly 1 search
  and 1 trips request, as announced ("1 search (--refresh: never served from the
  disk cache) + 1 trips"). The pre-call line honestly says "spent 2 of 1,000"
  after an earlier run in the same process.
- **Must-fix 3:** the test the drift message names exists, and the full-suite
  runs above prove both messages.
- **Rewording:** no changelog text (v0/v1/v5, round/re-test/R2-x, must/should-fix,
  "finding X-n") appears in:
  - capture or yq-check output;
  - the record;
  - live output for 12 table x outcome combinations.

  The D29 and round-2 comparisons show that only the four ruled sentences and the
  band-note clause changed.

## Observations (not findings)

- **Scope:** a verdict is keyed by (source, airline) only. A J check also reaches
  W and Y awards and every region on that airline. That is D1 option (b) as
  specified; R4-1 is where it bites. Worth a line to Tsuki.
- **The rule gives no numeric tolerance for "about equal".** On VS J the gap to
  the band is at least $200, so a realistic total will not straddle the two
  verdicts; a stretched "about" lands on "inconclusive", which is safe.
- **The loader does not check the airline against the program's
  `bookable_carriers`.** This is harmless: a verdict only reaches awards whose
  lookup is KNOWN on that airline. The same goes for `AIRLINE_RE` accepting an
  all-digit code, which no flight number can produce.
- **`capture --refresh` on a paginating search** overwrites a fresh, complete
  trip-run cache entry with its one page, then deletes it (R2-3). The next trip
  run re-fetches, which costs calls but never gives wrong data.
- **Pre-existing changelog text outside 5a's live-output scope:** the `main.py`
  `--help` strings (v0/v3/v5), and two replay refusal messages
  (`snapshot_replay.py`: "predates v5", "finding H-1").

## Invariants, re-verified

| Check | Result |
|---|---|
| suite, committed tree | 1580 passed / 13 skipped, normal and `-O`; the tree is clean after the run |
| baseline red sets (v5 / adversarial / known-failures) | 19 / 40 / 0, identical by id |
| header-only `yq_inclusion.csv` | nothing moves (36 equivalence probes; exit code and headline equal to `--trips off`) |
| couple trip | WITHHELD, exit 3 |
| `-O` guards | all three import-time edits still refuse |
| network | 0 connection attempts; replays make 0 `requests.get` calls |
| keys | none in any cache, snapshot, manifest, capture or `.raw.txt` file, or in stdout (env, `.env`, `--api-key`) |
| D29 (as ruled) | identical to master apart from the four reworded sentences |
| search path | the pagination grid still matches master, and the old-corpus replay hash is master's |

## Earlier findings

- **Round 1 (22), round 2 (7) and round 3 (none):** still closed. Their probes,
  updated as listed above where round 3 changed the contract, are green.
- **Open:** R4-1 only.
