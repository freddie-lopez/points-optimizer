# Fix report, round 2: `feature/operating-airline`

**Input:** Re-test 2 in `docs/test-reports/operating-airline.md` (commit `9bf7c90`)
found 7 new issues, 2 Medium and 5 Low, with 10 red probes in
`docs/test-reports/operating-airline-probes/test_oa_r2_retest.py`.

**Result:** all 7 are fixed. Nine of the 10 red probes are now green. The one
still red is `test_two_lookups_that_returned_identical_bytes_still_replay`. Its
assertions about the replay pass. Only its precondition fails, and I disagree
with that precondition (see R2-2). I left the Tester's report and probes
unchanged: `git diff 9bf7c90 HEAD -- docs/test-reports` is empty.

## Counts

| | at `9bf7c90` | after the fixes |
|---|---|---|
| suite, normal | 1460 passed, 13 skipped | **1513 passed, 13 skipped** |
| suite, `python -O` | 1460 passed, 13 skipped | **1513 passed, 13 skipped** |
| v5 / adversarial / known-failures red sets | 19 / 40 / 0 | 19 / 40 / 0, identical by id |
| operating-airline probes | 10 red / 377 | **1 red / 377** (the same one red under `-O`) |

`test_the_existing_contract_tests_are_untouched` still passes. Against
`a17497d`, `tests/` shows only `A` files plus `M tests/fixtures/seats_aero/README.md`.

**Regression tests.** There are 7 new files in `tests/`, plus two edits to
`tests/test_trips_tools.py`, a file this branch added. I copied all of them onto
a `9bf7c90` worktree and ran them there: 41 fail and 39 pass. The ones that pass
are guards on behaviour that must not change, for example:
- a genuine capture still flips;
- a row pointed at another id's file is still refused;
- UNKNOWN still says "This lookup established nothing further".

Every finding has at least one test id that fails on `9bf7c90` and passes now.

**My own test edits.** Both are in `tests/test_trips_tools.py`, a branch-added file:
- **The fill step (R2-1).** It now fills only the five field lines, and asserts
  each one appears exactly once. This is the change the coordinator asked for.
- **`test_a_capture_writes_the_envelope_and_the_raw_body_with_no_key`.** Its raw
  body is deliberately not the page. Under R2-4 that file is refused by the
  label check, so the test now expects exit 5 and the "CANNOT FLIP" line. Its
  checks on the envelope and the raw body are unchanged.

## Findings, fixes and regression tests

All test ids are under `tests/`.

| # | Finding | Fix commit | Regression test id(s) that fail on `9bf7c90` |
|---|---|---|---|
| R2-1 M | A record `yq-check` wrote, filled in as instructed, is refused as "still has ____ blanks" | `b9d7db2` | `test_yq_record_roundtrip.py::test_a_record_filled_as_instructed_loads`; `::test_the_marker_appears_only_on_the_five_field_lines`; `test_trips_tools.py::test_the_record_it_writes_is_refused_until_the_blanks_are_filled` |
| R2-2 M | Two ids with byte-identical responses make an honest corpus unreplayable | `5011a04` | `test_trips_identical_bytes_replay.py::test_each_id_gets_its_own_file_even_when_the_bytes_are_identical`; `::test_the_corpus_replays_and_says_empty_for_each_id`; `::test_a_re_fetch_of_the_same_id_with_identical_bytes_still_shares_its_file` |
| R2-3 L | A refused `capture` leaves a one-page search in the cache | `a808e2a` | `test_trips_capture_cache.py::test_a_refused_capture_leaves_no_search_in_the_cache`; `::test_the_next_trip_search_fetches_and_follows_the_pages`; `::test_an_incomplete_entry_the_capture_did_not_write_is_left_alone` |
| R2-4 L | `capture` says CAPTURED CLEAN for a file the label check refuses | `24c06cf` | `test_trips_capture_verdict.py::test_an_inferred_route_capture_exits_5_and_gives_no_flip_advice`; `::test_a_raw_body_that_is_not_the_page_exits_5`; `::test_a_capture_outside_real_exits_5`; `test_trips_tools.py::test_a_capture_writes_the_envelope_and_the_raw_body_with_no_key` |
| R2-5 L | The synthetic page, re-wrapped, still verifies the parser | `2bb58c1` | `test_trips_flip_forgery_limits.py::test_the_synthetic_page_under_the_full_wrapper_is_refused`; `::test_a_synthetic_itinerary_and_id_inside_another_page_are_refused`; `::test_a_raw_body_whose_types_differ_from_the_page_is_refused[60000.0]`; `::test_a_capture_missing_what_the_tool_writes_is_refused[no-request]`, `[other-path]`, `[other-params]`, `[203]`, `[bool-status]`, `[no-parser-version]`, `[no-captured-at]`, `[future]`; `::test_a_capture_whose_row_no_itinerary_matches_is_refused` |
| R2-6 L | "This lookup established nothing further" printed where no lookup was made | `aeaee2a` | `test_trips_single_carrier_wording.py::test_no_lookup_is_described_as_this_lookup[...]`: all 11 NOT_LOOKED_UP and NOT_RECORDED codes |
| R2-7 L | More lines name looked-up metal without the parser label | `347e6d7` | `test_trips_labels_every_metal_line.py::test_default_flow_band_and_surcharge_notes_carry_the_label`; `::test_case_c_lines_carry_the_label`; `::test_alternatives_headline_carries_the_label`; `::test_the_inconclusive_warning_names_looked_up_metal_with_the_label` |

## The choices

**R2-1.** The record's intro prose no longer quotes the blank marker, so only the
five field lines carry it. The loader's check, which refuses any record that
still contains the marker, stays strict. I fixed the tool's sentence rather than
loosening the loader.

**R2-2. I stopped deduplicating trips snapshots across ids; I chose this over
checking ids against the manifest row.** A trips snapshot is now shared only by
re-fetches of the same availability id. So two ids that got identical bytes
each have their own file, and each file's `_meta.availability_id` is the lookup
it records. Search deduplication is unchanged.

Why this keeps tamper detection strongest:
- With cross-id deduplication, an honest corpus has a B4 row pointing at B3's
  empty-list file. That is byte for byte the round-1 L13 attack: a row
  re-pointed at another id's empty file. The only difference is a
  "(re-fetch, identical)" note, and anyone can type that note.
- So any rule that accepts the honest case also accepts the attack. That
  includes comparing the id against the manifest row, or accepting a file
  whose owner also has a row.
- With one file per id, the id check needs no exception, and both L13 probes
  stay green.

The cost is one extra small file per identical response. No committed corpus
holds trips snapshots, so nothing already recorded changes.

**The R2-2 probe:** `test_two_lookups_that_returned_identical_bytes_still_replay`
asserts, as a precondition, that the second manifest row says
"(re-fetch, identical)". In other words, it requires the cross-id deduplication
this fix removes. I think the precondition asserts the wrong thing. It fixes one
implementation, the one under which R2-2 and L13 cannot both be met. I left the
probe as it is. I ran a temporary copy of it with only that line removed (then
deleted the copy): its real assertions pass. The replay exits 0 and prints
"EMPTY itinerary list for availability <B4 id>".
`tests/test_trips_identical_bytes_replay.py` covers the same flow, and also
checks that the L13 refusal still holds on identical bytes.

**R2-3.** When the capture's own one-page search comes back incomplete, it
deletes the cache entry that search just wrote, then refuses. An incomplete
entry an earlier run left behind is not touched, and the message says so. A
complete capture search is still cached. I did not make trip runs ignore
incomplete cached results, because that would change the search path.

**R2-4.** `run_capture` ends with `schema_verification_problems`. It prints
"CAPTURED CLEAN ... set TRIPS_SCHEMA_VERIFIED_BY" (exit 0) only when that check
accepts the file. Any refusal is exit 5, with the check's reasons, and says
"Do NOT set". Exit 5 now means "captured with drift, or refused by the label
check". The module docstring, the `--help` text, the README table and the
fixtures README all say so.

**R2-5: closed the cheap paths only.** The flip check now refuses:
1. the committed synthetic page under any wrapper, any itinerary copied from
   it, and its placeholder id;
2. a raw body whose JSON differs from the page in value types, compared as
   canonical JSON. Python's `==` treats `60000`, `60000.0` and `True` as equal.
   Whitespace, CRLF and key order still pass: the Tester's green probe for
   harmless rewrites stays green;
3. a capture missing the `_meta` the tool always writes: the request path and
   params, HTTP 200, the parser version, and a `captured_at` in the past;
4. a capture where no award in its recorded row is matched KNOWN or AMBIGUOUS by
   the response.

I did not make the raw-body comparison byte-exact. A forger writes whatever
bytes they like, so byte-exact adds no protection. It would only break harmless
re-saves.

The remaining limit is stated plainly in the README, in
`tests/fixtures/seats_aero/README.md` and in `trips_endpoint/README.md`, and in a
code comment: a file built by hand on purpose can still pass, because nothing is
signed. The defence is reviewing the commit that adds a capture.

**R2-6.** With a one-carrier row, NOT LOOKED UP and NOT RECORDED now open with
"No lookup was made on this run". UNKNOWN keeps "This lookup established nothing
further". The carrier domain is unchanged.

**R2-7.** I added the label to every place that names metal the lookup found:
- the band note and the surcharge note, which were the two lines the Tester named;
- the case-C note, and the "on the metal its itinerary lookup found" note;
- the legacy `metal:` line and the surcharge rule line, when
  `carrier_source == seats_aero_trips`;
- the alternatives headline, through a new `Alternative.metal_label`;
- the yq-check INCONCLUSIVE warning.

Two of these lines are only printed for a trips-derived carrier, which needs a
YQ verdict to exist, so no pre-feature output changes.

Some data lines were printed without escaping, so rich markup deleted the
bracketed label from them. They are now escaped: leg notes, flags, warnings,
APD lines, provenance lines and surcharge notes. None of those strings contained
`[` before this feature, so their output is unchanged. The three baseline red
sets and the round-1 byte-identity probes confirm it.

Not changed: the surcharge line's `[{confidence}]` is rich markup, so it prints
as nothing. That is older than this feature and outside its scope.

## Commits in this round

`b9d7db2`, `5011a04`, `a808e2a`, `24c06cf`, `2bb58c1`, `aeaee2a`, `347e6d7`, plus
this report. No network call was made. Every test stubs the transport and writes
only under `tmp_path`.

## Operating-airline probe red set

**Before (10, at `9bf7c90`), all in `docs/test-reports/operating-airline-probes/test_oa_r2_retest.py`:**

```
test_M3_class_every_line_that_names_trips_metal_carries_the_parser_label
test_a_line_about_a_lookup_that_was_never_made_does_not_speak_of_this_lookup[not_looked_up-CAP_REACHED]
test_a_line_about_a_lookup_that_was_never_made_does_not_speak_of_this_lookup[not_looked_up-RATE_LIMITED_EARLIER]
test_a_line_about_a_lookup_that_was_never_made_does_not_speak_of_this_lookup[not_looked_up-TRIPS_OFF]
test_a_line_about_a_lookup_that_was_never_made_does_not_speak_of_this_lookup[not_recorded-NO_TRIPS_SNAPSHOT]
test_a_refused_capture_does_not_leave_a_truncated_search_for_the_next_trip_run
test_capture_never_says_clean_and_set_the_constant_for_a_capture_the_flip_check_refuses
test_the_committed_synthetic_page_cannot_verify_the_parser_under_any_wrapper
test_the_whole_verdict_flow_a_record_the_tool_wrote_filled_by_hand_loads
test_two_lookups_that_returned_identical_bytes_still_replay
```

**After (1):**

```
test_two_lookups_that_returned_identical_bytes_still_replay   (fails its dedup precondition only; see R2-2)
```
