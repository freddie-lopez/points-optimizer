# Tester report: `fix/known-failures`

Branch `fix/known-failures` at `a532e89` (8 commits on `ad83802`). Tester pass,
2026-09-10. Everything below was executed, not inferred. No application code was
touched and no network call was made: every live path ran against a stubbed
`requests.get` serving rows built from the committed SFO-MAD capture.

Probes: `docs/test-reports/known-failures-probes/` (outside `testpaths`). Each
probe asserts the CORRECT behaviour, so a RED probe is a defect still present.

```
.venv/bin/python -m pytest -q -p no:cacheprovider docs/test-reports/known-failures-probes
```

Recorded state: **22 red / 56 green** (sandbox, and the simulated Mac with the key
exported). **23 / 55** on the simulated Mac when the key is kept in a file and not
exported: the extra red is Finding 11.

## Summary

| # | Sev | Title | New on this branch? |
|---|---|---|---|
| 1 | **High** | Single-route search prints unknown taxes as `$0.00` and ranks on it | **Regression** for 3 sources; pre-existing otherwise |
| 2 | **High** | Known gap 1 is bigger than reported: a rejected unknown-tax award sets the optimistic end with taxes AND APD at $0, labelled "surcharge" | Not fixed (master was worse) |
| 3 | **High** | A UK-departure tax figure smaller than the APD owed is believed: POINTS verdict, single-number margin, wrong answer | Pre-existing; same class as the 0 rule |
| 4 | Medium | `PARSER_VERSION` not bumped: same hash, same parser version, different answer, no REPARSED banner | **Yes** |
| 5 | Medium | Stale pre-APD break-even ($182.00) printed beside the moved one ($43.89) | **Yes** |
| 6 | Medium | Negative taxes go down the "unconvertible" branch: APD left out of the floor, and "no FX rate" said of USD | Partly |
| 7 | Medium | Search mode says "Seats.aero returned no award availability" when it returned a Qatar award | Pre-existing |
| 8 | Low | A leg whose only unknown is TAXES is still reported as an unknown SURCHARGE in 4 places (and counted twice) | Yes |
| 9 | Low | Replayed legs: provenance cell reads `none`, and the "NONE USABLE ... NOT $0" block is not printed | Pre-existing |
| 10 | Low | Indirect verdict hides an unattributed award at trip level | Yes |
| 11 | Low | `src.config` reads the developer's `.env` files at collection time; module-level test code can see the key | Pre-existing, not closed |
| 12 | Low | Loader: 4 inputs still traceback or lose the filename; a bad cash-option date is silently the leg's own date | Pre-existing |
| 13 | Low | README's "five PAY CASH sub-states" table leaves out the new verdict (and three older ones) | Yes (for the new one) |

The Coder's six "Known gaps", sized:
1. **Gap 1 (rejected alternative + APD):** larger than claimed. See Finding 2.
   The floor lacks APD **and** lacks any "taxes" label. It moves the trip's high
   end by 4.4 to 4.6 points on Trip B, and the leg prose calls the unknown a surcharge.
2. **Way eleven:** as stated. Finding 3 is a sharper case of it on the scored side.
3. **Zero-is-unknown:** a reasonable judgement. It is also an island. One cent
   gets past it (Finding 3).
4. **sitecustomize:** chaining to a second sitecustomize works (probe). macOS
   builds not verified. I have no Mac.
5. **No real corpus in the repo:** as stated.
6. **`legs_award_unattributed` not printed:** confirmed. Finding 10 makes it worse.

---

## Findings

### 1. High: single-route search prints unknown taxes as `$0.00`, ranks on it, and names it the top strategy

**Repro:** `test_kf_taxes.py::test_search_mode_never_prints_unknown_taxes_as_zero_dollars`,
`::test_search_mode_does_not_rank_an_unknown_tax_award_first_on_a_zero`. By hand:
`--origin SFO --destination MAD --date 2027-01-15 --balance UR=160000 --card "Chase Sapphire Preferred"`
with the stub returning a `singapore` row (52,000 miles, `YTotalTaxes: 15000` USD)
and a `united` row (50,000 miles, `5600` USD).

**Expected:** the KrisFlyer taxes are UNKNOWN, and the output says so (the rule this
branch introduces). Nothing is ranked on a $0 it made up.

**Actual (branch):**
```
│ 1 │ Singapore Airlines KrisFlyer │ ... │ 52,000 │ 0 │ $0.00 │ $520.00 │
│ 2 │ United MileagePlus           │ ... │ 50,000 │ 0 │ $56.00 │ $556.00 │
Top strategy cash cost: $0.00
```
**Master, same input:** United #1 at $556.00, KrisFlyer #2 at $150.00 / $670.00.

This branch correctly stops believing the figure. `optimize()` (which the branch did
not touch) then turns `cash_component_known=False` into `cash_cost = 0.0`, and
`print_strategies` / `print_summary` render it with `_money`. This is the house
failure: unknown becomes $0.00 and wins the ranking. The HTML export gets it right
(`_html_cash_cell`). The terminal does not. The same thing already happened on
master for missing and unconvertible taxes (verified). The branch widens it to
every row from the three documented sources and to every 0.

**Location:** `src/optimizer.py:356` (`cash_cost = award.cash_component if cash_known else 0.0`),
`src/formatter.py:1226`, `src/formatter.py:1246`.

### 2. High: known gap 1 is bigger than reported

**Repro:** `test_kf_taxes.py::test_gap1_*` (4 probes). B4 LHR-SFO has two awards: United
27,600 with $224.63 known taxes (scored, and chosen) and United 20,000 with no tax
figure (not scored, rejected).

**Expected:** the rejected award's floor is at least 20,000 pts + APD =
$338.11. It has no TotalTaxes for the duty to hide in, so this is the same rule the
branch applies to a chosen award. The trip label names award TAXES as part of the
spread.

**Actual:**
- `points_floor_usd = 200.00`: taxes at $0 and APD at $0. That floor becomes B4's
  contribution to the optimistic end, and the headline prints `0.00% - 9.02%`. With
  the duty that is certainly owed, the most it can be is 4.60%.
- `legs_taxes_unknown_ids == []`. It is keyed on the CHOSEN candidate
  (`optimizer.py:2247`), so the label reads only "high end = only if every unknown
  surcharge is $0". Nothing anywhere says TAXES.
- The leg warning says `NOT SCORED - its carrier-imposed surcharge is UNKNOWN (not $0)`.
  United's surcharge is a known $0. What is unknown is the taxes.
- Worse when points win: a scored United 20,000 + $50 ($250) beside a KrisFlyer
  10,000 with unreported taxes gives `7.42% - 12.22%`. The high end rests on $100
  for B4. With the owed APD the bound is $238.11, and the high end is at most 7.80%.
- The way-ten guard passes this run. The unknown is filed as `SURCHARGE_UNKNOWN` with
  a `label`, so "widens the range" is satisfied, and nothing checks what the widening
  assumes.

**Location:** `src/optimizer.py:1040-1060` (floor and warning for rejected
alternatives), `src/optimizer.py:1900` (APD added only through the chosen award's
path), `src/optimizer.py:2247` (the `taxes_unknown` predicate).

### 3. High: a UK-departure tax figure below the APD owed is believed and scored

**Repro:** `test_kf_taxes.py::test_a_uk_departure_tax_figure_below_the_apd_owed_is_not_believed[1|500]`.
B4 is United 35,000 with `YTotalTaxes: 1` (1 cent) or `500` ($5.00).

**Expected:** GBP 102 of APD ($138.11) is owed on this ticket, so a figure below
that cannot contain it and is provably incomplete. The points side is at least
$488.11 against $482.00 cash, so the answer is cash.

**Actual:** `verdict='points'`, score $350.01 / $355.00, and the headline is a
**single number**, `Optimizer beats paying cash by 4.22% (live)`. APD is "STATED and
NOT ADDED" because the TotalTaxes "may already contain" it. Master does the same.

This branch's own premise is that no award ticket carries zero government charges.
It enforces that at exactly 0 (`seats_client.py:612`) and nowhere else, so one cent
walks past the rule meant to catch the triage's "tax=0 on an LHR departure". It is
the scored-side version of way eleven, with a flipped verdict.

**Location:** `src/seats_client.py:596-620`, `src/optimizer.py:1632-1650`
(`_apd_inclusion_unverified`: the live rule never compares the figure against the duty).

### 4. Medium: `PARSER_VERSION` not bumped, so a replay's answer changes under an unchanged certificate

**Repro:** `test_kf_taxes.py::test_the_parser_version_was_bumped_when_the_parse_changed`,
`::test_a_replay_whose_answer_changed_under_the_parser_says_it_was_reparsed`. By
hand, I built one corpus (B4 = `singapore`, tax 0) and replayed it on both trees:

| | manifest | parser line | headline |
|---|---|---|---|
| master | `mh_4f32323cad04bd70` | `2026-09-09.v5 at capture / 2026-09-09.v5 now` | `0.00% - 5.82% (snapshot)` |
| branch | `mh_4f32323cad04bd70` | `2026-09-09.v5 at capture / 2026-09-09.v5 now` | `0.00% - 1.40% (snapshot)` |

**Expected:** `seats_client.py:59` says "BUMP THIS whenever `parse_pages_detail` or
anything it calls changes what a given page yields". This branch changes what a page
yields: 0 and three sources become UNKNOWN, six sources get names, and qatar/finnair
get an indirect path. So the REPARSED banner should fire.

**Actual:** the banner does not fire, and the same hash and the same parser version
print a different answer. Tsuki's own Mac snapshots, captured under `2026-09-09.v5`
with a qatar row at tax 0, will replay differently and say nothing about it.

**Location:** `src/seats_client.py:66`.

### 5. Medium: a stale pre-APD break-even is printed beside the moved one

**Repro:** `test_kf_taxes.py::test_no_stale_pre_apd_break_even_is_printed`. This is the
Coder's own B4 case (United 30,000, no tax figure).

**Expected:** one break-even, $43.89, which is after APD.

**Actual:** the leg detail also prints
`... the carrier-imposed surcharge is UNKNOWN ... Points beat cash only if the surcharge is below $182.00.`
That figure ignores the $138.11 owed, and it sits a few lines from the $43.89. It
shows in every unknown-tax UK case I ran (MAN, EDI, J/F/W). In the J case the verdict
already says "cash wins whatever it turns out to be" and the same block still says
points win below $182. The warning is written in `evaluate_leg` before `apply_apd`
runs. `_add_apd_to_unscored_floor` rewrites `verdict_reason` only. The Coder's CLI
test checks that `only if the surcharge is below $43.89` is absent, so it cannot
see `$182.00`.

**Location:** `src/optimizer.py:1126-1135`, `src/optimizer.py:1743-1766`.

### 6. Medium: negative taxes are treated as "unconvertible", so APD is left out and USD is said to lack an FX rate

**Repro:** `test_kf_taxes.py::test_negative_taxes_floor_carries_the_owed_uk_apd`,
`::test_negative_usd_taxes_are_not_described_as_a_missing_fx_rate`. B4 is United
30,000 with `YTotalTaxes: -500` in USD.

**Expected:** the plan groups negative with missing, zero and unreported: unknown,
with APD added because there is no usable TotalTaxes. The floor should be at least
$438.11.

**Actual:** `convert_taxes` keeps `source_amount=-5.00`, so `taxes_unconvertible`
is True (`live_trip.py:505`). The unconvertible carve-out at `optimizer.py:1632`
then applies the live rule ("a TotalTaxes figure EXISTS ... it may well contain the
duty") to -5.00 USD. The floor stays at $300.00, APD is "unverified", and the trip
high end is 5.82% instead of 1.40%. The prose adds `Seats.aero reported taxes of
USD -5.00 and no FX rate for that currency is configured` and `USD -5.00 - CANNOT
be converted to USD`. The wording predates this branch. Leaving APD out of the floor
is this branch's carve-out.

**Location:** `src/live_trip.py:505-508`, `src/optimizer.py:1632-1636`,
`src/seats_client.py:~515-525`.

### 7. Medium: search mode reports "no award availability" when Seats.aero returned a Qatar award

**Repro:** `test_kf_indirect.py::test_search_mode_does_not_call_a_qatar_award_no_award_availability`.
Single-route search, where the stub returns a `qatar` row and an `american` row.

**Expected:** a statement that awards came back, and that Qatar is reachable only
indirectly.

**Actual:** `Seats.aero returned no award availability for this route and date range.`
and `No strategies found.` Seats.aero returned two awards. `optimize()` found no
fundable path and returned `[]`, and `run_search` reads `[]` as "nothing was there".
That turns a filter result into a statement about award space. Master does the
same. The branch's promise of "never 'no path' about Qatar" does not reach this
surface.

**Location:** `src/main.py:911-923`, `src/optimizer.py:334-380`.

### 8. Low: a leg whose only unknown is TAXES is reported as an unknown SURCHARGE, and counted twice

**Repro:** `test_kf_taxes.py::test_a_leg_whose_only_unknown_is_TAXES_is_not_reported_as_an_unknown_SURCHARGE`.
B4 is United (program-policy $0 surcharge, known) with no tax figure.

**Actual:** the verdict cell reads `WITHHELD (surch unknown)`. The totals table
counts B4 in both `surcharge is UNKNOWN (NOT $0) | 1` and `TAXES are UNKNOWN | 1 (B4)`.
The headline caveat says `The spread is carrier-imposed surcharges that are not known,
AND award TAXES on B4`, and this run has no unknown surcharge. The footer says
`Legs where a points path exists but its carrier-imposed surcharge is UNKNOWN (NOT $0): B4`.
Every one of these says NOT $0, so no unknown is presented as zero. What they get
wrong is WHICH quantity is unknown, which is what the commit "Every sentence about a
leg with unknown taxes says taxes" set out to fix.

**Location:** `src/formatter.py:309`, `src/formatter.py:1017`, `src/formatter.py:1140`,
`src/main.py:830-836`, `src/optimizer.py:2237` (`legs_surcharge_unknown` is keyed on the verdict string).

### 9. Low: replayed legs show provenance `none` and skip the loud unknown-tax block

**Repro:** `test_kf_taxes.py::test_a_replayed_leg_with_unknown_taxes_prints_the_loud_not_zero_line`.

**Actual:** on `--from-snapshot`, B4's table cell reads `none | unknown`.
`_points_provenance_cell` handles only LIVE and BADGE. `_print_live_scoring_block`
returns early unless the leg is LIVE, so neither `taxes from the API: NONE USABLE
... IT IS NOT $0` nor the floor clause naming the added APD is printed. The numbers
are correct (break-even $43.89 after APD). Master does the same.

**Location:** `src/formatter.py:367-395`, `src/formatter.py:636`.

### 10. Low: the indirect verdict hides an unattributed award at trip level

**Repro:** `test_kf_indirect.py::test_an_unattributed_award_beside_a_qatar_award_is_still_counted_at_trip_level`.
B4 has qatar 33,000, american 30,000, and 25,000 with no program named.

**Actual:** the verdict is `cash (indirect path not scored)` and
`legs_award_unattributed_ids == []`. `elif indirect_candidates` is checked before
`elif unattributed_candidates` (`optimizer.py:1254`), and the counter counts verdicts.
The 25,000-point award could belong to a direct UR partner and is cheaper than the
Qatar one. It appears in the leg warnings and nowhere at trip level (and that
counter is never printed anyway, which is Coder gap 6).

### 11. Low: the developer's key files are read at collection time

**Repro:** `test_kf_harness.py::test_collection_time_code_cannot_see_the_developers_key`.
It is red whenever a repo `.env` or `~/.config/points-optimizer/.env` holds the key
and the shell does not export it. It was red in the sandbox while my canary file
existed, and on the simulated Mac with the key only in files.

**Actual:** `src/config.py:96-100` runs `load_env()` at import, and every test module
imports `src.*` during collection, before any fixture exists. So module-level test
code (a `skipif`, a parametrize list) sees the real key, and `_ENV_INJECTED` holds its
value and path. The `isolated_environment` snapshot is taken after that injection,
so it puts the key back into `os.environ` after every test. Monkeypatch's undo of
`delenv` does the same. No current test reads it at module level, and every test
body and every child is clean (verified). This is a hole in "the suite owns its
environment", not a leak anyone has hit.

### 12. Low: loader leftovers

**Repro:** `test_kf_loader.py` (5 red probes).
- A directory passed as `--trip-fixture` raises an `IsADirectoryError` traceback
  (`trip_loader.py:71`: only `JSONDecodeError` is converted).
- A non-UTF-8 file gives one clean line, but without the filename:
  `Error: 'utf-8' codec can't decode byte 0xff ...`.
- Loadable but bad values traceback at scoring: `"currency": 5` hits
  `AttributeError` at `config.py:467`, and `"amount": 1e400` hits `OverflowError`
  at `config.py:260`.
- Pre-existing: a cash option with `"date": "2027-02-30"` or `"tomorrow"` loads with
  exit 0. `_maybe_date` returns None, and None means "the leg's own date", so a fare
  for some other day is scored as the own-date fare. That is H-5's shape, arriving
  through the loader.

### 13. Low: the README verdict table is stale

`README.md:408-416` "The five PAY CASH sub-states are distinct" does not list
`cash (indirect path not scored)`, and was already missing `cash (award unattributed)`,
`cash (APD unknown)` and the no-live-data verdict. It still defines
`cash (surcharge unknown)` as "because the surcharge is unknown", but that verdict now
also means unknown taxes. No test ties verdicts to the README the way
`test_exit_codes_are_documented.py` ties exit codes.

**Observations, not scored as findings:**
- With `travelers: 2`, APD is added x2 but a live award's per-passenger points are
  counted x1 (floor $576.22 = one seat's 30,000 points + two passengers' APD). This
  predates the branch and applies offline too.
- `test_no_changelog_in_user_output.py` scans only `--offline` output. Every string
  this branch adds is on the live or replay paths. I scanned those outputs by hand
  with the test's own `_scan` and found no leaks. The replay banner does print
  `parser 2026-09-09.v5`, which the regex would flag if replay were scanned.
- The scratch master worktree
  (`/tmp/claude-0/.../scratchpad/master`) has a warm `data/cache/` (4 entries,
  22:08). With it, master's v5-probes read **21/77**, not 19/79. I moved it aside to
  measure and then put it back.

---

## What I could not break

**Unknown taxes on the chosen award.** Missing, 0 from any source, any figure from
qatar/turkish/singapore, unconvertible (MXN), two unknown-tax candidates, an
off-date award promoted against a dated cash option, an off-date award kept as a
finding (renders `+ taxes UNKNOWN`), J/F/W cabins (GBP 244, duty alone settles cash),
MAN/EDI/LGW origins (APD added), an unknown APD band (LHR-MEX; the label names both
the departure tax and the award taxes), and snapshot replay of zero/missing rows
(correct numbers). No POINTS verdict, no single-number margin, and the floor includes
APD every time. The "duty alone flips to certain cash" rule is also sound across
candidates, because APD is owed on every ticket from that airport.

**Offline/badge path.** `--offline` output is byte-identical to master for all five
trip fixtures. No fixture candidate carries `taxes_unknown`.

**Indirect path on the trip path.** Qatar+American+unattributed, Finnair on B1, a
cheapest-but-indirect Qatar beside a scored United, and an off-date Qatar: no sentence
naming Qatar or Finnair says "not a partner", "no points path", "NO UR PATH" or "PAY
CASH (no path)". The leg is never in `legs_no_partner`, and an indirect award is never
scored. The way-ten import guards hold under `-O`: deleting the
`INDIRECT_PATH_UNVERIFIED` row raises, and an unclassified `*_UNKNOWN` code raises.
Exit codes: the new verdict needs no row. It is a verdict, not an exit status, and
the AST-based test is unaffected.

**Harness, simulated Mac.** `git archive` into `.../points-optimizer-v5/`,
`SEATS_AERO_KEY` exported, canary keys in the checkout's `.env` and the real
`~/.config/points-optimizer/.env`, a warm `data/cache/` plus a working-tree corpus
(B4 with qatar at tax 0) written by one stubbed live run, and HTTP(S)_PROXY pointed at
a logging canary proxy.
- Branch full suite **900 passed, 4 skipped**. Under `-O` also 900/4. With the key
  in files only (not exported) also 900/4. Master under the same conditions: **10
  failed, 798 passed, 4 skipped**, the triage's ten, by name.
- Canary proxy hits: **0**. The whole checkout (excluding `__pycache__`) and `/root`
  were byte-identical before and after. No test wrote the cache, the corpus, a trip
  fixture or a key file.
- Children: plain children get the harness `ConnectionError`. `-E` and `-I` skip the
  sitecustomize, but the dead proxy still refuses them (`ProxyError`), and they still
  resolve the harness's `POINTS_OPTIMIZER_*` paths. `-S` cannot import `requests`.
  A second `sitecustomize.py` on `PYTHONPATH` ran, and the guard still won. A key-
  holding live child, run plain or with `-E`, did not add a file to the corpus. No
  test builds a child environment from scratch or clears `os.environ`.
- The environment restore breaks nothing that sets env on purpose: full suite green,
  v5-probes that set `os.environ` manually are unchanged.

**Loader.** 27 malformed variants (bad JSON, empty, list/str/null top level, legs as
dict/str/null, leg as int/null, each required key missing, bad/int/null dates,
string amounts and points, the answer file, a unicode filename): one `Error:` line
naming the file, exit 1, no traceback.

**Scanner.** The root scrub removes only the checkout path, so a leak inside a path
under the root still matches. A "Step 7" leak did not hide behind Rich wrapping at
any of 80 offsets. A symlinked `...-v5` checkout passes. Offline output contains no
repo paths.

**Regressions.**
- Sandbox: 891 passed, 13 skipped, identical under `-O`.
- `v5-probes`: 19 red / 79 green, the same red SET by test id as clean master.
- `adversarial-probes`: 40 / 38, the same red SET by id as master.
- Both probe sets are unchanged under the simulated Mac. Master's v5-probes read
  21/77 there, the branch's 19/79.

## Cleanup

I removed `~/.config/points-optimizer/.env` (my canary). The empty directory existed
before this pass and was left as found. The real repo has no `.env` and no
`data/cache/`, and `git status` shows only this report and the probe directory. I
stopped the canary proxy. The simulated-Mac checkouts live only in the scratchpad.

---

## Re-test: `278332e`

Re-attacked 2026-09-11 against `278332e` ("Tester round: unknown taxes never
ranked, never floor-less, never hidden behind a surcharge label"). Same rules as
before: no `src/` edits, no network, stubbed transport only.

**Result: the 13 findings are fixed on the trip path. Two survive in search mode,
which the fix only partly reached. Eight new problems, all Medium or Low; none
High.** Probes: `known-failures-probes` now **7 red / 91 green** (all 7 red are in
`test_kf_retest.py`; every probe from the first pass is green).

### Regressions

| Run | Result |
|---|---|
| sandbox `pytest -q` | 914 passed, 13 skipped (same under `-O`) |
| simulated Mac (fresh `git archive 278332e` into `points-optimizer-v5`, key exported, canary keys in the checkout's `.env` and the real `~/.config/points-optimizer/.env`, warm cache and corpus, logging canary proxy) | 923 passed, 4 skipped, also under `-O`, also with the key only in files. Canary proxy hits **0**. Checkout and `/root` unchanged before and after |
| same, with a corpus captured by master (parser `2026-09-09.v5`, qatar at tax 0) | 923 / 4. `--from-snapshot` of it prints `REPARSED UNDER A DIFFERENT PARSER VERSION` and B4 goes to `>= $638.11`, PAY CASH |
| `--offline` on all 5 trip fixtures | **byte-identical to master** (both regenerated on the same day; the FX "N days old" line changes daily) |
| `adversarial-probes` | 40 / 38, same red set as master by id |
| `v5-probes` | 20 / 78. The one addition to master's red set is `test_a_live_leg_states_apd_without_adding_it` (below) |

### The v5 probe that went red is by design, not a regression

`test_v5_held_up.py::test_a_live_leg_states_apd_without_adding_it` replays the
v5 probes' default corpus. That corpus reuses the real SFO-MAD Aeroplan row on
every leg, B4 included, so B4 departs LHR carrying CAD 44.60 ($32.36) of taxes.
That figure is below the GBP 102 ($138.11) duty it would have to contain, so under
Finding 3's rule it is now UNKNOWN and the duty is ADDED. What the probe guards
still holds for a figure that could contain the duty: the new
`test_a_realistic_live_tax_figure_still_states_apd_without_adding_it` replays B4
with United's real $224.63 and gets `IT IS NOT ADDED HERE` / `NOBODY HAS CHECKED`.
The v5 probe's fixture is now unrealistic for an LHR departure. Its assertion is
still right. I did not edit it; it is not mine.

### The three probes the coordinator flagged: all three were my errors, now fixed

- `test_gap1_the_high_end_overstates_a_points_win_by_the_owed_apd`: its "scored"
  United award had $50.00 of taxes on an LHR departure, the exact kind of figure
  my own Finding 3 says not to believe. The taxes are now $150 (above the duty),
  and the probe is green: the high end is 7.80% and the label names only award
  taxes, which is correct because nothing else on this run is unknown.
- `test_an_indirect_award_never_enters_the_score_even_when_it_is_cheapest`: it
  asserted verdict-based counting, which contradicted my own Finding 10. It now
  asserts `legs_indirect_path_unverified_ids == ["B4"]`. That row is true, and the
  probe's real point (the award is never scored) is unchanged.
- `test_a_replay_whose_answer_changed_under_the_parser_says_it_was_reparsed`:
  `build_corpus` wrote the current parser version into the manifest rows, so the
  corpus I built was inconsistent. With both the rows and `_meta` at
  `2026-09-09.v5` (a real old corpus), REPARSED prints. The inconsistency is its
  own small problem: R-7 below.

### Status of the original findings

| # | Status on 278332e |
|---|---|
| 1 search `$0.00` / ranked on it | **Partly fixed.** Sort, cell and summary are fixed. The per-program dedup still ranks on the $0: **R-1** |
| 2 gap 1 | Fixed: floor carries APD, `TAXES_UNKNOWN` widens the range, label and warning say TAXES |
| 3 below-APD figure believed | Fixed on the trip path (per passenger, per cabin, all UK airports, promoted off-date awards, replays). **Not applied in search mode: R-2.** Not applied to off-date findings: **R-6** |
| 4 parser version | Fixed (`2026-09-10.taxes-trust`; REPARSED fires on an old corpus) |
| 5 stale break-even | Fixed (warnings rewritten too) |
| 6 negative taxes | Fixed (unusable, APD added, no FX sentence) |
| 7 search "no award availability" | Fixed. New wording has **R-3** and **R-4** |
| 8 taxes reported as surcharge | Fixed: `WITHHELD (taxes unknown)`, no double count, caveat names only what is present |
| 9 replay provenance / loud line | Fixed: `snapshot` cell, `replayed award:` block |
| 10 unattributed hidden by indirect | Fixed (counted by reason code, row printed) |
| 11 collection-time key | Fixed for `tests/` and `adversarial-probes`. **Not for `v5-probes`: R-5** |
| 12 loader | Fixed (all 32 loader probes green) |
| 13 README verdicts | Fixed, and guarded by `test_verdicts_are_documented.py` |

### New findings (severity-ranked)

**R-1. Medium: search dedup still ranks on a $0 unknown, drops a known-cash award, then misreports it.**
Repro: `test_kf_retest.py::test_R1_search_dedup_does_not_drop_a_known_cash_award_for_an_unknown_one`.
SFO-MAD search returning United 50,000 + $56.00 and United 52,000 with no tax
figure. `optimize()` keeps one strategy per program/date/cabin, the one with the
lower `total_value`, and the unknown one still counts as $0 there ($520 < $556).
The known award disappears. The summary then says `no strategy on this search has
a known cash cost; ranked by points alone`, which is false: one had a known cost
and needed 2,000 fewer points. Location: `src/optimizer.py:375`
(`if key not in seen or strategy.total_value < seen[key].total_value`). The fixed
sort sits below it.

**R-2. Medium: search mode does not apply the below-duty rule.**
Repro: `::test_R2_search_mode_applies_the_below_uk_duty_rule`. `--origin LHR
--destination SFO` with United 35,000 + $5.00: ranked #1 as a known `$5.00 /
$355.00`, and `Top strategy cash cost: $5.00`, while GBP 102 is owed on the
ticket. `taxes_below_owed_uk_duty` lives in `live_trip.award_to_candidate` (the
trip path only). `optimize()` reads `award.cash_component_known` directly.
Location: `src/optimizer.py:~350-356`, `src/live_trip.py:491`.

**R-3. Low: "a finding about your transfer partners" when the cause is the balance.**
Repro: `::test_R3_...`. United (a UR partner) 50,000 with `UR=1000`: the header
says `NONE of them can be funded ... That is a finding about your transfer
partners, NOT about award space`. The per-award line correctly says `no fundable
transfer path`, but the header makes a partnership claim out of a balance
shortfall, which is the M-5 shape. Location: `src/main.py:~943-946`.

**R-4. Low: a run that made one API call says "no API call made".**
Repro: `::test_R4_...`. To list the unfundable awards, `run_search` calls
`seats_client.search()` a second time. The in-process cache answers it (still one
HTTP call, verified), but the call resets `last_pagination_note`, so the coverage
line becomes `served from the IN-PROCESS cache; no API call made`. Location:
`src/main.py:~933`, `src/seats_client.py:1406-1460`.

**R-5. Low: fix 11 does not cover `v5-probes`.**
Repro: `::test_R5_...` (a static import-order check). On the simulated Mac (a
checkout `.env` with a key, not exported) I collected a throwaway module in each
suite: `config._ENV_INJECTED` was `{'SEATS_AERO_KEY': ('repo .env', <checkout>/.env)}`
under `docs/test-reports/v5-probes`, and `{}` under `tests/` and
`adversarial-probes`. The cause: `docs/test-reports/v5-probes/conftest.py`
imports `src.response_cache` / `src.seats_client` before it imports
`tests.conftest`, so `load_env()` runs on the real files first. Per-test isolation
still holds (the suite's red set is stable under the Mac conditions).

**R-6. Low: an off-date finding prints a below-duty figure as a clean price.**
Repro: `::test_R6_...`. In one response for B4, the promoted Jan-26 award with
$5.00 is (correctly) UNKNOWN, while the Jan-25 advisory finding renders
`United MileagePlus Y 30,000 + USD 5.00`. `live_trip._finding` reads the parser's
view, not the below-duty rule. It is not scored.

**R-7. Low (pre-existing): a manifest row and its snapshot can disagree on the parser version, and nothing notices.**
Repro: `::test_R7_...`. The banner reads the version from the manifest row, and
the per-leg replay clause reads it from the snapshot's `_meta`. A row claiming the
current parser over a snapshot whose `_meta` says `2026-09-09.v5` replays with no
REPARSED banner, while the same output says `parsed at capture by 2026-09-09.v5`.
`snapshot_replay.verify` does not compare the two.

**R-8. Low: the new session HOME leaks one temp directory per pytest process.**
`tests/conftest.py` makes `mkdtemp(prefix="points-optimizer-test-home-")` at
import and never removes it. After this pass there were 31 in `/tmp` (all empty;
explore scripts that import the harness make them too). On a Mac they build up
in `$TMPDIR`. I removed them.

**Observations (not scored):**
- Two rejected alternatives on one leg: a scored United $350, a KrisFlyer 10,000
  with tax 0, and a United 12,000 with MXN taxes. The floor candidate is chosen
  before APD is added, so the leg floor is $238.11 while the MXN alternative's own
  warning says `cost floor is $120.00`. The real bound is still right (any ticket
  from LHR costs at least points + APD, so $120 + $138.11 > $238.11), but the prose
  contradicts itself.
- LHR-MEX (APD band UNKNOWN): the below-duty rule is off (`charge.is_known` is
  False), so $5.00 is believed as the whole tax figure. The leg is withheld anyway
  (`cash (APD unknown)`) and the label says "the departure tax is $0". Both
  possible bands' reduced rates are at least GBP 102, so the rule could still fire.
- Replay output still labels candidates `LIVE ...` in warnings
  (`UNVERIFIED: LIVE Qatar Privilege Club LHR-SFO ...`) on a `snapshot` leg. This
  predates the branch.
- The loader now upper-cases `currency`. That is harmless: no committed fixture
  uses lower case, and the offline output is byte-identical.

### What the fixes did not break

Each of these has a probe:
- **Below-duty rule, per passenger:** on a two-traveller leg, $150/pax is believed
  and $100/pax is unknown, with APD x2 added.
- **J cabin:** $224.63 is unknown against the standard rate (GBP 244, $330.38) and
  the duty is in the floor. $400 is believed.
- **GBP boundary:** GBP 102.00 exactly is believed; 101.99 is not.
- **Other UK airports:** MAN, EDI and LGW all fire.
- **Off-date and replays:** a promoted off-date award fires against its own date's
  $470 fare, and replays apply the rule.
- **Search ordering, mixed known and unknown:** United $56.00 ranks #1, and the
  unknowns show `UNKNOWN` / `>= $...`, never `$0.00`.
- **Search with no fundable award:** it lists the Qatar indirect path, with one
  HTTP call.
- **Harness:** the conftest-import HOME change broke no test here or on the
  simulated Mac, and `test_harness_isolation` now checks against the real HOME
  recorded before the move.

### Cleanup (re-test)

I removed my canary `~/.config/points-optimizer/.env` again (the empty directory
is as found) and stopped the canary proxy. I removed the 124 `/tmp/tmp*` cache
directories my scripts left and all `points-optimizer-test-home-*` directories.
The real repo has no `.env` or `data/cache/`. The simulated-Mac checkouts are in
the scratchpad only.

---

## Re-test 2: `e64c32a` + `fcb70f8`

Adversarial pass on the two untested commits: `e64c32a` (the fixes for my re-test
findings) and `fcb70f8` (the Manager's Must-fix 1 and Should-fix 1, 2, 3, 4, 5, 7,
and part of 8). Same rules: no `src/` edits, no network.

**Result: nothing Critical or High. Three Medium, five Low.** The multi-traveller
guard does what Must-fix 1 asked on the live, offline and replay paths, but it has
a side door (R2-1). On a couple's trip it also turns "not priced" into a quoted
live 0.00% (R2-5). Every re-test finding from `278332e` (R-1 to R-8) is fixed.
Probes: `known-failures-probes` now **8 red / 112 green** (all 8 red are in
`test_kf_retest2.py`).

### Regressions

| Run | Result |
|---|---|
| sandbox `pytest -q` | **925 passed, 13 skipped**, same under `-O` |
| simulated Mac (`git archive fcb70f8` into `points-optimizer-v5`, master-captured corpus, warm cache, key exported / in `.env` / in `~/.config`, canary proxy) | **934 passed, 4 skipped**, same under `-O` and with the key only in files. Canary hits 0. Checkout unchanged |
| `v5-probes` | **19 / 79, the same red set as master by id**: the corpus repair (Should-fix 5) restored it |
| `adversarial-probes` | 40 / 38, the same red set as master |
| `--offline` on all 5 trip fixtures | **byte-identical to master**. No committed fixture has a multi-traveller flight, and the Trip B hotels for 2 are untouched |
| session HOME | removed at exit; **0** `points-optimizer-test-home-*` left after any run (R-8 fixed) |

### Probe changes

`test_kf_retest.py::test_the_below_duty_rule_is_per_passenger_on_a_two_traveller_leg`
now errors, because a 2-traveller leg has no `best_points`. What it tests (the
below-duty check compares per passenger) is a candidate-level rule, so I rewrote
it to read the leg's live candidate. It now also asserts that the leg is the new
verdict with no floor. Green.

### Findings (severity-ranked)

**R2-1. Medium: the party guard has a side door. A reachable unpriced partner still gets a one-seat break-even.**
Repro: `test_kf_retest2.py::test_R2_1_...`. On B1 with `travelers: 2` and
`unpriced_partner_programs: ["United MileagePlus"]` (Trip A's fixture has this
shape), the verdict is `cash (points unpriced)`: *"Points would win below 39,500
points"*. The leg carries no `PARTY_PRICING_UNVERIFIED` reason and is not counted.
That break-even compares ONE seat's award price against the party's cash. With
the couple's $790 entered, it reads "below 79,000", so a 50,000-per-seat award
(100,000 for two) looks like a win: the Must-fix-1 false win, one branch over.
Cause: `elif result.break_even_programs:` (`optimizer.py:1358`) comes before
`elif party_candidates:` (`:1377`), and the unpriced-partner block does not look
at `travelers`. Reachable only through a hand-written fixture field.

**R2-5. Medium: a couple's trip quotes "0.00% (live)" when no flight was priced.**
Repro: `::test_R2_5_...`. Build `--new-trip` with 2 travellers and 2 flights, and
run it live with United awards on both legs. Both legs are (correctly) not scored,
but the headline reads **"Optimizer beats paying cash by 0.00% (live)"**, margin
provenance `live`, exit **0**. That is one quotable live number saying points save
nothing, on a trip where points were never evaluated: "could not price" becomes a
finding of zero. The per-leg verdicts do say "Pay cash BY DEFAULT, not by
finding", and a row counts the legs. But `PARTY_PRICING_UNVERIFIED` is classified
COUNTED, not WIDENS, and nothing touches `margin_withheld`, so the headline carries
no qualifier. For comparison, an API failure withholds the margin (exit 3), and an
unknown surcharge widens it to a range. This is Tsuki's main case (flying as a
couple). Location: `models.py` `TRIP_LEVEL_ANSWERS["PARTY_PRICING_UNVERIFIED"]`,
`main.py:811` (`withheld` looks only at provenance).

**R2-3. Medium: search dedup now hides the cheaper award (e64c32a overshot the R-1 fix).**
Repro: `::test_R2_3_...`. SFO-MAD returns United 50,000 + $56 and United 30,000
with no tax figure. Dedup (`optimizer.py:411`) now uses the ranking rule, so the
known award always beats the unknown one for the same program, date and cabin.
The unknown one is **deleted**, not ranked after. A 30,000-point award, 20,000
fewer points than the one shown, never appears. The ranking already sorts unknowns
after knowns, so deduping across known and unknown only removes real availability.

**R2-2. Low: a flight leg for 0 or -1 travellers is scored as one seat.**
`::test_R2_2_...[0|-1]`. The guard reads `int(travelers or 1) > 1`, and the loader
accepts any int (`trip_loader.py`: `int(raw.get("travelers", 1))`). So `0` and `-1`
give a POINTS verdict ($256 vs $395). `--new-trip` refuses 0; a hand-written
fixture is not refused.

**R2-4. Low: the party guard erases the other classifications.**
`::test_R2_4_...`. The guard `continue`s before the attribution, partner and
indirect checks. On a 2-traveller leg, a Qatar award and an unnamed-program award
reach the trip block only as "party not priced", and `legs_indirect_path_unverified`
and `legs_award_unattributed` both come back empty. A leg whose only award is
American (no UR path at all) is told to "Price it by hand: 2 x the points, 2 x the
taxes". The wording is conservative, but it undoes Finding 10 on party legs.

**R2-6. Low: the relocation banner is missing from search mode (Should-fix 4 is only partly done).**
`::test_R2_6_...`. `print_relocation_banner` is called only from `build_live`
(trip runs). A single-route search with `POINTS_OPTIMIZER_ENV_FILE` set resolves
its key through it and says nothing about it. The key-not-found error on a trip run
also skips the banner: it lists `3. repo .env (/tmp/x.env)`, which shows the path
but not that a variable moved it. The README says "live runs print it in the
banner", and search is a live run.

**R2-7. Low: `--passengers 2 --html` silently drops the export.**
`::test_R2_7_...`. The PRICED-FOR-ONE-SEAT branch returns before `export_html` and
prints nothing about the request it ignored.

**R2-8. Low (docs): the README overstates when the duty is added.**
The new section says "When a UK departure's taxes are unknown, the duty is added
to the floor". The code does not do that for a figure in a currency with no FX rate,
a case the same section lists as unknown: the figure may contain the duty, so the
live rule holds (the Coder's own test `test_an_unconvertible_figure_still_does_not_add_apd`).

**Observation.** If a client object refuses attributes, `optimize()` swallows the
failed `last_search_awards` assignment, and `run_search` falls back to *"Seats.aero
returned no award availability"*, the claim R-7 removed. The CLI always builds a
`SeatsClient`, which accepts the attribute, so no user can reach this. Manager
Should-fix 8 (return the awards instead) would close it.

### What held up

- **e64c32a**, all green:
  - R-1: dedup keeps the known award.
  - R-2: search applies the below-duty rule.
  - R-3: the wording now names "THIS WALLET - its transfer partners and its
    balances", with a per-award reason for partners that can't be funded.
  - R-4: `last_search_awards` means one HTTP call and a truthful coverage line.
  - R-5: v5 conftest import order.
  - R-6: off-date findings say `+ taxes UNKNOWN`.
  - R-7: `PARSER VERSION DISAGREEMENT` prints.
  - R-8: atexit removes the session HOME.
- **Party guard:**
  - Live, `--offline` badge (a 3-traveller B2) and `--from-snapshot` (B3): the leg
    is not scored, has no floor and no alternatives, makes no balance-ceiling
    demand, spends 0 points, and is counted in the footer row.
  - `--new-trip --travelers 2`, end to end: both flights get the new verdict.
  - Hotel legs for 2: unaffected.
  - Search `--passengers 2`: prints the one-seat banner, names no "Top strategy",
    and writes no `results.html`.
  - Exit code: 0 (see R2-5).
- **Should-fix 1:** replaying a master-style corpus now prints "at least **$638.11**
  ... the award's other taxes are UNKNOWN". "$500.00" and "carrier-imposed
  surcharge" are gone from B4.
- **Should-fix 2:** search floors carry the owed APD: J `>= $930.38`, F
  `>= $1,230.38`, MAN Y `>= $438.11`. The non-UK SFO-MAD floor stays `>= $300.00`.
- **Relocation banner:** printed on live trip runs, never on `--offline`, so the
  no-changelog scanner never sees it.
- **`PYTHONUSERBASE`:** a preset value is kept and inherited by children (checked
  with a nested pytest).

### Cleanup and a disclosure

During this pass, one of my scratch scripts (`couple.py`, not a probe) ran the CLI
in-process after importing the probe helpers. That import loads `src.config`
before my script set `POINTS_OPTIMIZER_CACHE_DIR` / `_SNAPSHOT_DIR`, so the run
used the defaults. It wrote `data/cache/seats_aero/` (2 files) and two snapshots
plus a `MANIFEST.md` into the **committed corpus directory**
`tests/fixtures/seats_aero/live_trip_b/` of the real repo. All five files were
created at 05:09:22 by that run; I deleted them and re-ran the suite (925 / 13,
unchanged). Nothing was committed. This is my error, not a product defect: config
reads those variables at import, as documented. It does show that anything
importing `src` outside pytest archives into the corpus by default.

I also removed my canary `~/.config/points-optimizer/.env` (the empty directory is
as found), stopped the canary proxy, and deleted 55 `/tmp/tmp*` directories my
scripts left. The real repo has no `.env`, no `data/cache/` and no
`results.html`, and `git status` shows only the probe and report changes.
