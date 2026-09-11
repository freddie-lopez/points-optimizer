# Coder report: the ten Mac failures, and the tax figure nobody should trust

Plan: `docs/plans/known-failures.md`. Branch `fix/known-failures` (7 commits on
`ad83802`). Not pushed.

## Result

| | before (master) | after |
|---|---|---|
| sandbox, `pytest -q` | 799 passed, 13 skipped | **925 passed, 13 skipped** (final) |
| sandbox, `python -O -m pytest -q` | same | **925 passed, 13 skipped** (final) |
| **simulated Mac** (below) | **10 failed, 798 passed, 4 skipped** | see "Final state" below |
| v5-probes red / green | 19 / 79 (sandbox), **21 / 77 under Mac conditions** | 19 / 79 both (final) |
| adversarial-probes red / green | 40 / 38 | 40 / 38 (same red SET, diffed by id) |

**The simulated Mac reproduces the triage's numbers exactly** - 10 failed, 798
passed, 4 skipped - on master, without any network: checkout in a folder named
`points-optimizer-v5`, `SEATS_AERO_KEY` exported, a warm `data/cache/` and a
committed B4 snapshot with a `qatar` row at tax 0, both produced by one stubbed
live run (`simulate_mac_full.py`, scratch). The same ten tests, by name.

## What was actually wrong (differs from the triage)

- **Group A was not "no network".** Six subprocess tests inherited the
  developer's real key; one in-process test got a cache hit from the real
  `data/cache/` (Trip B's own B1). In the sandbox they passed only because
  `test_config_key_resolution` leaked `SEATS_AERO_KEY=from_repo` into
  `os.environ` via `config.load_env()`. Run alone here they failed (exit 1).
  Live CLI runs from tests also archive into the COMMITTED corpus.
- **Group B's traceback happens on every machine.** The two answer-file
  parametrisations had been scanning a crash in the sandbox and passing.
- **Group C: the test was wrong, as the triage said.** Fixed as it proposed.
- **tax=0 is a documented source behaviour, and missing taxes already won.**
  A United award with no tax figure on B4 scored POINTS, 5.82%.

## Steps

1. **Unknown taxes are never scored** (`seats_client.untrusted_tax_reason`,
   `TAXES_UNREPORTED_SOURCES`; `PointsCandidate.taxes_unknown`;
   `optimizer._surcharge_for`, verdict reason, formatter). When a live UK
   departure's taxes are unusable, APD is added to the floor/break-even
   (`_apd_inclusion_unverified`, `_add_apd_to_unscored_floor`), and the leg
   can become a certain cash verdict if the duty alone loses.
2. **Source map**: all 26 published sources named. `qatar`, `finnair` carry
   `indirect_ur_path` (UR -> BA Avios -> combine, with conditions) and get
   verdict `cash (indirect path not scored)`, reason `INDIRECT_PATH_UNVERIFIED`
   (way-ten: counted, `legs_indirect_path_unverified`). Never "no path".
3. **Group C**: `assert_award_is_honest` - attributed or explicitly
   unattributed; runs over a synthetic envelope on every machine.
4. **Group A**: `tests/conftest.py::isolated_environment` (restore os.environ,
   strip key, tmp HOME / key file / cache / snapshots) + `tests/_child_guard/
   sitecustomize.py` (children's `requests` raise ConnectionError) + dead proxy
   env. New env vars read by `config`: `POINTS_OPTIMIZER_ENV_FILE`,
   `POINTS_OPTIMIZER_CACHE_DIR`, `POINTS_OPTIMIZER_SNAPSHOT_DIR`. Failure-mode
   matrix (8 modes) and the live success path at the CLI. Probe suites get the
   same isolation.
5. **Group B**: `TripFixtureError` (one line, exit 1, names file and field);
   scanner discovers only loadable trips, requires exit 0 and no traceback,
   scrubs exactly the repo root.

## Tests changed (not added) and why

- `test_live_first_defaults.py::run` passes an explicit fake key to the child.
  No assertion changed. The premise ("has a key, no network") is now stated.
- `test_response_cache.py::test_every_committed_snapshot_parses_into_valid_awards`
  asserts attributed-or-explicitly-unattributed instead of `award.program`.
- `test_no_changelog_in_user_output.py`: discovery, precondition, root scrub.
- No numeric assertion anywhere was edited.

## Known gaps (for the Tester to size)

1. **APD on a REJECTED alternative's floor.** APD is added to the leg floor when
   the CHOSEN award's taxes are unknown. If a scored award wins and a rejected
   unknown-tax alternative sets a lower floor, that floor still lacks APD.
2. **Way eleven in general** is not done: the high end still assumes unknown
   taxes beyond APD are $0 (labelled so).
3. **Zero-is-unknown for every source** is a judgement: any real zero-tax award
   would now be withheld rather than scored. None is known to exist.
4. `sitecustomize` shadowing: chained to any other sitecustomize on sys.path;
   not tested on macOS Python builds.
5. The Mac's real snapshots are not in the repo (the GitHub corpus is empty):
   the Group C fix is proven on a synthetic envelope and the simulated Mac only.
6. `legs_award_unattributed` exists in the totals but is not printed at trip
   level (pre-existing; the new indirect counter is printed).

---

## Fix rounds after the Tester (commits 278332e, e64c32a)

Tester pass 1 (`524e459`): 3 High, 4 Medium, 6 Low. All 13 fixed in `278332e`;
Tester re-test (`0cf558e`) confirmed them and found 2 Medium + 6 Low new, all in
single-route search or harness edges; fixed in `e64c32a`. Every finding has a
regression test in `tests/test_known_failures_tester_round.py` that fails on the
commit before its fix (verified by running the file against that commit).

Behaviour changes a reader will notice:
- A UK-departure tax figure BELOW the per-passenger APD for the award's cabin is
  treated as incomplete (unknown), on the trip path, in search, and in off-date
  findings. Real LHR rows ($224.63 etc.) are unaffected.
- New reason code `TAXES_UNKNOWN` (way ten: widens the range, `legs_taxes_unknown`);
  table cell `WITHHELD (taxes unknown)`; a rejected unknown-tax alternative's floor
  carries owed APD.
- `PARSER_VERSION` is now `2026-09-10.taxes-trust`: replays of the Mac's corpus
  will print REPARSED, by design.
- Search: unknown-cash strategies rank after known ones and print UNKNOWN.

Probe suites: adversarial-probes 40/38, identical red set to master. v5-probes
20/78: `test_a_live_leg_states_apd_without_adding_it` flipped BY DESIGN (its
corpus puts $32.36 of taxes on LHR, below the GBP 102 duty; the Tester agrees and
added a $224.63 variant that still passes). Tester probes: 98/98 green.

Final state: sandbox 919 passed / 13 skipped (same under `-O`); simulated Mac (v5 folder name, exported key, warm cache, qatar tax-0 corpus) 928 passed / 4 skipped.

## Manager review round (manager-review-known-failures.md: Ship with fixes)

- **Must-fix 1 (Critical, pre-existing):** a flight leg for 2+ travellers is no
  longer scored - verdict `cash (multi-traveller points not priced)`, reason
  `PARTY_PRICING_UNVERIFIED` (way ten: counted, `legs_party_pricing_unverified`,
  printed). Search with `--passengers > 1` prints the per-seat list under a
  "PRICED FOR ONE SEAT" banner and names no top strategy (and skips `--html`).
  Real party pricing waits on decision D1.
- Should-fix done: the certain-cash sentence quotes the post-APD floor and says
  TAXES when taxes are the unknown; search floors include owed UK APD; README
  documents the tax rules, the party rule, REPARSED and the env vars; live runs
  name any `POINTS_OPTIMIZER_*` relocation in effect; the below-duty note states
  its adult/non-connecting assumption; `PYTHONUSERBASE` is pinned to the real
  user base before HOME moves; the "observed in Trip B" notes say where that
  claim comes from; the v5 probe corpus was repaired (v5-probes back to master's
  19-red set).
- Not done here: committing Tsuki's real corpus (must happen on his Mac); the
  `last_search_awards` side channel stays (documented).
- Two Tester probes (`test_kf_retest.py::test_the_below_duty_rule_is_per_passenger_on_a_two_traveller_leg`)
  now error: they score a 2-traveller leg, which Must-fix 1 forbids.

## Re-test 2 round

All 8 fixed (R2-1..R2-8), each with a regression test that fails on `fcb70f8`:
the party guard now also suppresses a per-seat break-even for an unpriced
partner and runs AFTER the party-independent facts (unattributed, indirect, not
a partner); a trip with an unpriced party leg withholds its headline (exit 3,
README exit table updated); search dedups known and unknown cash separately;
`travelers` must be a whole number >= 1; search and the key-not-found error name
relocations; `--passengers N --html` says the export was not written; README
states the unconvertible-currency APD exception.

Probe conflicts, for the Tester to judge: `test_kf_retest2.py` probes that
assert exit 0 on party-leg runs now see exit 3 (R2-5); the R2-4 probe expects
verdict PARTY for a leg whose only awards are Qatar (indirect) and unattributed
- the branch gives the indirect verdict, because nothing on that leg was
fundable regardless of party size; both counters are populated.

Final state: sandbox 935 passed / 13 skipped (same under -O); v5-probes and
adversarial-probes red sets identical to master.

## Re-test 3 round

R3-1 a withheld-for-unpriced trip prints WITHHELD for "Optimizer's
recommendation" and "Saving" too; R3-2 a trip withheld for two reasons names
both; R3-3 a party leg with no REACHABLE unpriced partner is not treated as a
party leg. Regression tests fail on `a0b30b3`. The Tester's three R3 probes
(red on `a0b30b3`) are green: Tester probes 140/140.

Final state: sandbox 937 passed / 13 skipped (same under -O); v5-probes and
adversarial-probes red sets identical to master.
