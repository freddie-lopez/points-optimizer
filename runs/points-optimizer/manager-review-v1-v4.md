# Manager review — v1, v2 (parser), v3 (live mode), v4 (adversarial fix round)

**Author**: Manager
**Date**: 2026-09-09
**Reviewed**: `docs/plans/v1.md`, `docs/plans/v3.md`, the v0 plan in the project
docs, four coder reports, the Tester's 25-finding adversarial report and its 78
probes, and `src/` as shipped (15,764 lines).
**Why this exists**: four versions shipped without a manager pass. This closes
that debt. Everything below that says "verified" was executed by me in this
sandbox; everything I could not execute is labelled unverified.

---

## Verdict

**Ship with fixes.** The v4 fix round is real work, honestly reported, and the
25 findings it claims are fixed are fixed — I re-ran every probe and the 39 that
flipped are exactly the 39 the report names. But the project's signature failure
mode is **not** closed. I found a fourth live instance of it in fifteen minutes
of probing, on an axis the fix round never touched, and it renders byte-identical
text to the bug that motivated the whole round. Separately, four versions of work
sit in a git repo with **zero commits** and a `.env` holding the live API key that
is **not gitignored**.

Nothing here says do not ship. It says: four things must land before anyone
builds v5 on top of this.

---

## What I verified myself

Every claim in the v4 fix report's headline is true.

| Claim | Result |
|---|---|
| `python -m pytest -q` → 512 passed, 9 skipped | **confirmed**, 10.45s |
| `python -m pytest docs/test-reports/adversarial-probes/ -q` → 39 failed, 39 passed | **confirmed** |
| The 39 flipped probes are exactly the ones the fix table names | **confirmed** — I diffed the failure list against the report's table; exact match, no probe was edited |
| `python -O -m pytest -q` → 512 passed, 9 skipped | **confirmed**, and `-OO` too |
| Badge-mode Trip B at UR=160,000 → 6.46%–15.45%, $3,126.11 all-cash, 28,000 UR spent | **confirmed**, exit 0 |
| `--live --require-all-live` with no network withholds and exits 3 | **confirmed** — margin `WITHHELD`, all four legs rendered as `THIS IS AN API FAILURE ... It is NOT a finding of no availability`, exit 3 |
| The 9 skips are the Step 10 live gate | **confirmed** |
| No API key appears in any fixture, snapshot or cache file | **confirmed** — I grepped the whole tree for the 31-character key in `.env`; zero hits |

I also independently re-derived, with my own code rather than the Tester's
probes, that the balance ceiling binds at exactly 160,000 UR, that the Hyatt
Oct-1 cliff is keyed on the transfer date, that `--max-stranded-points 0` rejects
a real overshoot, and that R3 does not split when one currency suffices. Details
in the "held up" section below.

**The v4 Coder's report is the most honest document in this project.** It
discloses two existing tests it changed and why, names a defect it found and
deliberately did not fix, states that the greedy trip-budget ordering is not
proven optimal, and states plainly that no live behaviour was verified. I found
no undisclosed deviation in it.

---

## Must fix before ship

### 1. The failure-as-finding class is NOT closed. This is the fourth instance. — Coder

**Critical.** The whole premise of v4's root cause 1 is that a sixth state plus a
`__post_init__` clause makes "we could not get usable data, so we said there is
nothing there" *unbuildable*. It does not. It makes three specific instances
unbuildable — unreadable rows, budget breaks, wrong-shaped envelopes — and leaves
a fourth reachable on the pagination axis.

Repro (mine, executed):

```
payload: {"data": [], "hasMore": true}     # server says there is more; no cursor to follow
  state           : LiveQueryState.NO_AWARD_SPACE
  pagination_note : STOPPED after page 1: the response says there are more results
                    (hasMore) but carries no cursor and no usable offset ...
  render          : Seats.aero returned NO award space for SFO->MAD on 2027-01-15
                    (searched SFO->MAD on 2027-01-15, 0 rows). THIS IS A FINDING:
                    the API answered, every row it sent was READ SUCCESSFULLY, and
                    the answer is that there is no award to buy on this date.

payload: {"data": []}                       # genuinely complete and empty
  render          : ...byte-identical to the above...
```

We saw one page of an admittedly truncated result set, that page was empty, and
we announce "there is no award to buy on this date" — and we announce it in
exactly the same words as the case where we actually know. The user cannot tell
the two apart.

The machinery to fix this already exists and is disconnected:
`RawSearchResult.incomplete` is defined at `src/seats_client.py:159` and set at
`:920`, but the client never exposes it as `last_incomplete`,
`live_trip.query_leg` (`src/live_trip.py:196-315`) never reads it,
`LiveLegOutcome` has no field for it, and `__post_init__` has no clause for it.
The `INCOMPLETE` marker survives only on `pagination_note` — **which
`NO_AWARD_SPACE.render()` still does not read.** That is verbatim the structural
complaint the Tester made in C-1 ("that note lands on `outcome.pagination_note`,
which `render()` never reads for the `NO_AWARD_SPACE` case"). The fix addressed
C-1's instance and left C-1's mechanism in place.

**Ask**: plumb `incomplete` through to `LiveLegOutcome` as a field; add a
`__post_init__` clause forbidding `NO_AWARD_SPACE` when the result set was
truncated; make `_skipped_clause()`'s sibling — a `_truncation_clause()` —
unconditional on every render, the same way `rows_skipped` now is. And answer the
general question the class keeps asking: **what is the exhaustive list of ways we
can fail to know, and is each one a field on the outcome?** Three rounds of
fixing instances have not produced that list.

### 2. L-1 is fixed at two of its three call sites — Coder

**High.** The fix report marks L-1 fixed: an unpriceable currency is now a
per-leg problem, not a whole-run abort. That is true for `cash_options` and for
`mandatory_fees`. It is not true for a **captured surcharge**.
`_captured_surcharge` (`src/optimizer.py`) calls `convert_to_usd` with no guard,
and `surcharge_currency` is a documented trip-fixture field
(`src/trip_loader.py:83`).

Repro (mine, executed) — Trip B with `surcharge_captured: true`,
`surcharge_currency: "JPY"` on B1's candidate:

```
Error: No FX rate configured for 'JPY'. Add it to config.FX_RATES_TO_USD.
EXIT=1
```

Whole trip abandoned, no leg named — the exact behaviour L-1 described, and worse
than the original, because L-1's message at least came from a code path the fix
then taught to name the leg. This matters for v5 specifically: a `--new-trip`
fixture builder will generate this field.

**Ask**: route `_captured_surcharge`'s conversion through the same per-leg
handling `cash_options` now uses — an unpriceable captured surcharge is UNKNOWN
for that leg, not a dead run.

### 3. Four versions of work, zero commits — Coder / team lead

**High.** `git log` returns *"your current branch 'master' does not have any
commits yet"*. `git ls-files` returns 0. Nothing in this project has ever been
committed.

Every claim of the form "committed snapshot", "committed regression corpus",
"committed fixture", "no probe was edited" is therefore unverifiable — not
false, unverifiable, which for a project whose entire discipline is provenance is
its own kind of problem. It also means there is no way to see what v4 actually
changed versus what v3 shipped; I had to take the fix report's file table on
trust. And it directly blocks v5: `--from-snapshot` replay is designed around a
*committed* manifest (v3 §11.3, and the v3 Coder put it first in his own v4
recommendations).

**Ask**: initialise properly and commit, ideally with one commit per version so
the four rounds are separable. Do MR-4 first.

### 4. `.env` with the live Seats.aero key is not gitignored — Coder

**High.** `.gitignore` covers `wallet.json` and `data/cache/`. It does not cover
`.env`. `git check-ignore -v .env` → not ignored. The file holds a 31-character
`SEATS_AERO_KEY`. Combined with MR-3, the first `git add -A` commits Tsuki's Pro
API key. The v0 plan §2 says in bold that the key must never be committed.

**Ask**: add `.env` to `.gitignore` before any commit is made. This is also the
natural moment to do v5's `~/.config` key resolution — `config.load_env` is
hard-coded to the repo-local path (`src/config.py:20`).

### 5. `Award.cash_component_known` defaults to `True` — Coder

**Medium, raising to must-fix because of what it is.** The v4 Coder found this
himself and deliberately left it, which I respect and which is why he gets credit
for it below. But it is the last live instance of root cause 2, and this project
does not get to leave one of those lying around.

Verified: `Award.cash_component_known` has default `True`. So a Google-badge
award constructed with `cash_component=0.0` and no tax data is a **known** zero,
and renders `$0.00` in the HTML export with no marker. That is precisely v0's
bug — silence rendering as a confirmed free — and it is an honesty flag whose
default is the unsafe value, in a codebase whose own comment two files away reads
*"`cash_surcharge: 0.0` with no flag is silence, not a real zero."*

It also sits on the `--origin/--destination` single-route path, **which the
Tester never attacked at all**. That path is a coverage gap, not just a bug.

**Ask**: flip the default to `False` and make every construction site state its
answer. Expect a handful of test changes; each is a real question being asked for
the first time.

---

## Decisions Tsuki needs to make

**1. Trip B's fixture has been incoherent for four consecutive versions.** v0,
v1, v2 and v3 each flagged it and none fixed it, correctly, because it is your
call. It is still wrong today and the tool prints the flags on every run: the
Madrid and Amsterdam hotels are both booked Jan 15–19 (you cannot be in two
cities on the same four nights), B1 is **SFO**→MAD while the description says
MRY→MAD so the positioning flight is unpriced, and flights are priced for 1 adult
while hotels are priced for 2.

This stops being a documentation nit the moment Step 10 runs, because live mode
queries the airports the fixture names and spends real API calls doing it. A live
margin over an incoherent itinerary is precise about the wrong trip.

**Options**: (a) correct the fixture before Step 10; (b) run Step 10 anyway and
treat the margin as a plumbing test, not a number. **Recommendation: (a).** It is
twenty minutes of your time and it is the difference between v5 producing a
number you can use and a number you have to caveat.

**2. Should v5 start at all before Step 10 runs on your Mac?** Step 10 is the
gate the v3 plan itself set, it has never run, and the nine skipped tests are
waiting on it. v5's headline feature (`--from-snapshot` replay) consumes a
snapshot corpus that **is currently empty** — `test_every_committed_snapshot_parses_into_valid_awards`
passes vacuously today.

**Options**: (a) run Step 10 first, then build v5 against real snapshots;
(b) build v5 against synthetic snapshots and backfill. **Recommendation: (a).**
Building a replay feature with nothing to replay is how you discover the envelope
format is wrong on the day you can least afford it — and this project already
learned that lesson once, when the one real captured response immediately exposed
a parser that had been wrong for the entire life of the codebase.

**3. Is the multi-currency engine worth keeping?** `data/ratios.csv` contains 18
rows and **all 18 are UR**. The entire multi-currency funding machinery — R1–R4,
`MAX_SPLIT_CURRENCIES`, and v4's L-6 fix — is unreachable in production and can
only be exercised against synthetic ratio data. The v1 plan's §10 predicted
exactly this ("if Tsuki holds only Chase cards, the multi-currency work is dead
weight"). You have since confirmed UR-only.

**Options**: (a) leave it, it costs nothing at runtime; (b) populate MR rows,
since you hold an Amex Hilton Surpass; (c) delete it. **Recommendation: (a) for
now, (b) when hotels become interesting** — Hilton is an MR partner and is
currently reported as "no points path" on Trip B's B7, which is true under
UR-only scope and false about your actual wallet.

---

## Should fix soon

- **Exit code 4 is undocumented.** v4 added exit 4 ("plan not executable") to sit
  alongside 3 (`--require-all-live` withheld) and 1 (error). It appears in the fix
  report and nowhere else — not in `--help`, not in `README.md`. Any script
  wrapping this tool cannot tell the three apart. Document all exit codes in one
  place.
- **Internal changelog is leaking into user-facing output.** Trip B's B7 flag now
  ends with a paragraph explaining what the flag used to say before v4 and cites
  "finding L-7" by name; B6's flag explains what v0 did with the city tax. Tsuki
  is reading a booking recommendation, not the project's own commit history. Move
  it to the report, keep the current fact in the output.
- **Three of the Tester's 15 "held up" properties lost their evidence.** Held-up
  #14 (snapshot dedup) cites `test_E4`, and #15 (percentage adjacent to its
  provenance) cites `test_B1`/`test_B3` — all three probes were *deliberately
  flipped* by the fix round, since they double as the probes for L-8, H-4 and
  C-3. The properties still hold (I re-confirmed #15 by CLI, and #14 is covered by
  `tests/test_response_cache.py`), but nobody should read those three rows of the
  Tester's table as still-executing evidence.
- **`_taxes_are_the_whole_carrier_cash_figure` is still a guess.** Unchanged
  since v3 §11.1 and correctly disclosed in both reports. C-2 narrowed it; it is
  still resting on one captured row for one program that happens to levy no YQ.
  The resolution is empirical and cheap: on Step 10, if any row comes back for a
  YQ-levying program, compare `TotalTaxes` against a real booking page and write
  the answer down.
- **The greedy trip-budget ordering is not proven optimal** (savings-per-point,
  disclosed in the docstring and the report). Acceptable — an executable
  approximation beats an inexecutable optimum — but revisit it the first time a
  real wallet produces a visibly wrong demotion.
- **`test_B7b`**: a server returning the same cursor forever still spends 25 calls
  (2.5% of the daily cap) on one leg. Disclosed and left alone. Fine for now;
  it becomes a real cost under v5's live-by-default.
- **`--help` examples still show `--balance-ur 180000`**, the deprecated flag and
  the synthetic balance. Update to `--balance UR=160000`.

---

## Is v4 a sound base for v5?

**Yes, once MR-1 through MR-4 land.** The architecture is right: the seam between
`apply_live` and `evaluate_leg` held under a serious attack, `evaluate_leg` was
genuinely not touched by live mode, and the two-pass trip budget is a clean fix
to a real missing constraint. Nothing in v5's plan requires re-litigating a v1–v3
design decision.

**Must be fixed before building on top:**

| Fix | Why it blocks |
|---|---|
| **MR-3** (zero commits) | `--from-snapshot` replays a *committed* manifest. There is no commit. This is a precondition, not a nice-to-have. |
| **MR-4** (`.env` not ignored) | Do it in the same breath as MR-3, and fold it into v5's `~/.config` key resolution — `config.load_env` is hard-coded to the repo path today. |
| **MR-1** (truncation reported as a finding) | **Live-by-default multiplies exposure to exactly this path.** Today the bug needs an explicit `--live`; after v5 it is the default behaviour of every run. Fix the class before you widen the blast radius. |
| **MR-2** (captured surcharge aborts the trip) | `--new-trip` will *generate* `surcharge_currency`. Shipping a fixture builder that writes a field which crashes the scorer is not acceptable. |

**Can ride along with v5:**

- MR-5 (`cash_component_known` default) — do it early in v5 rather than before
  it; it touches the single-route path v5 is not otherwise changing.
- Multi-currency data, the greedy ordering, the taxes/surcharge empiricism,
  output verbosity, exit-code docs. None of these constrain v5's design.

**One design note for v5's plan.** `--from-snapshot` is the right feature and the
v3 architect and v3 coder both said so (v3 §11.3). But it only removes the "margin
depends on when you ran it" problem if the manifest becomes an **input**, not
documentation. Make the v5 plan state explicitly that a quotable margin is quoted
against a manifest hash, and that a run with `--from-snapshot` prints that hash
next to the percentage — the same "emitted by the same call" discipline v3 used
for provenance, which is the one structural defence in this codebase that has
actually worked.

---

## Which of the Tester's 15 "held up" properties I confirmed

**Independently confirmed — my own code, production data, not the Tester's probes:**

| # | Property | My evidence |
|---|---|---|
| 1, 2 | `LiveLegOutcome` and `Reason` invariants survive `python -O` | Full suite green under `-O` **and** `-OO`: 512 passed, 9 skipped both times |
| 4 | R3 — no split when one currency suffices | `best_plan(need=50,000)` → `{'UR': 50000}`, `currencies_used=1` |
| 5 | `--max-stranded-points 0` rejects a real overshoot | need 57,300: strict → `None`; default → strands 700 |
| 6 | The balance ceiling binds exactly at 160,000 UR | 160,000 → feasible, 0 stranded; 160,001 → `None`; 159,001 → 160,000 spent, 999 stranded |
| 7 | Hyatt Oct-1 cliff keyed on the **transfer** date | Sapphire Preferred, need 45,000: 2026-09-30 → 45,000 UR; 2026-10-01 → 60,000; 2026-10-02 → 60,000 |
| 9 | `UNKNOWN` never renders as `$0.00` | Full CLI run on Trip B — B3's surcharge column reads UNKNOWN throughout, and the totals block carries the "UNKNOWN (NOT $0)" counter row |
| 15 | The percentage never appears without its provenance line | Both CLI runs: badge run prints `margin provenance: badge` inside the same table block; `--require-all-live` run prints `WITHHELD` with the reason, exit 3 |

**Confirmed only by replaying the Tester's probes — not independent:**
#8 (`VERDICT_SENSITIVE` boundaries, `C9` ×5), #11 (malformed mileage, `C2` ×14),
#12 (corrupt cache is a miss, `B6d`), #13 (TTL boundary, `B6c`), #10 (`Raw`
fields unreachable, `C6`). All still pass; I did not re-derive them.

**Could not confirm:** **#3 (R2 — a two-currency split strands one block, not
two).** This is the property the v1 plan called the most likely place for a subtle
regression, and I could not exercise it with production data: `data/ratios.csv`
has no non-UR rows, so `best_plan(UR+MR, need=200,500)` returns `None` for want
of an MR→United partner row, not for any reason to do with R2. The Tester's `D1`
proves it against **synthetic** ratios and still passes. So R2 is proven, but only
against data that does not exist in production — worth knowing before anyone
treats it as field-tested.

**Not checked:** #14 (snapshot dedup by content hash) — its probe was flipped by
the L-8 fix; I confirmed the maintained suite covers it
(`test_an_identical_refetch_adds_a_manifest_row_but_no_second_snapshot`,
`test_a_different_response_adds_a_second_snapshot`) but did not re-derive it.

---

## Did the v4 Coder overreach?

**Three places. All three were right, and all three were disclosed.** This is the
good version of overreach.

**1. H-5's own-date cash baseline — the significant one.** The Tester asked for
one thing: *"`evaluate_leg` selects the cash option whose `date` matches the
candidate's date, or the leg is not scored."* The Coder implemented that and then
added a second gate: a points verdict must beat both the matched date's fare
**and** the leg's own-date fare (`src/optimizer.py:1201-1203`), with
`cash_baseline_usd` kept separately so the trip's all-cash denominator is always
built from own-date fares.

**Right.** The Tester's fix alone would have moved the min-over-window bias from
the points side to the cash side: a Jan-17 award beating a $900 Jan-17 fare would
be recommended even when flying Jan 15 as planned and paying $395 beats both.
That is v3 §4.5's own argument applied one step further, and getting it wrong
would have handed points a free win in exactly the direction this project keeps
erring.

**Disclosed** — the fix report gives it its own section, states plainly "I added
one thing beyond it", and shows both worked cases (H-5a, H-5b). **And verified
harmless**: the badge-mode Trip B headline is unchanged, because every Trip B cash
option is on-date, so the two comparisons collapse to one. No number moved
silently.

**2. L-1's scope expansion.** Fixing the abort exposed a latent hole reachable
only from a code path the CLI could not previously reach: a leg with no priceable
cash rendered `$0.00` and turned the headline into `-inf%`. The Coder fixed both,
added an explicit `Legs EXCLUDED from both totals` row so an excluded leg is never
dropped silently, and said so. Right, and correctly disclosed. **But see MR-2** —
he fixed two of the three call sites and reported it as complete.

**3. Exit code 4.** A new CLI contract, added for C-3, deliberately distinct from
`--require-all-live`'s 3. Correct design. Disclosed in the report — but **not** in
`--help` or the README, which is the one place this overreach was under-disclosed.
Listed above under "should fix soon".

**Not overreach, but worth naming**: 42 new regression tests in
`tests/test_v1_v3_adversarial_fixes.py`, none of which anyone asked for. The
Coder's reasoning is exactly right and is the single best judgement call in the
round: the Tester deliberately put the probes outside `testpaths` so they would
not need maintaining, which means **nothing in the maintained suite would have
pinned any of the 25 fixes** — all of them could have silently regressed with 470
tests green. Building that net was the correct unrequested work.

---

## Agent performance

**Architect** — Strong. The v1 and v3 plans are the best artifacts here: they
name their own three most-likely-wrong decisions, and in both cases those
predictions came true (v1 §10 predicted multi-currency would be dead weight — it
is; v3 §11.3 predicted the TTL was the wrong reproducibility unit — it is, and
`--from-snapshot` is now v5's headline). **To improve**: v3 §8 asserted "exactly
three tests may change" and nine changed, two of them *required* by the plan's own
Step 0 acceptance criteria. Stop predicting exact test-breakage counts; state the
invariant that must hold and let the coder measure. A wrong number in a plan
invites a coder to make the number true.

**Coder (v4 round)** — Excellent, and the honesty is genuinely unusual: two
changed tests justified, one found-and-not-fixed defect flagged rather than
quietly patched, the greedy ordering labelled non-optimal, and an explicit "no
live behaviour is verified" section. Every headline claim I checked was true.
**To improve**: the fix report's framing is "all 25 fixed, none disputed", and
that framing is what let MR-1 and MR-2 through. Both are cases where the *finding*
was fixed and the *class* was not — MR-2 is the same finding at a third call
site, MR-1 is the same mechanism on a different axis. For a project whose defining
failure is a bug recurring inside the code written to prevent it, the last step of
every fix should be: *where else does this shape appear?* — not: *does the probe
flip?*

**Tester** — Very strong. 25 findings, every one with an executed probe and pasted
output, three Criticals that were all real, and a 15-property "held up" section
that is worth as much as the findings. The root-cause note ("three of these are
one bug wearing three hats") is what made the fix round coherent. **Two things to
improve**: first, the `--origin/--destination` single-route path was never
attacked at all, and that is where MR-5 lives; say so explicitly next time rather
than leaving the gap implicit. Second, three of the 15 held-up properties were
evidenced by probes that also served as finding probes (E4, B1, B3), so the fix
round destroyed their evidence — keep held-up evidence in tests that a fix will
not flip.

**Team lead / process** — This is where the failure was. Four versions with no
review pass, and the two things I found in the first twenty minutes — zero
commits, an unignored `.env` holding a live API key — are not deep. They are what a
review catches. The per-version discipline inside this project is genuinely high;
the discipline *around* it was absent.

---

## What's solid

Only things I ran myself.

- **512 tests pass, and they pass under `-O` and `-OO`.** The conversion of the
  honesty invariants from `assert` to `raise ValueError` is real and holds.
- **The 39 flipped probes match the fix report's table exactly.** No probe was
  edited to make a fix look complete.
- **`--require-all-live` genuinely withholds.** Four dead legs, margin
  `WITHHELD`, exit 3, and every leg rendered as an API failure with the explicit
  words *"It is NOT a finding of no availability."* H-4 is properly closed.
- **No API key in any fixture, snapshot or cache file.** I grepped the tree.
- **The stranding and ratio machinery is correct against Tsuki's real balance.**
  Ceiling binds at exactly 160,000; the Hyatt cliff is on the transfer date, on
  both sides and on the exact day; strict no-stranding rejects a real overshoot.
  These I derived independently, not from the Tester's probes.
- **C-3's fix is well built.** Two passes, the second re-running `evaluate_leg`
  against the depleted wallet rather than second-guessing it, an unconstrained
  trip left bit-for-bit unchanged, and a demoted leg that names which legs spent
  the balance first. The demotion message is the best user-facing text in the
  tool.
- **C-2's fix is properly coupled.** `taxes_unconvertible` is set in exactly one
  place from exactly the right condition and checked *before* the modeled table,
  so a program-policy $0 can no longer answer a question about an unpriceable tax.
  That was the reintroduced v0 bug and it is genuinely dead.
