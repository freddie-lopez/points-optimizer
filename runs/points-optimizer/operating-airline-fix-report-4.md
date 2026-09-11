# Fix report, round 4: `feature/operating-airline`

**Input:** Tester re-test 4 (commit `34509a5`). It raised one finding, **R4-1
(Medium)**, and one observation: changelog text in `--help` and in two replay
refusal messages.

**Result:** R4-1 is fixed, and so is the class it belongs to. The changelog text
is reworded, and the live-output scan now covers it. I did not edit the Tester's
probes or report: `git diff 34509a5 HEAD -- docs/test-reports` is empty.

## Counts

**Full suite.** "At `34509a5`" is the round-3 tree the Tester re-tested. The
flipped states were run on a scratch copy: a genuine stubbed capture in `real/`
and the constants edited in `src/seats_trips.py`. That copy was not committed.

| state | at `34509a5` | now, normal | now, `-O` |
|---|---|---|---|
| committed | 1580 passed, 13 skipped | **1603 passed, 13 skipped** | 1603 passed, 13 skipped |
| `TRIPS_SCHEMA_VERIFIED_BY` = the capture | 1571 passed, 22 skipped | **1594 passed, 22 skipped** | 1594 passed, 22 skipped |
| flipped + `TRIPS_TOTALTAXES_UNIT = "cents"` | 1570 passed, 23 skipped | **1593 passed, 23 skipped** | 1593 passed, 23 skipped |

**Baseline probe red sets:** v5 19, adversarial 40, known-failures 0. All three
are identical by test id to the saved baselines.

**Operating-airline probes:** 1 red of 496 at `34509a5` (R4-1). Now 15 red of
496, and the same 15 under `-O`. The R4-1 probe is green. The 15 are listed
below. They are red because of the band-line requirement the coordinator asked
for, and nothing else.

**Regression tests.** New `tests/test_yq_check_no_band.py` (19 tests) and 3 new
tests in `tests/test_no_changelog_in_live_output.py`. Run on a `34509a5`
worktree:
- 13 of the 19 R4-1 tests fail there and pass now.
- 2 of the 3 changelog tests fail there: `--help` for `src.main`, and the two
  refusals. The third, `trips_tools --help`, was already clean and is a guard.
- The 6 R4-1 tests that pass on `34509a5` are guards. Four of them assert that
  a record whose status is AMBIGUOUS, UNKNOWN, NOT LOOKED UP or NONE backs no
  verdict. That was already refused by D1, which confirms the class was
  covered.

## R4-1: a check that cannot tell includes from excludes backs no verdict

What was wrong: when no nonzero modelled band existed for the airline, cabin
and route, yq-check still printed the row template, and the loader never read
the band line. The Tester's repro was VS W on JFK-LHR: the table has a VS band
only for J on NA-EU. So a record that says in its own words "the site total
cannot tell includes from excludes" backed an `includes_yq` row. D1 would then
have applied that row to every VS award.

Fix (commit `7f839c2`, test tweak `f72ba78`):

1. **yq-check prints no row** when there is no nonzero band. In place of the
   rule it prints "THIS CHECK CANNOT BACK A VERDICT: ...". It then:
   - names the reason: "no nonzero carrier surcharge band is modelled for VS
     metal under Virgin Atlantic Flying Club, cabin W, on this route, so a site
     total equal to the row figure is also what a fare with no carrier
     surcharge would show";
   - points to where the table does model a band, read from
     `data/surcharges.csv`: "For VS metal the surcharge table models a band for:
     cabin J on NA-EU routes - run the check there". When the airline has no
     band anywhere under the program (a VS award on a DL flight), it says that
     no yq-check on that airline can settle the question.
2. **A machine-readable marker.** yq-check writes the line
   `- yq-check: NO VERDICT POSSIBLE - <reason>` into the record's Seats.aero
   half. It writes the same marker when the lookup names no single KNOWN
   airline.
3. **The loader** (`_check_record_band` in `src/yq_inclusion.py`) refuses a
   record in any of these cases, whatever its verdict line says:
   - it carries the marker (matched case-insensitively);
   - it does not have exactly one `modelled carrier surcharge band:` line;
   - its band line has no dollar amount above zero (`NONE MODELLED`, `$0`,
     `$0-$0`).
4. **The class.** A record whose itinerary lookup status is anything but KNOWN
   on the checked airline was already refused by D1 (status and checked-airline
   lines). `test_the_class_any_status_but_known_backs_no_verdict[...]` passes
   on `34509a5` too, which confirms this. Such a check now also writes the
   marker and prints no row.

The README YQ section and `docs/yq-checks/README.md` state the rule. The coder
report's Mac step now says the check must be J on a US-Europe route, the only
place a VS band is modelled.

**Regression tests that fail on `34509a5`:**
- `test_yq_check_no_band.py::test_a_cabin_with_no_band_prints_no_row_and_names_the_reason`
- `::test_the_record_carries_the_machine_readable_marker`
- `::test_the_record_filled_honestly_still_backs_no_verdict` (the Tester's end-to-end repro)
- `::test_an_airline_with_no_band_anywhere_says_no_check_on_it_can_settle`
- `::test_no_single_known_airline_writes_the_marker_too`
- `::test_the_loader_refuses_a_record_without_a_nonzero_band_or_with_the_marker[...]`
  (7 cases: no band line, two band lines, NONE MODELLED, $0, $0-$0, the marker,
  the marker in other case)
- `::test_the_marker_the_tool_writes_is_the_one_the_loader_refuses`

Four of my own branch-added test files were updated because their hand-written
records need the band line: `test_yq_inclusion.py`, `test_yq_record_checks.py`,
`test_yq_airline_scope.py` and `test_yq_check_decision_rule.py`. The DL-metal
row-template case moved to `test_yq_check_no_band.py`, because DL metal has no
band under Virgin Atlantic and now prints no template.

## Changelog text in `--help` and two replay refusals

Commit `63f8d6b`. Text only; no exit-code line changed.

- **`src.main --help`:** removed "as of v5" (three places), the "(v3)" and
  "(v5)" group titles, "DEFAULTS AS OF v5", and "Kept for v0 compatibility",
  which now reads "Kept so older scripts keep working".
- **Old-row refusal:** "this row predates v5 ... write a v5 row" now reads "this
  row was written by an older version of the tool ... write a row that has one".
- **Archived-budget refusal:** drops "(finding H-1)".
- **Tests:** `test_no_changelog_in_live_output.py` gained
  `::test_no_release_reference_in_help[src.main]`, `[src.trips_tools]` and
  `::test_no_release_reference_in_the_old_row_and_budget_refusals`. They use the
  existing scanner; the pre-feature offline test is untouched.

## The 15 red probes, and why

All 15 build records with the probe conftest's `yq_record_body`, which has no
`modelled carrier surcharge band:` line. The loader now refuses such a record:
"has 0 'modelled carrier surcharge band' lines". The coordinator asked for this
("refuse any record whose band is missing").

I checked it with a temporary scratch copy of the probes, since deleted. With
only one change, that helper writing the line
`- modelled carrier surcharge band: $200-$350 (pt $275) one way (VS metal, cabin J)`,
all 219 tests in `test_oa_d_numbers.py`, `test_oa_r2_retest.py` and
`test_oa_r4_retest.py` pass.

- `test_oa_d_numbers.py::test_a_record_for_one_source_cannot_back_a_verdict_for_another`
- `test_oa_d_numbers.py::test_the_validator_accepts_the_same_good_row_it_refuses_variants_of`
- `test_oa_r2_retest.py::test_H1_a_legitimately_filled_verdict_line_loads[...]` (4)
- `test_oa_r2_retest.py::test_H1_a_record_saved_with_crlf_line_endings_loads`
- `test_oa_r4_retest.py::test_a_plain_yes_in_any_case_loads[...]` (4)
- `test_oa_r4_retest.py::test_airline_column_case_and_whitespace_normalise[...]` (4)

**For the Tester's re-test:** add the band line to `yq_record_body`.

## Commits in this round

`7f839c2`, `63f8d6b`, `15560f9` (the coder report's J-only note and the
expected count), `f72ba78`, plus this report. No network call was made.
