# Fix report, round 1: `feature/operating-airline`

**Input:** Tester report `docs/test-reports/operating-airline.md` and probes
`docs/test-reports/operating-airline-probes/` (commit `3dfe404`): 1 High, 4 Medium,
17 Low.
**Result:** all 22 findings are fixed. No Low was left unfixed. I disagree with no
probe. The Tester's report and probes are unchanged
(`git diff 3dfe404 HEAD -- docs/test-reports` is empty).

## Counts

| | at `3dfe404` | after the fixes |
|---|---|---|
| suite, normal | 1376 passed, 13 skipped | **1460 passed, 13 skipped** |
| suite, `python -O` | 1376 passed, 13 skipped | **1460 passed, 13 skipped** (the one warning is pytest's standard `-O` notice) |
| v5-probes red set | 19 | 19, same ids |
| adversarial-probes red set | 40 | 40, same ids |
| known-failures-probes red set | 0 | 0 |
| operating-airline probes | 36 red / 296 | **0 red / 296** |

The baseline red sets were compared by test id against the saved baselines, and all
three are identical. `test_the_existing_contract_tests_are_untouched` passes:
compared with `a17497d`, `tests/` still shows only `A` files plus
`M tests/fixtures/seats_aero/README.md`.

**Regression tests:** there are 12 new files in `tests/` (95 tests). I copied them onto a
`3dfe404` worktree and ran each file there: 66 fail, and 29 pass. The 29 that pass
are guards that pin behaviour that must *not* change, for example that a matching
record still loads, a header-only trips manifest still reads NOT RECORDED, and
`--trips all` still looks up a party leg. Every finding has at least one test id
that fails on `3dfe404` and passes now; they are listed below.

**My own tests updated:** three test files were already added on this branch, and I
edited them because their fixed behaviour changed on purpose. All three are still
status `A` against `a17497d`, so no pre-feature test was touched.
- `tests/test_yq_inclusion.py`: `GOOD_RECORD` is now a full record, with a title,
  a program line and a verdict line.
- `tests/test_trips_tools.py`: it now expects the `<VERDICT>` template.
- `tests/test_trips_flags.py`: it now expects the new 429 banner text and the
  "earlier request" wording.

## Findings, fixes and regression tests

All test ids are under `tests/`.

| # | Finding | Fix commit | Regression test id(s) that fail on `3dfe404` |
|---|---|---|---|
| 1 H | A record that says `excludes_yq` or `inconclusive` backs an `includes_yq` row | `f3f0869` | `test_yq_record_checks.py::test_a_record_that_does_not_say_the_rows_verdict_is_refused[excludes_yq]`, `[inconclusive]`, `[]`, `[maybe]`, `[INCONCLUSIVE]`; `::test_an_includes_record_cannot_back_an_excludes_row`; `::test_a_record_with_no_verdict_line_or_two_is_refused` |
| 2 M | A record for one source backs a verdict for another | `f3f0869` | `test_yq_record_checks.py::test_a_record_for_one_source_cannot_back_another`; `::test_a_record_whose_program_line_names_another_source_is_refused`; `::test_a_record_with_no_source_title_is_refused` |
| dev 5 | yq-check printed both candidate CSV rows | `f3f0869` | `test_yq_record_checks.py::test_yq_check_prints_no_row_with_a_verdict_in_it`; `::test_the_printed_template_pasted_unchanged_is_refused` |
| 3 M | Alternatives and the yq-check block carry no UNVERIFIED label or marketing caveat | `356a612` | `test_trips_labels_everywhere.py::test_an_alternative_from_trips_metal_carries_the_label_and_the_marketing_caveat`; `::test_the_yq_block_flights_and_carrier_lines_carry_the_label` |
| 4 M | The availability-id gate accepts `"<id>\n"` | `13a0c78` | `test_trips_availability_id_gate.py::test_whitespace_around_an_id_is_refused[2mB3kQx9LpTz7VwYc4Hn8RsDfGj\n]`; `::test_the_transport_sends_nothing_for_a_newline_id`; `::test_a_row_whose_id_ends_in_a_newline_is_not_looked_up_and_writes_no_row` |
| 5 M | A paginated trips list can read KNOWN | `4688c63` | `test_trips_coverage_signals.py::test_every_signal_the_search_transport_honours_blocks_known[extra0]`, `[extra1]`, `[extra2]`, `[extra4]`; `::test_search_and_trips_read_the_signals_through_one_function` |
| 6 L | The stored/recomputed coverage union is dropped | `4688c63` | `test_trips_coverage_signals.py::test_a_stored_incomplete_flag_is_honoured_on_a_cache_hit` |
| 7 L | `count` larger than the list is not incomplete | `4688c63` | `test_trips_coverage_signals.py::test_a_count_larger_than_the_list_is_incomplete` |
| 8 L | The cap stops counting when an unexpected exception escapes after sending | `db2e5ca` | `test_trips_rate_limit_and_cap.py::test_an_unexpected_failure_after_sending_still_counts_against_the_cap` |
| 9 L | A 429 on a search does not stop the lookups | `db2e5ca`, wording `1990866` | `test_trips_rate_limit_and_cap.py::test_a_429_on_a_search_stops_every_trips_request` |
| 10 L | Some lookups are paid for and printed nowhere | `7401964` | `test_trips_paid_lookups_shown.py::test_auto_spends_no_trips_call_on_a_leg_for_two_travellers`; `::test_a_paid_lookup_for_a_non_chosen_award_is_printed` |
| 11 L | A single-carrier award reads "nothing known" while scoring treats it as known metal | `7401964` | `test_trips_paid_lookups_shown.py::test_a_single_carrier_known_metal_award_keeps_its_metal_line` |
| 12 L | A truncated trips manifest is read as "no lookups recorded" | `ca467a0` | `test_trips_replay_integrity.py::test_a_trips_manifest_without_its_table_is_refused[zero-bytes]`, `[merge-conflict]`, `[other-text]` |
| 13 L | The replay transport never checks which id its file records | `ca467a0` | `test_trips_replay_integrity.py::test_a_row_pointing_at_another_ids_recording_is_refused`; `::test_a_recording_that_names_no_id_is_refused`; `::test_the_replay_transport_refuses_a_file_about_another_id` |
| 14 L | Duplicate ids, and rows for a leg not in the trip | `ca467a0` | `test_trips_replay_integrity.py::test_one_id_recorded_twice_with_different_bytes_is_refused`; `::test_a_row_for_a_leg_the_trip_does_not_fly_is_refused[no-such-leg]`, `[hotel-leg]` |
| 15 L | Cap before cache, and a 429 skipped cached lookups; the README promised free cache hits | `db2e5ca` | `test_trips_rate_limit_and_cap.py::test_a_cache_hit_after_the_cap_is_spent_is_still_read`; `::test_a_cache_hit_after_a_429_is_still_read_and_nothing_more_is_sent` |
| 16 L | The label-flip checks are weaker than the README | `7abef60` | `test_trips_flip_evidence.py::test_a_raw_body_that_does_not_match_the_page_blocks_the_flip[empty]`, `[blank]`, `[not-json]`, `[other-body]`; `::test_the_synthetic_example_rewrapped_as_real_cannot_flip`; `::test_an_inferred_route_capture_cannot_flip`; `::test_a_capture_whose_row_is_another_ids_cannot_flip`; `::test_a_capture_that_does_not_say_how_its_route_was_found_cannot_flip`; `::test_a_hand_written_file_cannot_flip_cents`; `::test_a_genuine_capture_with_its_raw_body_deleted_cannot_flip_cents` |
| 17 L | `capture` promises at most 2 calls, but its search paginates | `4da7fb1` | `test_trips_capture_guards.py::test_a_paginating_search_is_refused_within_the_promised_calls`; `::test_a_first_page_that_holds_the_row_but_says_there_is_more_is_refused` |
| 18 L | The capture filename uses unsanitized API strings | `4da7fb1` | `test_trips_capture_guards.py::test_an_inferred_route_with_a_slash_is_sanitized_not_a_crash`; `::test_a_row_route_with_path_characters_is_sanitized` |
| 19 L | A closed stdin at the prompt gives a traceback | `4da7fb1` | `test_trips_capture_guards.py::test_no_answer_at_the_prompt_is_a_clean_refusal[EOFError]` (on `3dfe404`, `[KeyboardInterrupt]` aborts the pytest session) |
| 20 L | yq-check offers figures the tool does not believe | `62319c4` | `test_yq_check_untrusted_taxes.py::test_an_untrusted_row_figure_is_refused_before_the_trips_call[negative]`, `[unconvertible]`, `[GBP-5-below-APD]`, `[one-cent]` |
| 21 L | `MixedCabinPct > 0` under `min_cabin_pct=100` is not drift | `66f8af4` | `test_trips_mixed_cabin_drift.py::test_a_nonzero_mixed_cabin_pct_is_required_drift[1]`, `[30]`, `[100]`; `::test_a_capture_with_a_mixed_itinerary_exits_5_and_cannot_flip` |
| 22 L | `--trips-cap ²` gives no named reason | `10a71f9` | `test_trips_cap_flag_unicode.py::test_a_cap_that_is_not_a_plain_whole_number_names_why[\xb2]`, `[\xb3]`, `[\xb9⁰]` |

## What each fix does, where the choice mattered

**(a) H1: the record's verdict must agree with the CSV row.** `yq_inclusion.load`
reads the record's own statements and refuses the row in each of these cases:
- the record does not have exactly one verdict line;
- the verdict line is blank, `inconclusive`, or not a known verdict;
- the verdict line differs from the CSV row ("the record says X and the row says Y").

**(b) M2: evidence must match its source.** The record's title must name exactly
one source. The set of sources named in the title and on the program lines must
equal the row's source.

**(c) Deviation 5: the safer option.** yq-check now prints one row, a template
with a literal `<VERDICT>` and the instruction to copy the word from the record's
verdict line. It never prints a pre-filled verdict. I rejected the alternative,
having the loader derive the verdict from the record and dropping the CSV
column, for two reasons:
- Two independent statements that must agree are stronger evidence than one
  statement read twice. A typo in either now refuses the row.
- Removing the `verdict` column would change the committed CSV format, a one-way
  door the plan settled.

A template pasted unchanged is refused, because `<VERDICT>` is not a verdict.

**(d) Both label-flip gaps are closed.** `schema_verification_problems` now requires:
- a non-empty `.raw.txt` that parses to JSON equal to the capture's page;
- a recorded `availability_row` whose `ID` is the captured id;
- `route_inferred_from_itineraries` to be exactly `False`.

So an inferred-route `capture --availability-id` cannot flip the label, and
neither can a re-wrapped synthetic example. `totaltaxes_unit_problems("cents")`
now considers only captures that pass that check. The README's flip text says
all of this.

**(e) M5: the same signals, from shared code.** `seats_client.pagination_signals`
is the one reader of `hasMore` / `has_more`, `cursor` / `next_cursor` and `skip`.
Search pagination (`_next_page_params`) and `seats_trips.trips_coverage` both
call it. `trips_coverage` also treats a `count` larger than the list as a sign
there is more (L7).

**(f) README.** The cap-before-cache and 429 gaps were fixed rather than
documented (L15). Cache hits are read after the cap or a 429, so "Cache hits are
free" is now true. The Known limitations bullet describes the new behaviour: a
429 on any search or lookup stops every later trips request, and cached answers
are still read.

**Other behaviour changes, each a narrowing:**
- L10: `auto` no longer looks up an award on a leg for 2+ travellers. Such a leg
  is WITHHELD (exit 3) and never scored, so a lookup there spends a call that
  nothing reads. The award reads `NOT_NEEDED_PARTY`, and `--trips all` still
  looks it up. Paid lookups for non-chosen candidates now print under "other
  live award ... (not the chosen option)".
- L17: `capture` caps its search at one page on its own client (the class default
  is untouched). An incomplete search is refused before the trips call.
- L21: a nonzero `MixedCabinPct` is blocking drift. A capture that holds one
  exits 5 and cannot flip the label; the matcher still excludes the itinerary.
  This also closes the Tester's note on deviation 4.
- L22: `int()` runs inside the check, and the `isdigit` gate is kept, so `+5`
  and `1_0` stay refused and `５` stays accepted.
- L12-L14: only a trips manifest whose table header is intact and whose rows were
  deleted reads as "no lookups recorded". This is deviation 3 as originally
  argued. The replay refuses all of these:
  - a zero-byte or non-manifest file;
  - a row whose file records another id, or no id;
  - a row for a leg the trip does not fly;
  - one id recorded twice with different bytes. The same bytes recorded twice is
    not a conflict.

## Commits in this round

`f3f0869`, `356a612`, `13a0c78`, `4688c63`, `db2e5ca`, `7401964`, `ca467a0`,
`7abef60`, `4da7fb1`, `62319c4`, `66f8af4`, `10a71f9`, `1990866`, plus this
report. No network call was made. Every test stubs the transport and writes only
under `tmp_path`.

## Operating-airline probe red set

**Before (36, at `3dfe404`), all in `docs/test-reports/operating-airline-probes/`:**

```
test_oa_b_parser.py::test_a_count_larger_than_the_list_is_incomplete
test_oa_b_parser.py::test_an_id_with_a_trailing_newline_is_refused[ABCDEFGHIJKLMNOP\n]
test_oa_b_parser.py::test_an_id_with_a_trailing_newline_is_refused[B4virxxxxxxxxxxxxxxxxxxxxxx\n]
test_oa_b_parser.py::test_pagination_signals_the_search_transport_honours_are_honoured_here[hasMore-1]
test_oa_b_parser.py::test_pagination_signals_the_search_transport_honours_are_honoured_here[hasMore-str1]
test_oa_b_parser.py::test_pagination_signals_the_search_transport_honours_are_honoured_here[has_more-yes]
test_oa_b_parser.py::test_pagination_signals_the_search_transport_honours_are_honoured_here[skip-str]
test_oa_c_budget.py::test_a_request_that_fails_outside_the_named_errors_still_counts_against_the_cap
test_oa_c_budget.py::test_a_search_429_stops_trips_requests_in_the_same_run
test_oa_c_budget.py::test_admitted_gap_a_429_also_skips_lookups_the_cache_could_answer
test_oa_c_budget.py::test_admitted_gap_a_free_cache_hit_after_the_cap_reads_not_looked_up
test_oa_d_numbers.py::test_a_couple_leg_that_can_never_be_scored_spends_no_trips_call
test_oa_d_numbers.py::test_a_record_for_one_source_cannot_back_a_verdict_for_another
test_oa_d_numbers.py::test_an_alternative_built_from_trips_metal_carries_the_unverified_caveats
test_oa_e_cache_keys.py::test_a_cache_entry_that_records_incomplete_never_reads_known
test_oa_e_cache_keys.py::test_a_newline_id_builds_no_request_and_no_manifest_row
test_oa_f_replay.py::test_a_row_pointing_at_another_ids_recording_is_not_attributed_to_this_id
test_oa_f_replay.py::test_a_trips_row_for_a_leg_not_in_the_trip_is_not_used_silently
test_oa_f_replay.py::test_a_truncated_trips_manifest_is_refused_not_read_as_no_lookups
test_oa_f_replay.py::test_two_rows_for_one_availability_id_are_not_resolved_silently
test_oa_g_tools.py::test_a_closed_stdin_at_the_prompt_is_a_clean_refusal
test_oa_g_tools.py::test_a_forged_real_capture_from_the_synthetic_example_cannot_flip_the_label
test_oa_g_tools.py::test_an_inferred_route_capture_cannot_flip_the_label
test_oa_g_tools.py::test_api_supplied_airport_codes_cannot_steer_or_crash_the_capture_write
test_oa_g_tools.py::test_the_cents_flip_rests_only_on_a_genuine_capture
test_oa_g_tools.py::test_the_promised_maximum_holds_when_the_search_paginates
test_oa_g_tools.py::test_the_yq_block_carrier_line_carries_the_unverified_label
test_oa_g_tools.py::test_yq_check_refuses_a_tax_figure_the_tool_itself_does_not_believe[-100]
test_oa_g_tools.py::test_yq_check_refuses_a_tax_figure_the_tool_itself_does_not_believe[500]
test_oa_h_guards_docs.py::test_every_bad_flag_exits_1_with_a_named_reason_and_no_traceback[argv_extra10-1 to 50]
test_oa_h_guards_docs.py::test_the_readme_does_not_promise_a_free_cache_hit_the_code_does_not_give
test_oa_i_legacy_and_misc.py::test_a_lookup_that_was_paid_for_is_shown_somewhere
test_oa_i_legacy_and_misc.py::test_a_record_whose_own_verdict_disagrees_with_the_row_is_refused[excludes_yq]
test_oa_i_legacy_and_misc.py::test_a_record_whose_own_verdict_disagrees_with_the_row_is_refused[inconclusive]
test_oa_i_legacy_and_misc.py::test_a_single_carrier_row_the_scorer_treats_as_known_metal_is_not_printed_as_nothing_known
test_oa_i_legacy_and_misc.py::test_mixed_cabin_itineraries_under_min_cabin_pct_100_are_recorded_as_drift
```

**After: none.** All 296 probes pass.

The two `admitted_gap_*` probes pass because the gap is gone. They assert the
fixed behaviour (a cached answer is read), so a pass means the fix holds.
