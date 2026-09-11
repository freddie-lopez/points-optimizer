# Coder report: operating-airline lookup through the Seats.aero trips endpoint

Plan: `docs/plans/operating-airline.md` (committed at 5b3a9b5). Branch
`feature/operating-airline`, 14 step commits on top of the plan. Not pushed
(the sandbox cannot push).

## Result

| | before (master a17497d + plan) | after (f548c48) |
|---|---|---|
| `.venv/bin/python -m pytest -q -p no:cacheprovider` | 937 passed, 13 skipped | **1362 passed, 13 skipped** |
| `.venv/bin/python -O -m pytest -q -p no:cacheprovider` | 937 passed, 13 skipped | **1362 passed, 13 skipped** (plus pytest's own "-O ignores asserts outside test modules" warning, present on master too) |
| `docs/test-reports/v5-probes/` red set | 19 | 19, **identical by test id** |
| `docs/test-reports/adversarial-probes/` red set | 40 | 40, **identical by test id** |
| `docs/test-reports/known-failures-probes/` red set | 0 | 0 |

Red sets were produced with the command in the brief and `diff`ed against the
saved baselines after steps 4, 6, 7, 8, 9, 11 and 13. **No existing test file
was edited**: `git diff a17497d --stat -- tests/` shows only new files plus
`tests/fixtures/seats_aero/README.md`, a doc the plan's Step 13 lists.
`test_verdicts_are_documented`, `test_exit_codes_are_documented`,
`test_live_first_defaults` and `test_no_changelog_in_user_output` are unchanged
and pass. The sandbox venv runs pytest 9.0.3; the new tests use only
parametrize, fixtures, monkeypatch, tmp_path, capsys and `pytest.approx`, all of
which are in 7.4.0.

No network call was made to seats.aero or any other host. Every test stubs
`requests.get`. Nothing writes into the committed corpus or Tsuki's paths.

## Steps completed

| Step | Commit | What | Verified by | Result |
|---|---|---|---|---|
| 0 | 843a929 | Pin a search-only manifest's hash | `tests/test_trips_hash_stability.py` (3), run on unmodified code to get the literals `mh_775a13936a4e27e0` / `mh_bf36633363c6772b` | green then and at the end |
| 1 | a4b778e | `MetalStatus`, `METAL_REASONS`, `MetalLookup` (ValueError invariants, one `render()`), import-time storage classification | `test_metal_lookup_model.py` (108): 34 forbidden combinations raise, also in a `python -O` child; every non-KNOWN render says NOT KNOWN / NOT LOOKED UP / NOT RECORDED + "possible carriers" and none of "operated by" / "no trips" / "no flights"; KNOWN renders say UNVERIFIED | 108 passed |
| 2 | 80da892 | `src/seats_trips.py` parser + matcher; `tests/_trips_payloads.py`; `trips_endpoint/README.md`, `synthetic/openapi_example.json`, `real/.gitkeep` | `test_seats_trips_parser.py` (98): OpenAPI example KNOWN (CM, TK); every D13 parse-derived row; the D19 table; D15 cabins; D16 matching; an unreadable could-match itinerary blocks KNOWN; extras are drift; never raises | 98 passed |
| 3 | 6c54375 | `SeatsClient.trips_raw`, `RawTripsResult`, `TripsLookupError(code)` | `test_trips_transport.py` (30): exact URL/header/params/timeout, same counter as search, 0 sends at budget 0 or bad id, each HTTP/transport/JSON failure's code, payload verbatim | 30 passed |
| 4 | 90c4c93 | Three-phase `apply_live`, `metal_pass`, D2 qualification, `LiveOptions.trips_mode=None`/`trips_cap=10`, candidate fields, formatter lines | `test_metal_end_to_end.py` (8): Trip B B4 prints "operating airline: VS by flight number (VS19)", the flights line, the codeshare clause and UNVERIFIED; **auto, off and not-engaged score identically**; every search call precedes every trips call | 8 passed |
| 5 | 27462f1 | Endpoint-aware `ResponseCache` (`for_trips()`, trips manifest, `put(parser_version, secret)`, `assert_no_key_material(key=)`); caching in `trips_raw`; manifest annotation | `test_trips_cache.py` (18): files and an annotated row; 2nd run in TTL = 0 trips calls, same lookup; failures/budget write nothing; envelope per §4.4; flag key grepped absent; search globs blind to trips files; wrong-shape 2xx = same SHAPE_ERROR twice | 18 passed |
| 6 | 55adcc6 | `SnapshotTransport.trips_raw`, `load_trips_replay_set`, trips lines in `manifest_hash`, `build_replay` wiring, banner lines | `test_trips_replay.py` (10): tool-written corpus replays to the live line + replay clause with 0 `requests.get` calls; no `trips_endpoint/` gives NOT RECORDED, same exit code, and the legacy hash; trips rows change the hash; missing / tampered / empty trips snapshot exits 1; old trips parser prints TRIPS LOOKUPS REPARSED | 10 passed |
| 7 | 77281ed | `--trips`, `--trips-cap`, conflicts, single-route footer, banner | `test_trips_flags.py` (24): off = 0 calls; cap 1 = 1 call and "per-run cap of 1" on the rest; budget 0 after the searches = 0 requests; 429 then RATE_LIMITED_EARLIER; `all` looks up Aeroplan; every conflict exits 1 with a reason; banner split "4 search + 3 trips" and label | 24 passed |
| 8 | f4accf6 | `METAL_LOOKUP_MISSING` / `METAL_UNKNOWN` (way ten, COUNTED), totals keys, CARRIER_UNKNOWN dropped when KNOWN, trip-block rows and lines | `test_metal_way_ten.py` (18): imports under -O; both discovered; counts across KNOWN / AMBIGUOUS / UNKNOWN / cap / budget / off / NOT_RECORDED / not-needed / not-partner; keys present when not engaged; `check_trip_level_answers` on the flag scenarios; "operating airline NOT LOOKED UP on B3, B4" | 18 passed |
| 9 | 4d91372 | `data/yq_inclusion.csv` (header only), `src/yq_inclusion.py`, cases A-E in `award_to_candidate`, metal-aware `resolve_leg_surcharge`, `docs/yq-checks/README.md` | `test_yq_inclusion.py` (32): committed table = no change and "NOT ADDED" band line; includes_yq = taxes only, names evidence; excludes_yq + KNOWN VS = taxes + $200-$350 (pt $275), SURCHARGE_MODELED; excludes_yq without metal = SURCHARGE_UNKNOWN; Flying Blue (KL, AF) and AF\|KL resolve, AF+DL does not; 0 and below-APD taxes unscoreable under both; every D23 bad row refused | 32 passed |
| 10 | 99d9e5c | Per-itinerary taxes (`matched_trip_taxes`, `trip_taxes_view`) | `test_trip_taxes.py` (26): unverified unit shows both readings; 0 / -1 / 10^9 / None / "abc" move no number under any verdict; with "cents" monkeypatched the trust rules apply per itinerary, a higher figure makes taxes UNKNOWN and widens, a lower one is disclosed only | 26 passed |
| 11 | e48775e | `PointsCandidate.metal_for_alternatives`, used by `find_same_metal_alternatives` | `test_metal_alternatives.py` (6): VS award on KNOWN AF metal gives an unpriced, partnership-assumed Flying Blue alternative ($75-$125); AMBIGUOUS / UNKNOWN / NOT_LOOKED_UP give none; totals unchanged | 6 passed |
| 12 | 509b330 | `src/trips_tools.py` (`capture`, `yq-check`) | `test_trips_tools.py` (27): call-count line before any request; "n" = 0 calls, exit 1; envelope + `.raw.txt` per §4.8 with the flag key absent; drift report; injected drift = exit 5 and file written; yq-check prints every field, writes a record with blanks, refuses qatar and a 0 tax figure, warns on DL metal | 27 passed |
| 13 | f548c48 | Flip checkers (`schema_verification_problems`, `totaltaxes_unit_problems`), README section, flag rows, `### trips_tools exit codes`, known limitations | `test_trips_verification_label.py` (13), `test_metal_statuses_are_documented.py` (4); full suite, -O and probe red sets | all green |

## Deviations from the plan

Each is the safer reading, taken because the plan was ambiguous or
self-contradictory and there was no one to ask mid-run.

1. **Three sentences reworded so they pass the plan's own word grep.** D13's
   404 and `data: []` prose ("does NOT mean the award has no flights", "NOT a
   finding that the award has no flights") and D11's NOT RECORDED prose ("has no
   trips snapshot") contain "no flights" / "no trips", which Step 1's acceptance
   and §7 item 1 forbid in every non-KNOWN render. Now: "This is NOT a finding
   about whether the award has flights" and "this replay holds no recorded trips
   snapshot for availability {id}". Meaning unchanged.
2. **`--trips-cap` is refused wherever `--trips` is** (with `--offline` and with a
   single-route search), not only with `--from-snapshot`. The plan named only
   `--trips` for those two; ignoring a flag silently is the house failure.
3. **A trips manifest whose rows were all deleted replays as NOT RECORDED**
   instead of being refused as "not a manifest". The plan's own remedy for a
   bad trips snapshot is "delete the row"; with one row that leaves an empty
   manifest, and refusing it would make the remedy impossible. A SEARCH manifest
   with no rows is still refused.
4. **`trips_tools` exits 5 only for drift that blocks the label flip**: a
   required-field failure, an unreadable itinerary, an envelope error, an
   incomplete list, or no itinerary at all. Undocumented keys and odd types on
   optional fields are printed but exit 0. This matches D14 ("no required-field
   drift"), so exit 5 means exactly "do not flip".
5. **yq-check prints BOTH candidate CSV rows** (`includes_yq` and `excludes_yq`)
   and says to add one; the verdict comes from the site comparison, which the
   tool cannot see.
6. **Interpretation, not a change: "could match" is read literally.** An
   unreadable itinerary is ruled out only when its source, cabin AND cost are
   all readable and do not match the award. So an itinerary with an unmapped
   cabin blocks KNOWN even if its source differs. More UNKNOWN, never a guess.
7. **Small additions beyond §4.2's field lists**, all needed to implement the
   plan: `MetalLookup.flight_numbers` and `.matched_trip_taxes`;
   `PointsCandidate.metal_surcharge_note` (the D21 "band NOT ADDED" line);
   `RawTripsResult.raw_text`, `.request_sent` and replay provenance fields;
   `LiveOptions.metal_report` and `.yq_table`; `ResponseCache.put(secret=)`.
   The trust logic for per-trip taxes lives in `seats_trips.trip_taxes_view`
   with the UK-duty check passed in (importing `live_trip` from `seats_trips`
   would be a cycle).
8. **The unknown-surcharge note is reworded when the lookup named metal.** It
   used to say "no operating carrier is recorded" beside "operating airline: VS
   by flight number"; it now says the named metal is not used because YQ
   inclusion is unverified. Text only; no number moves.

## How to run it

Sandbox:

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider
.venv/bin/python -O -m pytest -q -p no:cacheprovider
```

On Tsuki's Mac (key from `~/.zshrc`), from the repo root:

```bash
# A live trip run now also looks up the operating airline (auto, cap 10).
python -m src.main --trip-fixture trip_b_europe.json \
    --balance UR=160000 --card "Chase Sapphire Preferred" --transfer-date 2026-09-15

# A1 - capture one real trips response (at most 2 calls: 1 search + 1 trips).
# Use a date with Virgin Atlantic space on SFO-LHR. Paste the output; commit
# tests/fixtures/seats_aero/trips_endpoint/real/<file>.json and .raw.txt.
python -m src.trips_tools capture --origin SFO --destination LHR \
    --date <YYYY-MM-DD with VS space> --source virginatlantic

# A2 - the YQ check, on a Virgin Atlantic award ON VS METAL, then compare the
# printed block with virginatlantic.com and fill in docs/yq-checks/<date>-virginatlantic.md.
python -m src.trips_tools yq-check --origin JFK --destination LHR \
    --date <YYYY-MM-DD with VS space> --source virginatlantic --cabin J
```

Both tools print the call count and ask `Continue? [y/N]` (skip with `--yes`).
`capture` exit 0 = clean; only then set `TRIPS_SCHEMA_VERIFIED_BY` in
`src/seats_trips.py` to the file name. Exit 5 = file written but the parser must
be fixed first. After filling the yq-check record, add ONE row to
`data/yq_inclusion.csv`; the loader refuses a record that still has `____`.

## Known gaps

- **The parser has never seen a real response.** Everything is built from the
  OpenAPI field list in the handoff; the synthetic example's airports, times,
  prices and ids are placeholders, and says so. Expect drift on the first capture.
- **The cap is checked before the cache.** Once the cap is reached, a lookup a
  disk-cache hit would have served free still reads CAP_REACHED. Cache hits do
  not use up the cap. Same after a 429: every later lookup is skipped, cached or not.
- **The budget counter is per process** (as the plan says); it cannot see other
  runs. The banner's "calls spent" is this process only.
- **Replay does not flag trips rows for legs the trip does not contain**, and if
  two selected rows carry the same availability id, the later one wins silently.
- **`capture --availability-id` with no local row infers the route** from the
  itineraries for the drift report, and says so; the route check is then not
  evidence.
- `--trips` with `--new-trip` but without `--live` is accepted and does nothing
  (no scoring run happens).
- The flag-supplied key guard (`put(secret=)`) covers trips writes only; search
  writes keep their existing environment-key guard.
- The capture tool's search uses the runtime cache and archives nothing into the
  snapshot corpus (deliberate: a tool should not write the committed corpus).

## Out-of-scope observations

- Existing live output still contains "the v0 bug" in the unscoreable-award
  reason (`live_trip._taxes_are_the_whole_carrier_cash_figure`, case E prose,
  unchanged). `test_no_changelog_in_user_output` only scans `--offline` runs, so
  it never sees live text. Worth widening that guard.
- `SeatsClient._budget_remaining()` resets on a date change mid-process; a run
  that crosses midnight UTC would see a fresh budget. Not new.
- Tsuki's uncommitted `live_trip_b/` corpus has no `trips_endpoint/`: replaying
  it will print NOT RECORDED for Virgin Atlantic / Flying Blue / JetBlue /
  KrisFlyer awards, with its hash unchanged.
