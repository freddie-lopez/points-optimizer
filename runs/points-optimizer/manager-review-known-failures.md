# Manager review: `fix/known-failures` (head 0a66ee4, base ad83802)

**Verdict**: Ship with fixes. The branch fixes the ten Mac failures and a real false WIN, and I reproduced both. But a separate false WIN that was already on master sits in Tsuki's main use case (flying as a couple), and none of the three agents flagged it as a defect. It needs a guard before he uses the tool. Also, the last fix commit has not been through adversarial testing yet.

---

## Must fix before ship

1. **CRITICAL, already on master: a multi-traveller flight leg prices ONE seat of points against whatever cash was entered. Coder adds the guard; Architect plans the real fix.**
   Repro (branch, stubbed transport): Trip B B1 with `travelers: 2`, cash entered as the couple's total $790 (2 x $395), and a live United award of 50,000 + $56. Result: verdict **POINTS**, +29.6%, and the headline says **"beats paying cash by 6.65%"** as a single number. "Points spent" shows 50,000. The true cost is 100,000 pts + $112 = $1,112 against $790, so cash wins by about $322. `--new-trip --travelers 2` writes exactly this kind of leg: the description says "2 adults" and there is one `CASH_USD` field with no stated unit.
   The problem shows up in several places:
   - The points, the award taxes and the trip balance ceiling are all counted x1, while APD is counted x N.
   - The branch's new `_add_apd_to_unscored_floor` makes this worse: floor = 1 seat of points + N x APD. That is the "$576.22" the Tester noted and did not score.
   - Search mode prints `Passengers: 2`, then ranks and prices a single seat.
   - `seats_available` is never compared to the traveller count, so a 1-seat award is offered to a couple. The triage review asked for this check (§1.4). The plan never answered it.

   Ask: until real per-party pricing exists, any flight leg with `travelers > 1` (on the live, replay and offline paths) and any search with `--passengers > 1` must withhold the points verdict. Print one line: "priced for ONE seat; multi-traveller award pricing is not modelled". Report it through way-ten (withheld, not scored), and add a test that fails on master. After that, plan the real fix once Tsuki answers decision D1.

2. **Tester: attack `e64c32a` before sign-off.** It changed 172 lines of `src/`: search dedup and rank, the duty rule in search mode, off-date findings, the parser-disagreement banner, the `last_search_awards` side channel, and the R-8 atexit cleanup. It was committed five minutes after the Tester's re-test of `278332e`, and nobody has attacked it. My spot checks passed: R-1 and R-2 behave end to end, and the probes are 98/98 green. That is not an adversarial pass, and Tsuki's rule is never to skip one. Scope: those changes plus the guard from item 1.

## Decisions Tsuki needs to make

- **D1. What does `CASH_USD` mean on a multi-traveller leg?** (a) per person, or (b) total for the party. **Recommend (b)** with the prompt and flag saying "TOTAL for all N travellers". Then multiply points, award taxes and APD by N, and require seats >= N. Treat `american`'s 0 seats as "not reported".
- **D2. KrisFlyer is now never scored.** Singapore KrisFlyer is a *direct* 1:1 UR partner. The branch rejects any tax figure from the `singapore` source, so every KrisFlyer award can only ever show a floor and a break-even, never a POINTS verdict. The same applies to `qatar` and `turkish`. Options:
  - (a) accept this;
  - (b) add a manual "captured award taxes" input per leg, taken from the airline's booking page the way cash is captured, so a KrisFlyer award can be scored;
  - (c) trust non-zero `singapore` figures (no).

  **Recommend (a) now, (b) next.** This rule rests on the Seats.aero docs footnote, which I could not verify here (no network).
- **D3. Qatar and Finnair via BA Avios are "named, not scored".** The Architect made this choice for Tsuki and labelled it. Even if he chooses to score it, Qatar's taxes are unreported, so it could never be more than a floor. The companion rule matters for his girlfriend: she needs her own Privilege Club account, at least 30 days old, with earned Avios. **Recommend: keep it not scored**, and add an informational "if your BA->Qatar combine is set up" floor line.
- **D4. The three tax-trust rules.**
  - A tax figure of 0 from any source is treated as unknown.
  - A UK tax figure below the APD for the award's cabin is treated as unknown.
  - APD is added to the floor when the taxes can't be used.

  **Recommend accepting all three.** One caveat: the below-APD rule assumes an adult passenger who is not in transit. APD can legally be £0 for an onward connection within 24h on one ticket, and for under-16s in economy or under-2s. That's fine for his point-to-point adult trips, but the code and report words "provably incomplete" overclaim.

## Should fix soon

1. **The certain-cash sentence contradicts its own leg (Coder, Low, shows up on his real B4).** I replayed a corpus captured by master on the branch. B4 prints: "Cash is cheaper: $482.00 vs at least **$500.00** ... The **carrier-imposed surcharge** is UNKNOWN". The table on the same leg shows `>= $638.11`, and what is actually unknown is the taxes (Aeroplan's surcharge is a known $0). Cause: `optimizer.py:1252-1258` writes this sentence before `apply_apd` runs, and `_add_apd_to_unscored_floor` only rewrites the verdict `cash (surcharge unknown)`. `margin_usd` also stays at the pre-APD value.
2. **The search-mode floor leaves out owed APD (Coder, Low).** An LHR-SFO search with United 35,000 + $5 shows `>= $350.00`. The APD that is definitely owed puts it at >= $488.11.
3. **The README doesn't mention the new tax rules (Coder, Medium).** It says nothing about the `singapore`/`qatar`/`turkish` taxes never being believed, the zero-is-unknown rule, the below-APD rule, or the three `POINTS_OPTIMIZER_*` environment variables. Without that, KrisFlyer vanishing from recommendations will look like a bug.
4. **Production code reads environment variables added for the tests (Coder, Low).** If `POINTS_OPTIMIZER_ENV_FILE`, `_CACHE_DIR` or `_SNAPSHOT_DIR` is set in his shell, live runs silently read a different key file, or cache and archive somewhere else. Print them in the key/live banner whenever one is set.
5. **A known-red "held-up" probe (Tester).** `v5-probes/test_v5_held_up.py::test_a_live_leg_states_apd_without_adding_it` is red by design: its corpus puts $32.36 of taxes on an LHR departure. Fix the corpus so the suite goes back to master's 19-red set. A guard that is red by design teaches everyone to ignore red.
6. **Some tests still depend on the local corpus (Architect).** The sandbox skips 13 and the Mac skips 4 (`test_live_trip_b.py`, `test_apd_verification_gate.py`). The triage review raised this in §4 and the plan ignored it. Committing the real corpus fixes it, and until then CI is vacuous for those 9 tests.
7. **The harness on macOS is unverified (Coder).** Child processes get a temp `HOME`. If Tsuki installed dependencies with `pip --user` (outside a venv), children lose `~/Library/Python/.../site-packages`, and every subprocess test fails with `ModuleNotFoundError`. Pass the real `PYTHONUSERBASE` to children, or document "run from the venv". Chaining to another `sitecustomize` has also not been tried on a macOS Python build.
8. **Debt (Coder, Low).**
   - `optimize()` hands its awards to `run_search` by setting an attribute on the client inside a try/except. It should return them.
   - The table at the top of the Coder report is stale: 891/899 there, against the final 919/928.
   - Five sources' notes now say "Observed in the live Trip B run of 2026-09-10". The repo contains no evidence of that run.

## Agent performance

- **Architect:** Read the code before planning and corrected the triage in four places, each with a reproduction (the key leak, the cache hit, the vacuous scanner pass, missing taxes scored as a win). Missed the traveller count and seat counts even though the review asked for them, the APD exemptions, and the corpus-dependent skips. Treated "KrisFlyer never scored" as mechanics instead of a decision for Tsuki.
- **Coder:** Followed the plan faithfully and listed its gaps honestly (gap 1 turned out to be High). Fixed all 21 Tester findings, each with a regression test. Weak spots: shipped `e64c32a` without it being attacked, adopted the below-APD rule without marking it as Tsuki's call, and left a stale table in the report.
- **Tester:** Strongest of the three. Found 3 High issues the plan missed. Verified with a canary proxy, byte-identical checkouts and red sets diffed by test id, and owned up to its own probe errors. Recorded the traveller x1 / APD x N problem as an "observation" when, for a couple, it is a Critical false win. Missed the stale certain-cash sentence.

## What's solid (verified myself)

- **Full suite:** 919 passed / 13 skipped, and the same under `python -O`.
- **Probe suites** (red sets diffed by test id against a clean, cache-free export of master):
  - v5-probes: 20/78, which is master's 19 plus the one flip.
  - adversarial-probes: 40/38, the same red set as master.
  - known-failures-probes: 98/98 green.
- **Simulated Mac** (fresh exports into `points-optimizer-v5`, key exported, warm cache, qatar tax-0 corpus):
  - Master: 10 failed / 798 passed / 4 skipped. They are the triage's exact ten. The failures came from warm-cache hits, not from the network.
  - Branch: 928 passed / 4 skipped, both with a corpus captured by the branch and with one captured by master, and also under `-O`.
  - With the key both exported and in `.env`, the checkout was byte-identical after the run: pytest wrote nothing to the cache, the corpus or the fixtures.
  - The probe counts are the same under Mac conditions.
- **Trip path, end to end:** KrisFlyer with $150 of taxes on B1 and United with 0 taxes on B1 are both withheld. The headline is a range labelled "award TAXES", with no POINTS verdict and no single number.
- **Search, end to end:**
  - SFO-MAD: United $56 ranks #1, KrisFlyer shows `UNKNOWN` / `>=`, `$0.00` appears nowhere, and the unknown United award no longer pushes out the known one.
  - LHR-SFO with $5 of taxes: the cash shows UNKNOWN and the top line reads "UNKNOWN - NOT $0.00".
- **Replay of a master-captured corpus:** prints `REPARSED UNDER A DIFFERENT PARSER VERSION`. The Qatar row is named as an indirect path with its conditions, and B4 is PAY CASH at `>= $638.11`.
- **Unverified:** the Seats.aero "taxes not available" footnote, the Finnair/BA/Qatar combine conditions, anything specific to macOS Python, and Tsuki's real corpus.

## What Tsuki does on his Mac

1. Don't trust any multi-traveller flight output until Must-fix 1 lands.
2. After the merge, run `.venv/bin/python -m pytest -q -p no:cacheprovider` from the venv. Expect **928 passed / 4 skipped**. That figure comes from the simulation; the result on his real corpus is unverified.
3. Commit the real snapshots: `tests/fixtures/seats_aero/live_trip_b/*.json` plus `MANIFEST.md`. The key-free test runs over them first. Until then, the Group C fix is proven only on synthetic data.
4. Replays of his corpus will print **REPARSED** (parser `2026-09-09.v5` -> `2026-09-10.taxes-trust`), and B4's answer will change: Qatar becomes "indirect, not scored", and any tax figure below the APD is distrusted. That is by design, not a regression.
5. pytest no longer touches his key, cache or corpus, and uses no API quota.

---

## Sign-off re-review (4940154)

**Verdict**: Ship. Must-fix 1 and 2 are closed and I re-ran every claim myself. There are no Critical, High or Medium findings left open. One consequence Tsuki has to know about: until he answers D1, every trip with a flight leg for 2 travellers (a couple's trip) withholds its headline and exits 3. That is the honest behaviour, and it means the tool gives him no flight answer for trips with his girlfriend until D1 is decided and built.

**What I verified myself at 4940154:**
- **Full suite:** 937 passed / 13 skipped, and the same under `-O`.
- **Probe suites:**
  - v5-probes: 19/79, the same red set by test id as a clean, cache-free export of master. The corpus repair only swaps B4's taxes for United's real $224.63; the assertions are unchanged.
  - adversarial-probes: 40/38, the same red set as master.
  - known-failures-probes: 140/140 green.
- **Simulated Mac** (fresh export into `points-optimizer-v5`, key exported and also in `.env`, warm cache, qatar tax-0 corpus): 946 passed / 4 skipped, both with a corpus captured by master and with one captured by the branch, and also under `-O`. The checkout was byte-identical after both runs.
- **My own couple repro** (B1 for 2 travellers, the party's $790, United 50,000 + $56) no longer produces a POINTS verdict. The leg gets `cash (multi-traveller points not priced)`, with no floor, no break-even and 0 points spent. On the CLI, Optimizer's recommendation, Saving and the percentage all read WITHHELD, with the reason named, and the exit code is 3. The R2-1 side door is closed: an unpriced partner on a party leg gets no one-seat break-even.
- **Search:** `--passengers 2` shows the "PRICED FOR ONE SEAT" banner and names no top strategy. With one passenger, United 50,000 + $56 ranks #1 and the cheaper 30,000 award with unknown taxes is shown after it as `>= $300.00` instead of being dropped. The R2-3 fix holds.
- **Replay of the master-captured corpus:** prints REPARSED. B4 now reads "at least $638.11 ... INCLUDING UK Air Passenger Duty ... the award's other taxes are UNKNOWN". The $500 figure and "carrier-imposed surcharge" are gone.
- **The real repo:** no `.env`, no `data/cache/`, no snapshots in `live_trip_b/`, and no leftover test-home directories.

**Still must-fix:** none.

**Should fix soon (unchanged or new, all Low):**
1. Exit 3 now means two things: an API or provenance failure, and a party leg that was not priced. It is documented, but a script that retries on 3 would retry forever on a couple's trip. Coder: give the party case its own exit code when D1 is built, or sooner.
2. On the `--offline` badge path, a party leg that departs the UK still prints "APD ... ADDED to the points-side cash total x2" when no points side exists (Tester R3 observation; the wording dates from v5). Coder.
3. The `last_search_awards` side channel stays in place and is documented. Coder: return the awards from `optimize()`.
4. The corpus-dependent skips (13 in the sandbox, 4 on the Mac) remain until Tsuki commits his corpus.
5. Process: the Coder edited the Tester's v5 probe (I had assigned that to the Tester). The edit is sound, but a probe should be changed by its owner.
6. Unverified: macOS Python behaviour (the `PYTHONUSERBASE` pin and `sitecustomize` chaining), the Seats.aero "taxes not available" footnote, the BA/Qatar/Finnair combine conditions, and Tsuki's real corpus.

**Decisions for Tsuki (final):**
- **D1. Multi-traveller pricing: now urgent, because it blocks every couple's trip.** Does `CASH_USD` on a leg mean the fare per person or the total for the party? **Recommend: the total for the party**, with the prompt and flag saying so. Then model N x points, N x award taxes, N x APD and N x the balance draw, and require seats >= N, treating `american`'s 0 seats as "not reported". Until this is built, a trip with a party leg is WITHHELD (exit 3), and search for 2+ passengers prints a per-seat list only.
- **D2. KrisFlyer (a direct 1:1 UR partner) is never scored,** because Seats.aero doesn't report `singapore` taxes. The same goes for `qatar` and `turkish`. **Recommend: accept this now, and add a manual "captured award taxes" input next** so a KrisFlyer award can be scored from the airline's booking page. The README now states this rule.
- **D3. Qatar and Finnair via BA Avios are named, not scored.** Even if scored, Qatar's taxes are unreported, so it could never be more than a floor. **Recommend: keep it not scored**, and add an informational "if your BA->Qatar combine is set up" floor line. The companion rule applies to his girlfriend: she needs her own Privilege Club account, at least 30 days old, with earned Avios.
- **D4. The tax-trust rules:** a figure of 0 from any source is unknown, a UK figure below the APD for its cabin is unknown, and APD is added to floors when no usable figure exists. **Recommend accepting all three.** The below-APD rule now states its assumption in the output: an adult who is not on an onward connection. The APD exemptions are not modelled.

**What Tsuki does on his Mac:**
1. Merge or pull `fix/known-failures` at `4940154` or later. From the venv, run `.venv/bin/python -m pytest -q -p no:cacheprovider`. Expect **946 passed / 4 skipped**. That comes from the simulation; the result with his real corpus is unverified. If subprocess tests fail with `ModuleNotFoundError`, run from the venv.
2. Commit his real snapshots: `tests/fixtures/seats_aero/live_trip_b/*.json` plus `MANIFEST.md`. The key-free test runs over them first. This turns the 9 corpus tests from skips into real checks.
3. Replays of his corpus will print **REPARSED** (parser `2026-09-09.v5` -> `2026-09-10.taxes-trust`), and B4 changes: Qatar becomes "indirect, not scored", and a tax figure below the APD is distrusted and the duty added. That is by design.
4. Solo trips work as before. A trip with any flight leg for 2 travellers prints WITHHELD and exits 3 until D1 is built. Price those legs by hand: 2 x the points and 2 x the taxes against the pair's total cash.
5. pytest no longer reads his key, cache or corpus, and uses no API quota. Live runs still archive into the committed corpus by design. If a `POINTS_OPTIMIZER_*` variable is set in his shell, the banner now says so.
