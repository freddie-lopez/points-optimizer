# Coder report: the ten Mac failures, and the tax figure nobody should trust

Plan: `docs/plans/known-failures.md`. Branch `fix/known-failures` (7 commits on
`ad83802`). Not pushed.

## Result

| | before (master) | after |
|---|---|---|
| sandbox, `pytest -q` | 799 passed, 13 skipped | **891 passed, 13 skipped** |
| sandbox, `python -O -m pytest -q` | same | **891 passed, 13 skipped** |
| **simulated Mac** (below) | **10 failed, 798 passed, 4 skipped** | **899 passed, 4 skipped** |
| v5-probes red / green | 19 / 79 (sandbox), **21 / 77 under Mac conditions** | 19 / 79 both |
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
