# Manager review: `feature/operating-airline` (head e5e0616, base master a17497d)

**Verdict**: Ship with fixes. I checked the "changes no number" claim myself and it holds. But the next step the feature exists for, flipping the UNVERIFIED label once Tsuki has captured a real response, turns his test suite red. The Mac instructions also need fixing before he runs anything.

---

## Must fix before ship

1. **Flipping the label breaks the suite. The Coder fixes it and the Tester re-attacks.**
   - Repro, on a fresh export with a stubbed transport: `capture` writes an honest file to `real/`, exits 0 and prints "CAPTURED CLEAN ... set TRIPS_SCHEMA_VERIFIED_BY".
   - Set that constant, as the message says, and the suite gives **6 failed** / 1500 passed / 20 skipped. The failures are:
     - `test_metal_end_to_end::test_b4_prints_vs_by_flight_number_with_every_qualifier`
     - `test_metal_lookup_model::test_while_the_constant_is_empty_every_known_render_is_labelled_unverified`
     - `test_seats_trips_parser::test_the_openapi_example_is_known_cm_tk`
     - `test_trips_flags::test_the_banner_splits_the_calls_and_carries_the_parser_label`
     - both tests in `test_trips_labels_everywhere`
   - The same capture also passes `totaltaxes_unit_problems("cents")`. Set `TRIPS_TOTALTAXES_UNIT = "cents"` as well and it's **28 failed**: `test_trip_taxes` x19, `test_yq_inclusion` Flying Blue x3, `test_trips_tools` yq-check, and more.
   - These tests hard-code the unverified state. Six sibling tests already skip with "the label has been flipped", so the pattern exists; it just wasn't applied everywhere.
   - **Why it matters:** the capture is the one action Tsuki is asked to take. If he follows the tool's own advice, he gets a red suite with no way to tell which reds are expected.
   - **Ask:** every assertion that depends on the label or the unit should branch on the constant or pin it with monkeypatch. Add one test that patches both constants to a genuine tmp capture and runs the modules that depend on them. The Tester then runs the full suite in both flipped states.

2. **The yq-check comparison rule can turn a clear answer into "record nothing". The Coder fixes it.**
   - The block says: "about equal to the row figure means includes_yq; about the row figure plus a separate carrier-charge line means excludes_yq; anything else is inconclusive, record nothing."
   - Suppose virginatlantic.com shows one combined "taxes, fees and carrier charges" total, well above the row figure, with no separate line. That is `excludes_yq`, and the rule tells him to record nothing. I can't verify from here how the site displays it today.
   - **Ask:** state the rule by the total. Site total about equal to the row figure means `includes_yq`. Site total about equal to the row figure plus the modelled band means `excludes_yq`; print the band in the block (VS J one-way: $200-$350). Anything else is inconclusive. Also tell him to check on the site that the flight is operated by Virgin Atlantic, not Delta.
   - This fails safe today, but it is the only step in the feature that can ever move money.

3. **The Mac instructions are wrong for his working copy. The Coder or coordinator fixes them.** Problems in the Coder report's "how to run":
   - It starts with a Trip B live run. The handoff says Tsuki does not want more example trips scored, and the run spends at least 4 searches plus up to 10 lookups to learn nothing new. Drop it.
   - It uses bare `python -m`. His copy runs from `.venv`, so it should be `.venv/bin/python`, as in every earlier review.
   - There is no step for getting the branch onto the Mac. The sandbox delivers a bundle.
   - A1 (`capture`) and A2 (`yq-check`) are listed as two runs, 4 calls. `yq-check` already writes the capture to `real/`, so one run covers both, in 2 calls.
   - It tells Tsuki to edit `src/seats_trips.py` and `data/yq_inclusion.csv` himself. Until item 1 is fixed, that turns his suite red. He should send the files and let the Coder do the flip.
   - On drift, the tool says "fix the parser until it reads this capture cleanly, then **capture again**" (`src/trips_tools.py:482`). That isn't needed. I checked: a capture that drifted gives `[]` from the flip check once the parser is fixed, with no new call. Change the message. Also tell him that a capture which exits 5 leaves exactly one red test (`test_every_committed_real_capture_parses_without_required_field_drift`), and that this is expected.

   The corrected steps are at the end of this review.

## Decisions Tsuki needs to make

- **D1. How far does one YQ check reach?**
  - As built, an `includes_yq` row scores every `virginatlantic` award at taxes only, whatever airline flies it: case B in `award_to_candidate` ignores metal. The check will be done on VS metal.
  - If Seats.aero builds its taxes figure differently for VS awards on Air France or KLM flights, those awards score too low. That is the v0 undercount again.
  - The Architect listed this as a "likely wrong" decision but did not put it to you.
  - Options: (a) one verdict per source, as built; (b) a verdict covers only the airline it was checked on, so VS awards on Air France stay unscored until a second check.
  - **I recommend (b).** It is cheap to change now, while the table is empty, and expensive once numbers have been quoted.
- **D2. Merge now, or hold until the check?**
  - Options: merge after the three fixes, or keep the branch unmerged until the YQ answer is in.
  - **I recommend merging after the fixes.** The code changes no number, it is well tested, and the check needs the branch on your Mac anyway. Build nothing more on this feature until the capture comes back.
- **T1-T3, the Architect's decisions. I agree with all three.**
  - T1: one `virginatlantic` check on VS metal, from a US airport. JFK->LHR in J keeps UK APD out of the comparison.
  - T2: defer. It changes a verdict, and it shouldn't come before the UI.
  - T3: `auto` with a cap of 10.

## Should fix soon

1. **`auto` spends calls on KrisFlyer awards that can never be scored.** The tool never believes `singapore` tax figures, and the YQ loader refuses a `singapore` row, so no lookup can ever move a KrisFlyer number. The reasoning is the same one behind `NOT_NEEDED_PARTY`. The Coder should skip these in `auto`; `--trips all` still shows them.
2. **`capture` and `yq-check` have no `--refresh`.** A search row cached for up to 6h can carry a stale availability id (a 404 after the trips call is spent) or a changed price (exit 5 from `_row_matches`). The odds are low, because the cache key is one route and one day.
3. **Size.**
   - `src/` grew from 16,623 to 21,241 lines (+28%) for output that changes no number. The whole diff is +16,098 lines: 4,688 in src, 5,735 in tests, 3,917 in probes and 1,913 in docs.
   - Not justified by any current need: the anti-forgery layer of the flip check (R2-5: synthetic-page fingerprints, typed canonical JSON, required `_meta`). It is about 100-150 lines plus a 172-line test file, and it guards a folder only Tsuki writes. The README itself says a deliberate forgery still passes.
   - Code that sits unused until data arrives: case C and the `cents` path for per-itinerary taxes.
   - Keep all of it, since it is tested, but freeze the feature until real data arrives.
4. **Output volume.** A KNOWN VS leg adds about 10 lines, printed twice (leg detail and the live block), and each line ends in the same 100-character label. Fold them into one line per leg when the UI is built.
5. **Pre-existing problems this review surfaced.**
   - Live output still prints "the v0 bug", which is changelog text.
   - `tests/test_apd_verification_gate.py` still skips 4 tests on the grounds that nobody has checked whether TotalTaxes contains UK APD. The handoff says this was verified on real LHR rows (£170-£379). Write the answer on the gate line so those 4 tests start asserting.
6. **Deleting a bad trips row changes the replay's manifest hash.** That is the documented remedy, and afterwards a number quoted against the old hash won't reproduce. The refusal message should say so.
7. **Unverified.** The sandbox runs requests 2.33.1 and an unpinned rich, not the pinned 2.31.0 and 13.5.0, and the label assertions depend on rich's markup handling. The real check is a suite run on his Mac.

## Agent performance

- **Architect:** Read the code first and was right to overrule the handoff (below). It also picked the one check that can settle the question: VS on VS metal, from a US airport. Its miss is the order of work. It planned 14 steps (replay, the manifest format, cash cases C and D, per-itinerary taxes) on a response schema nobody had seen, when its own top risk named that schema as the thing most likely to be wrong. It also left D1 off the list of decisions for Tsuki. **To improve:** when a real capture is one Mac command away, plan round 1 as the capture tool plus a minimal parser, and build the rest against real data.
- **Coder:** Followed the plan, disclosed 8 deviations, and fixed all 29 findings, each with a regression test. It says each of those tests fails on the old commit; I didn't re-check that. I did reproduce every test count it reports. But it never ran its own "only then set TRIPS_SCHEMA_VERIFIED_BY" step, which is Must-fix 1. Its how-to-run also ignores Tsuki's "no more example trips" rule and his venv. **To improve:** run every instruction you give Tsuki on a clean export before writing it down.
- **Tester:** Thorough and honest: 431 executed probes behind a socket canary, class checks beyond each repro, byte-identity checks against master, and a fair ruling against its own probe. But it spent two rounds hardening the flip against forgery by the only person who writes that folder, and never set the constant and ran the suite. It also left two things unattacked: the human half of yq-check (the decision rule) and case B's metal-blind scope. Those are the two places a wrong number could come from. **To improve:** attack the next step the user is told to take, not only the code.

## What's solid (verified myself)

- **Full suite:** 1513 passed / 13 skipped, and the same under `-O`.
- **Probe red sets, compared by test id:**
  - v5: 19, identical to the baseline.
  - adversarial: 40, identical to the baseline.
  - known-failures: 0 red, 140 green.
  - operating-airline: 0 red / 431, and the same under `-O`.
- **Simulated Mac:** a fresh `git archive` export with a key exported gives 1513 / 13, and the checkout was unchanged after the run.
- **Nothing moves, end to end:** I ran Trip B through the full CLI with a stubbed transport, three times: B4 KNOWN VS, B4 KNOWN DL, and an empty list. I compared each against `--trips off`.
  - Every run exits 0.
  - After normalising timestamps and the call count, no line outside the operating-airline lines differs.
  - What the feature adds: the operating-airline, flights, per-itinerary-tax and "band NOT ADDED" lines, plus the banner counts.
- **trips_tools, run with the network guard on:**
  - `--help` exits 0; no subcommand exits 1.
  - Every usage error exits 1 and names the reason.
  - With no key, it exits 1 before any prompt.
  - Answering "n", and a closed stdin, both exit 1 with 0 calls and nothing written.
  - A network failure exits 1 with nothing written.
  - `qatar` is refused before any call.
- **The capture itself:**
  - A clean stubbed capture into the checkout's `real/` exits 0 and prints the flip advice.
  - A drifting one (cabin word "premium economy") exits 5 and leaves 1 red test.
  - After a one-line parser fix, that same file verifies with no new call.
- **Unverified:** the real trips response; whether Seats.aero lists a BA source today; how virginatlantic.com shows carrier charges; macOS; the pinned rich and requests versions.

## Your five questions

1. **Value versus honesty.**
   - **What Tsuki gets today.** On a live trip run, for Virgin Atlantic, Flying Blue, JetBlue and KrisFlyer awards on one-traveller legs, he sees:
     - the flight numbers and the marketing carrier, stamped UNVERIFIED;
     - the modelled surcharge band for that airline, marked NOT ADDED;
     - occasionally an unpriced "same airline, other program" alternative, for example Flying Blue for a VS award on an Air France flight.

     It costs 1-10 extra calls per run. No score, verdict, range or exit code moves.
   - **To get more,** he runs one `yq-check` (2 calls), reads one figure off virginatlantic.com, and fills in five blanks. After a Coder round, Virgin Atlantic awards become scoreable for the first time: at the taxes alone if the answer is `includes_yq`, or taxes plus the band if it is `excludes_yq`. Every other program needs its own check.
   - **The trade.** The honesty is right: no line claims more than it knows. The order is wrong. About 4,700 lines of src were built before the one 2-call capture that could invalidate the parser. And the most valuable outcome, `includes_yq`, doesn't need the airline at all: case B scores VS awards on the row's taxes alone. A round that built only the two tools and the YQ table would have delivered the same first number for a fraction of the code. It is built and tested now, so the cost is maintenance, not risk. Don't extend it before the capture comes back.
2. **The Architect's claims: the Architect is right.**
   - `SEATS_AERO_SOURCES` holds 28 codes: the 26 in Seats.aero's published table (as pinned in `tests/test_source_map.py`, read 2026-09-10), plus `ana` and `lifemiles`.
   - None of them is BA, Iberia or Aer Lingus. A code comment says they were left out on purpose.
   - In `programs.yaml`, only BA, Iberia and Aer Lingus can ticket BA or IB flights. Virgin Atlantic's list is VS, DL, AF, KL.
   - So "a BA- or VS-program award on a BA flight" can't come from Seats.aero data. The handoff's line "all 26 sources mapped" is still true, but it doesn't support the check the handoff proposed.
   - Unverified: whether Seats.aero has added a BA source since 2026-09-10. I can't reach it from here.
3. **One-way doors.** None is binding yet. No trips snapshot exists anywhere, and they bind the first time his Mac writes one and a number is quoted against it.
   - **`trips_endpoint/` layout:** fine. The subdirectory keeps trips files out of the existing search globs.
   - **Trips manifest format:** fine. It reuses the search columns with different meanings (the route column holds `trips:<id>`). That costs some documentation and buys reuse of the existing tamper checks.
   - **`manifest_hash` extension:** safe. Lines are added only when trips rows exist. The Step 0 pins pass (I ran them), and the Tester reports that an old corpus gets the same hash from master's own `manifest_hash`. One side effect is Should-fix 6.
   - **`yq_inclusion.csv`:** the format is fine. The problem is what a row means (D1). Decide that before the first row.
4. **Tsuki's first real run.** The failure paths work (see "What's solid"). The instructions are incomplete (Must-fix 3). Harmless reasons the capture might not flip the label:
   - **One unreadable itinerary blocks the flip, even in another cabin.** A cabin word other than the four guessed makes every cabin of that row NOT KNOWN and exits 5. That is expected and cheap: a one-line parser fix, and no new call.
   - **No itinerary matches the row's price.** This happens if `min_cabin_pct=100` filtered it out, or the search row came from a cache up to 6h old. It exits 5 and needs a new capture, 2 more calls.
   - **`Carriers` names the operating airline on a codeshare.** That gives `TRIP_INCONSISTENT` and exits 5. This one is information, not noise.

   A clean first capture is less likely than not, and the plan expects drift. Either way, the file is what has value: send it.
5. **Size:** see Should-fix 3.

## What Tsuki does on his Mac (after the three fixes)

1. In `~/Downloads/points-optimizer-git`, get the branch from the bundle you're sent:
   `git fetch <bundle> feature/operating-airline:feature/operating-airline && git checkout feature/operating-airline`
2. Run `.venv/bin/python -m pytest -q -p no:cacheprovider`. Expect 1513 passed / 13 skipped. That is the sandbox figure; your Mac matched the sandbox on master, but this run is unverified. It makes no API calls.
3. On the seats.aero website (free, no API call), find a date with Virgin Atlantic Upper Class space from JFK to LHR on a flight Virgin operates itself. Then run the command below. It asks before spending anything and makes 2 calls at most:
   `.venv/bin/python -m src.trips_tools yq-check --origin JFK --destination LHR --date YYYY-MM-DD --source virginatlantic --cabin J`
4. On virginatlantic.com, look up the same flight for one adult and fill in the five blanks in `docs/yq-checks/<today>-virginatlantic.md`.
5. Send back the terminal output, that record, and the two files it wrote under `tests/fixtures/seats_aero/trips_endpoint/real/`. Push them on the branch, or attach them. Don't edit `src/seats_trips.py` or `data/yq_inclusion.csv`; the Coder does that.
6. Exit 5 is fine. One test stays red until the parser is fixed. Don't run the capture again.
7. Skip the Trip B run in the Coder's report.
