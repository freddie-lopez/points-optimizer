# Fix report, round 3: `feature/operating-airline`

**Input:** the Manager review `runs/points-optimizer/manager-review-operating-airline.md`
(commit `b3bc640`, verdict "Ship with fixes").

**Result:** everything the coordinator asked for is done: the three must-fixes,
decision D1 option (b), should-fixes 1, 2 and 6, and the "v0 bug" wording from
5a. Should-fixes 3 (size), 4 (output volume, for the UI round) and 5b (the APD
gate) are **deferred**, as asked.

I did not edit the Tester's probes or report: `git diff b3bc640 HEAD --
docs/test-reports` is empty. 38 operating-airline probes are now red. Every one
of them is caused by a change the Manager asked for; they are listed with the
reason below.

## Counts

**Full suite in the committed tree:** 1513 passed, 13 skipped at `b3bc640`;
**1580 passed, 13 skipped** now, both normally and under `python -O`.

**Full suite in all three label states** (must-fix 1). This was run on a
scratch copy of the tree. In that copy, the constants were edited in
`src/seats_trips.py` and a genuine stubbed capture was placed in `real/`. None
of it was committed.

| state | at `b3bc640` | now, normal | now, `-O` |
|---|---|---|---|
| committed | 1513 passed, 13 skipped | **1580 passed, 13 skipped** | 1580 passed, 13 skipped |
| `TRIPS_SCHEMA_VERIFIED_BY` = the capture | **6 failed**, 1500 passed, 20 skipped | **1571 passed, 22 skipped** | 1571 passed, 22 skipped |
| flipped + `TRIPS_TOTALTAXES_UNIT = "cents"` | **28 failed**, 1478 passed, 20 skipped | **1570 passed, 23 skipped** | 1570 passed, 23 skipped |

What the extra skips in the flipped states are:
- tests that describe the committed constants, such as "the committed unit is
  unverified" and "while the constant is empty";
- the two flip-in-source tests, which skip in a tree that is already flipped.

**Baseline probe red sets:** v5 19, adversarial 40, known-failures 0. All three
are identical by test id to the saved baselines.

**Operating-airline probes:** 0 red of 431 at `b3bc640`; 38 red of 432 now. The
same set is red under `-O`. The reasons are below.

**Regression tests.** There are 10 new test files plus a pinning fixture
(`tests/_trips_label_state.py`). Several test files this branch added were also
edited. I copied all of them onto a `b3bc640` worktree and ran them there: 80
tests fail. Every fix has at least one test that fails on `b3bc640` and passes
now.

Must-fix 1 needs a different kind of proof, because at `b3bc640` it was the
tests that failed, not the code. `tests/test_trips_label_flip_suite.py` makes a
scratch copy with both constants edited in source and runs every dependent test
file in a child pytest. On `b3bc640` it fails (the first failure is
`test_metal_end_to_end::test_b4_prints_vs_by_flight_number_with_every_qualifier`),
and it passes now.

## Fixes, commits and regression tests

| Item | Fix | Commit(s) | Regression test id(s) that fail on `b3bc640` |
|---|---|---|---|
| **MF1** flipping the label turns the suite red | Every test that describes the unverified state now pins both constants with one fixture, `unverified_constants` in `tests/_trips_label_state.py`: 14 tests in 8 files this branch added. `test_the_committed_unit_is_unverified` now skips once the unit is flipped. New `test_trips_label_flip.py` writes a genuine capture to tmp through the stubbed tool, patches both constants to it, and runs the matcher, the banner, a live Trip B run with its printed output, the cents tax rule and the yq-check block. | `bd99187`, `a408eaf`, `34cf2eb` | `test_trips_label_flip_suite.py::test_the_dependent_tests_pass_with_the_label_flipped_in_source`; `::test_they_also_pass_with_the_unit_flipped_to_cents`; plus the three-state full-suite run above |
| **MF2** yq-check decision rule | The block now prints the modelled band for the airline the lookup names (VS J one way: $200-$350) and "row figure + band". It states the rule by the site's **total**: about the row figure is includes_yq; about the row figure plus the band is excludes_yq; anything else is inconclusive and records nothing. It tells him to confirm on the site that the flight is **operated by** that airline (Virgin Atlantic, not Delta). The record's template matches, with a "the site shows this flight operated by VS itself (yes / no)" line, and the loader refuses any answer but "yes". | `ea35daf` | `test_yq_check_decision_rule.py` (all 9) |
| **MF3** tool messages | After drift, capture no longer says "capture again". It says the parser is fixed against the same file, which then verifies with no new call; a test proves this by fixing the parser. Exit 5 names the one test that stays red, `test_every_committed_real_capture_parses_without_required_field_drift`, and says this is expected (for required-field or envelope drift), or says that no test turns red. The clean message and the yq-check row template say to send the files back rather than edit `src/seats_trips.py` or `data/yq_inclusion.csv`. | `6ab8b08` | `test_trips_tool_messages.py` (6 of 7) |
| **MF3** the coder report's how-to-run | Rewritten. The Trip B live run is gone. It uses `.venv/bin/python`, adds the bundle step, and has one `yq-check` run that writes the capture too (2 calls). It gives the rule by the site total and the operator check, says to send the files back and not edit the two files, and explains why exit 5 is fine. The known gap "cap before cache" is marked as fixed in round 1. | `b90101a` | none (a document) |
| **D1 (b)** a verdict covers only its airline | `data/yq_inclusion.csv` is now `source,airline,verdict,verified_on,evidence,notes`, and verdicts are keyed by (source, airline). Cases B and C apply only when the award's own itinerary lookup is KNOWN and names exactly that one airline. AMBIGUOUS, UNKNOWN, NOT LOOKED UP, another airline or a multi-airline itinerary score **exactly as with no row** (checked, numbers and reason codes), and the award's line names the check that exists and why it does not apply. yq-check writes "itinerary lookup status" and "checked airline" into the record and the airline into the row template, and prints no template when the lookup did not name one KNOWN airline. The loader requires the record's lookup to be KNOWN on the row's airline. The README documents it as a decision Tsuki can reverse to option (a). | `40df226` | `test_yq_airline_scope.py` (all 13); `test_yq_inclusion.py::test_a_verdict_covers_only_known_metal_on_its_airline[...]` (8) and `::test_a_verdict_for_another_airline_does_not_reach_vs_metal` |
| **SF1** KrisFlyer in auto | In `--trips auto`, an award from a source in `TAXES_UNREPORTED_SOURCES` (`singapore`, `qatar`, `turkish`) reads the new `NOT_LOOKED_UP` / `NOT_NEEDED_TAXES_UNREPORTED`. It is not counted as a missing lookup, and it is documented in the README table and the auto criteria. `--trips all` still looks it up. | `e36cb32` | `test_trips_auto_skips_unreported_taxes.py` (all 6) |
| **SF2** `--refresh` | capture and yq-check take `--refresh`. It sends the search even when the disk cache holds it, says so in the call-count line, and refreshes the cache entry. With `--availability-id`, which makes no search, it is a usage error. | `fdc4a91` | `test_trips_tools_refresh.py::test_refresh_sends_the_search_even_when_the_cache_holds_it[capture]`, `[yq-check]`; `::test_refresh_with_an_availability_id_is_a_usage_error` |
| **SF6** deleting a trips row changes the hash | Three refusals give "delete the row (or file)" as the remedy: the missing, tampered or empty snapshot refusal; the unreadable trips manifest; and the duplicate lookup. All three now say that doing so changes the replay's manifest hash, so a number quoted against the old hash will not reproduce. The README says the same. | `6a50a3a` | `test_trips_replay_hash_note.py` (all 3) |
| **SF5a** "the v0 bug" in live output | Reworded as user-facing text ("turn an unknown charge into a saving that is not there"). The same scan also found four `data/surcharges.csv` notes saying "the v1 modeled band", which is printed live under case C; they now say "the earlier modelled band". Both are text only. The existing offline no-changelog test predates the feature, so I did not touch it. Instead, new `test_no_changelog_in_live_output.py` runs its scanner over stubbed live output: auto, all, off, and both verdicts. | `07da610` | `test_no_changelog_in_live_output.py` (all 5) |

## Operating-airline probes that went red, and why

All 38 come from changes the Manager asked for. I edited none of them.

| Probes | Count | Why | Asked for by |
|---|---|---|---|
| `test_oa_d_numbers.py`: `test_includes_yq_never_adds_a_band[...]` (8), `test_excludes_yq_adds_exactly_one_band_on_known_single_metal`, `test_excludes_yq_on_carriers_with_different_bands_stays_unknown[ambiguous]`, `[known_vs_dl]`, `test_unconvertible_currency_wins_over_every_verdict[...]` (2), `test_untrusted_taxes_win_over_every_verdict[...]` (8) | 21 | They build `YqVerdict(source, verdict, date, evidence)` positionally. The dataclass now has an `airline` field (a TypeError at construction). Their scoring intent would also change: `includes_yq` on 404, AMBIGUOUS or DL metal is now unscoreable instead of taxes-only. | D1 |
| `test_oa_d_numbers.py::test_the_committed_table_is_still_header_only` | 1 | The header is now `source,airline,verdict,...` | D1 |
| `test_oa_r2_retest.py::test_H1_a_legitimately_filled_verdict_line_loads[...]` (4), `::test_H1_a_record_saved_with_crlf_line_endings_loads` | 5 | Their records have no "itinerary lookup status", "checked airline" or "operated by ... (yes / no)" line, and their CSV has 5 columns. The loader now requires all three lines. | D1, MF2 |
| `test_oa_r2_retest.py::test_the_whole_verdict_flow_a_record_the_tool_wrote_filled_by_hand_loads` | 1 | It fills the old site half (a "separate carrier-imposed charge line" field that is gone) and writes a 5-column CSV. The record template and the row template changed. | MF2, D1 |
| `test_oa_r3_retest.py::test_every_line_naming_looked_up_metal_carries_the_label[excludes_known]`, `[excludes_ambiguous]` | 2 | They build `YqVerdict` positionally without `airline` (TypeError). | D1 |
| `test_oa_i_legacy_and_misc.py::test_not_engaged_output_is_byte_identical_to_master[VS, DL]`, `[VS]` | 2 | Master's output contains "which is the v0 bug", and that sentence was reworded. I checked with a temporary scratch copy, since deleted: after normalising that one sentence, the output is identical to master in both cases. **D29 (byte identity with master) no longer holds, and only for this one sentence.** | SF5a |
| `test_oa_r3_retest.py::test_the_full_cli_output_is_the_round_2_output_plus_the_label_and_nothing_else[...]` (6) | 6 | They compare with `9bf7c90`. Three pieces of text were reworded: the v0 sentence (SF5a), the band note's "no row for 'virginatlantic'", which now reads "... on VS metal" (D1), and the "v1 modeled band" notes (SF5a). I checked with a temporary scratch copy that these are the only differences in all 6 scenarios. | SF5a, D1 |

For the Tester's re-test: the D1-affected probes need verdicts built with
`airline=` and the 6-column CSV, and records need the three new lines. The two
byte-identity probes need the reworded sentence. **Coordinator decision:**
decide whether D29 should now read "identical to master except the reworded
v0 sentence", or whether that sentence should keep its master wording. The
Manager asked for the rewording, and the rewording is what I did.

## Deferred

These were left alone, as the coordinator asked:
- Should-fix 3: size.
- Should-fix 4: output volume, for the UI round.
- Should-fix 5b: the APD verification gate.

## Commits in this round

`bd99187`, `40df226`, `ea35daf`, `6ab8b08`, `e36cb32`, `fdc4a91`, `6a50a3a`,
`07da610`, `b90101a`, `a408eaf`, `34cf2eb`, plus this report. No network call
was made. Every test stubs the transport; the flip-in-source test works on a
tmp copy of the tree, and the child processes run behind
`tests/_child_guard`.
