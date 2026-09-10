# Plan: the ten Mac failures, and the tax figure nobody should trust

Branch `fix/known-failures`, off `master` at `ad83802`. Inputs:
`plans/points-optimizer-known-failures.md` (the triage) and
`plans/points-optimizer-known-failures-review.md` (its review) in the Project.

Baseline on this branch before any change: **799 passed, 13 skipped**, identical
under `python -O`; probe suites **19 red / 79 green** and **40 red / 38 green**.

## What reading the code changed about the triage

The triage was written without the code open. Four of its explanations are
wrong or incomplete, and the fixes follow the code, not the triage:

1. **Group A is not "the tests assume no network".** Six of the seven are
   `subprocess` runs of the CLI, which the conftest's `requests` guard cannot
   reach. They passed in the sandbox for two reasons, both accidental:
   - `test_config_key_resolution.py::test_a_key_injected_from_the_repo_file_...`
     calls `config.load_env()`, which writes `os.environ["SEATS_AERO_KEY"] =
     "from_repo"` directly. `monkeypatch.delenv(..., raising=False)` on an
     absent variable records nothing, so the fake key LEAKS into every later
     test. Run the seven alone in the sandbox and they fail with exit 1 ("No
     Seats.aero API key found"), not exit 3.
   - the leaked fake key then hit the sandbox's egress block, which the tool
     reports as an API failure (exit 3).
   On Tsuki's Mac the variable was already set from `~/.zshrc`, so the leak was
   a no-op and the children inherited **his real key**.
2. **The seventh (`test_trip_builder`) never touches the network at all.** It
   runs in-process, where `requests.get` is blocked. It asks for SFO-MAD on
   2027-01-15 - Trip B's own B1 - and `main` builds its `ResponseCache` on
   `config.CACHE_DIR`, the repo's real `data/cache/`. Tsuki's live run had
   cached that exact request under the 6-hour TTL, so the test got a cache
   hit. Every CLI test that goes live reads and writes the developer's real
   cache and **archives into the committed snapshot corpus**
   (`config.SNAPSHOT_DIR`). A pytest run on the Mac can write fixtures.
3. **Group B's traceback is present in the sandbox too.** `trip_001_answer.json`
   and `trip_002_answer.json` crash the loader with a raw `KeyError: 'id'` on
   every machine. The scanner test passed here only because the sandbox path
   has no `v5` in it: two parametrisations have been scanning a crash and
   calling it clean.
4. **`tax=0` is the documented behaviour of three sources, and missing taxes
   already produce a false WIN.** Seats.aero's docs mark `qatar`, `turkish` and
   `singapore` "Taxes and surcharges are not available for this mileage
   program". Reproduced on this branch with a stubbed Trip B:
   - a `singapore` row on B4 with `YTotalTaxes: 0` parses as KNOWN $0.00 and
     the trip headline prints **"beats paying cash by 0.00% - 5.82%"** - the
     5.82% is built on that $0;
   - a `united` row on B4 with **no tax figure at all** scores
     **POINTS, 5.82%** as a single number. `models.PointsCandidate` states the
     rule that allows it: *"only [a tax figure we cannot price] makes a leg
     unscoreable"*. A missing tax figure is scored as $0 by design. That is this
     project's signature failure written down as a rule.

## Steps

### Step 1 - Taxes the tool may not believe are UNKNOWN, and UNKNOWN taxes are never scored
- `seats_client.TAXES_UNREPORTED_SOURCES = {"qatar", "turkish", "singapore"}`,
  cited to the Seats.aero footnote. For these, any tax value is not usable.
- For every source, a tax figure of exactly 0 on an AVAILABLE cabin is
  "not reported", not $0. Nobody flies a commercial award ticket with zero
  government charges.
- Both land as `cash_component_known=False` with `cash_component_source_amount=None`
  (nothing usable was reported) and a note saying which rule fired. The figure
  the API sent stays in `raw_diagnostics`.
- `award_to_candidate`: **any** live award whose taxes are not known is
  unscoreable - missing, unreported, zero, negative, or unconvertible. A new
  `PointsCandidate.taxes_unknown` flag carries this; `taxes_unconvertible` keeps
  its narrower meaning for the message. `optimizer._surcharge_for`, the verdict
  reason and the formatter key on `taxes_unknown` and say WHICH rule fired.
- The model comment that licensed "missing = scoreable" is rewritten.
- **UK departures.** When a live leg's chosen award has UNKNOWN taxes, nothing
  counted on the points side can contain APD, so the rule "do not add APD, it
  may already be in TotalTaxes" has no TotalTaxes to point at. APD is then ADDED
  to the points side (floor included). That is the part of the unknown the data
  does support - the £102 of the triage - and it moves the optimistic end off
  $0 exactly where the triage said it must.

Acceptance: stubbed Trip B with (a) `singapore` taxes 0, (b) `united` taxes
absent, (c) `qatar` taxes nonzero - none produce a points verdict or a single-
number margin; (b) no longer prints a win; B4's floor includes APD in all three.

### Step 2 - Source map from the published table; indirect Avios paths are named, never "no path"
- Add every source in Seats.aero's published table that is missing
  (`qatar`, `finnair`, `lufthansa`, `ethiopian`, `frontier`, `spirit`), with the
  program names the table gives. `_DOCS` notes now cite the table and date.
- `qatar` and `finnair` get `indirect_ur_path`: Chase UR -> British Airways Club
  Avios -> combine into Qatar Privilege Club / Finnair Plus, with the
  conditions (30-day accounts, 2FA, matching names; Qatar ID upload; Qatar
  companion rule). Sources: BA's and Finnair's own pages, Thrifty Traveler.
- An award with an indirect path is **not scored** (two-hop transfers are not
  modelled, and whether Tsuki's accounts meet the conditions is not known) and
  is **never** reported as "not a partner / no points path". New reason code
  `INDIRECT_PATH_UNVERIFIED`, counted at trip level (`legs_indirect_path_unverified`),
  new verdict `cash (indirect path not scored)`.
- **Decision left to Tsuki** (asked; unanswered): whether to SCORE indirect
  paths. This step is the common prefix of both answers - it stops the false
  "no path" claim and scores nothing.

### Step 3 - Group C test
`test_every_committed_snapshot_parses_into_valid_awards` asserts each award is
attributed OR explicitly unattributed (empty program => the "NOT in the
source->program map" note and `ur_transferable is None`). The check becomes a
helper, and a synthetic envelope runs through it on every machine so the test is
not vacuous where the corpus is empty (it is empty on GitHub: the Mac's
snapshots were never committed).

### Step 4 - Group A: the suite owns its environment
`tests/conftest.py`, autouse, per test:
- snapshot `os.environ` and restore it afterwards (kills the `load_env` leak and
  any like it);
- remove `SEATS_AERO_KEY`; point `HOME` at a tmp dir; point `config._ENV_PATH`,
  `config.USER_CONFIG_ENV_PATH` at absent tmp files and clear `_ENV_INJECTED`;
- point `config.CACHE_DIR` / `config.SNAPSHOT_DIR` at tmp dirs in-process, and
  export `POINTS_OPTIMIZER_CACHE_DIR` / `POINTS_OPTIMIZER_SNAPSHOT_DIR` (new,
  read by `config`) so child processes use them too;
- put `tests/_child_guard/` on `PYTHONPATH`; its `sitecustomize.py` makes
  `requests` raise `ConnectionError("network disabled by the test harness")` in
  every child. "No network" becomes a stated property of the harness.
The seven tests pass an explicit fake key to their children. New tests: the
leak itself; a failure-mode matrix at the CLI (connection error, timeout, 401,
403, 429, 500, malformed JSON -> exit 3, API FAILURE); the live success path at
the CLI (stubbed payload -> exit 0, live provenance); a child process cannot
reach the network and cannot see the developer's key.

### Step 5 - Group B
- `trip_loader`: a file with no `id`/`legs`, or a leg missing a required key,
  raises `TripFixtureError(ValueError)` naming the file and key. `main` already
  turns `ValueError` into one red line and exit 1. No traceback for bad input.
- Scanner: discovers only loadable trip fixtures (not `*_answer.json`), asserts
  exit 0 and no `Traceback` before scanning, and scrubs exactly the repo root
  path (not every path) so a checkout folder named `...-v5` cannot trip it.

## Out of scope (queued, unchanged)
Way eleven (MR6-1) in general, the trips endpoint, UI, Trip B fixture repair,
`--require-all-live` max-age, Hilton points pricing.

## Order and gates
Steps 1 -> 2 (Qatar's taxes are unreported, so Step 1 must be in first), 3, 4, 5.
Full suite and `-O` after each. Then an independent Tester pass, then an
independent Manager review. Nothing is pushed or delivered before sign-off.
