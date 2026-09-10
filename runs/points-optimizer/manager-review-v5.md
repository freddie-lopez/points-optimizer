# Manager review — v5 (replay, `--new-trip`, live-first, APD) and the ten-finding fix round

**Author**: Manager
**Date**: 2026-09-09
**Reviewed**: `docs/plans/v5.md`, `runs/points-optimizer/v5-fix-report.md` (doubling as the
v5 coder report), `docs/test-reports/v5-adversarial.md` and its 98 probes,
`runs/points-optimizer/manager-review-v1-v4.md` (my own), and the tree at `b18ee7a`
(23 commits).
**Everything below marked confirmed was executed by me in this sandbox.** The working tree
is clean and `HEAD` is unchanged at `b18ee7a`; every probe fixture I wrote was removed.

---

## Verdict

**Ship.** The zip goes to Tsuki.

This is the first round in this project where the headline claim — *"fixed as a class, not as
instances"* — is actually true, and I verified the mechanism by breaking it rather than by
reading it. Way nine is genuinely closed. My five prior must-fixes all stayed fixed, including
MR-1, which the Tester correctly found had been silently dead through storage; it is now alive
and I re-derived that with my own repro, not the Coder's test.

But **there is a way ten, I found it in about twenty minutes, and it is the same bug this
project has now shipped eleven times.** It does not fire on Trip B, which is why this is a ship
and not a hold — the numbers Tsuki can quote today are unaffected. It fires the moment he
points `--new-trip` at a UK departure to any of fifteen ordinary countries. He must not use
the tool that way until MR5-1 lands. That is the single most important sentence in this
document.

---

## What I verified myself

Every headline number in the fix report is true.

| Claim | Result |
|---|---|
| `python -m pytest -q` → 785 passed, 13 skipped | **confirmed**, 25.6s |
| `python -O -m pytest -q` → 785 passed, 13 skipped | **confirmed**, 24.8s |
| `pytest docs/test-reports/v5-probes/ -q` → 19 failed / 79 passed | **confirmed** |
| `pytest docs/test-reports/adversarial-probes/ -q` → 40 failed / 38 passed | **confirmed** |
| Exactly one probe moved from the 39/39 baseline, and M-1 is the reason | **confirmed by diff, not by arithmetic** — see below |
| Trip B offline: $3,126.11 cash, $63.89 saving, 2.04%–11.03%, `(badge)` qualifier | **confirmed**, exit 0 |
| One APD line, on B4, at £102 reduced → $138.11 | **confirmed**, exactly one APD line in the whole run |
| The same run without `--offline` → exit 3, margin withheld | **confirmed**, `WITHHELD`, no percentage of any kind printed |
| `--new-trip` in flags mode, and the fixture scores | **confirmed** — builds, refuses nothing it should accept, scores end to end |
| H-2: a `--cabin J` LHR→SFO fixture picks standard | **confirmed** — `GBP 244.00 … per the standard rate` |
| H-4: a built fixture says "NEVER PRICED", not "no partner exists" | **confirmed**, on the offline path |
| No API key in any commit | **confirmed** — see below |

**On the moved probe, I did not take the count on trust.** A count moving from 39 to 40 out
of 78 is consistent with one probe moving *or* with two moving in opposite directions and
hiding each other. I checked out `c2b2b7a`, copied the probe suite in, ran it, and diffed the
two failure lists by name:

```
baseline=39  head=40
newly FAILING at HEAD:  test_B6e_a_cache_file_whose_pages_are_an_empty_list_is_a_HIT
newly PASSING at HEAD:  (none)
```

`B6e` asserts a *defect* — that a zero-page cache file is a HIT — which is exactly what M-1
fixed. The reason is confirmed, and **nothing regressed**: no v1–v3 fix came undone.

**On the API key.** The live key in `.env` is 31 characters. I searched all 23 commits and the
whole working tree for the literal value: **zero hits**. `.env` is untracked and
`git check-ignore -v .env` returns it as ignored on line 5 of `.gitignore`. Every `pro_` and
`SEATS_AERO_KEY=` hit in history is a placeholder (`pro_your_key_here`) or test material
(`pro_TESTKEYMATERIAL`, `user_KEYVALUE_uuu`). Clean.

---

## Did way nine get fixed as a class, or as instances again?

**As a class. This one is real, and it is the best piece of engineering in the project.**

I did not take the mechanism's word for it. I copied `src/` to a scratch directory, added a
tenth field to `LiveLegOutcome` and an invariant in `__post_init__` that reads it, and tried
to import the module:

```
ValueError: way (9): the invariants in LiveLegOutcome.__post_init__ read ['way_ten_probe'],
which no storage classification covers. Every field an invariant depends on must be
recomputed from the bytes, carried by the transport (and therefore persisted by every
storage layer), or explicitly local to one run. An unclassified field is one that answers
differently on the second read - that is way (9), and it is not allowed to be added silently.
```

It refuses to import. It refuses under `python -O` as well, which is the property that matters
and which the Coder correctly used `raise` rather than `assert` to get. `PERSISTED_PROVENANCE_KEYS`
really is derived (`('fetched_at', 'incomplete', 'incomplete_reason')`), and the storage layers
really do loop over it rather than keeping lists of their own.

This is the first time in five rounds that the answer to *"is the class closed?"* has been
demonstrable rather than asserted. Credit where it is due.

**Two holes in the guard, both worth knowing, neither a ship blocker** — see MR5-2. The short
version: the AST reads only `__post_init__`'s **own** source, so an invariant written one
function call away is invisible to it; and `LOCAL_TO_THIS_RUN` is a one-line escape hatch that
silences the error without doing any of the work.

---

## Did my five prior must-fixes stay fixed?

**All five. Yes.** Two of them I re-derived independently rather than trusting a test.

**MR-1 (truncation lost through storage) — fixed, and I proved it myself.** This is the one the
Tester correctly found had been *dead code with a comment claiming otherwise*. The Tester's
exact repro was `hit.meta.get('incomplete') -> False`. Mine, at `b18ee7a`:

```
PERSISTED_PROVENANCE_KEYS : ('fetched_at', 'incomplete', 'incomplete_reason')
hit.meta['incomplete']    : True
hit.meta['incomplete_reason'] : pagination stopped after page 1; hasMore with no c…
ON DISK incomplete        : True
```

It survives the write, it is on disk, and it comes back. MR-1 is alive for the first time.

**MR-2 (captured surcharge in an unpriceable currency aborted the whole trip) — fixed.** I set
B1's candidate to `surcharge_captured: true, JPY 1000`. Previously: `EXIT=1`, whole trip
abandoned, no leg named. Now: exit 0, the leg is named, and the text is the right text —
*"CANNOT BE PRICED … The amount is UNKNOWN - it is NOT $0 and it is NOT being converted at a
rate the tool invented."*

**MR-3 (zero commits) — fixed.** 23 commits, one per fix, separable and legible. The commit
messages are unusually good; each states the finding and the rule rather than the file list.

**MR-4 (`.env` not gitignored) — fixed, and properly.** `.env` is ignored, untracked, and the
`.gitignore` entry carries a four-line comment explaining why it exists. `.env.example` is
tracked and carries a placeholder. This was the highest-consequence item on my last list and
it is closed.

**MR-5 (`Award.cash_component_known` defaulted to `True`) — fixed.** `src/models.py:174` now
reads `cash_component_known: bool = False`, and there is a comment two hundred lines away
citing it as precedent for a later rule. The unsafe default is gone.

---

## Is there a way ten?

**Yes. MR5-1 below.** I found it by asking the question the Coder's own report says should be
asked last — *where else does this shape appear?* — about the axis v5 itself added.

Way nine was: *a fact known at construction is lost by a storage layer.* Way ten is the same
sentence with one word changed: **a fact known at the leg is lost by aggregation.** The leg
render is scrupulously honest. The trip headline — the one number the tool tells Tsuki to
quote — silently drops it, and drops it in the direction that flatters points.

---

## Must fix before v6

### MR5-1. WAY TEN: an UNKNOWN APD scores as $0 in the headline, on both ends of the range — Coder

**Critical by this project's own standard.** This is the v0 bug, restated on the axis v5 added.

`apply_apd` (`src/optimizer.py:1657-1663`): when the duty is owed but its amount is not known,
`apd_added_usd = 0.0` and the leg's four score fields are **not touched at all** — not
`points_score_low_usd`, not `points_score_high_usd`. The unknown enters neither end of the
range whose entire documented job is to bracket unknowns.

**Repro (mine, executed).** Trip B, unchanged except B4's leg cabin set to `X`, which L-1
correctly makes UNKNOWN:

| | Saving | Headline |
|---|---|---|
| B4 cabin known (Y) — as shipped | **$63.89** | **2.04% – 11.03%** |
| B4 cabin UNKNOWN | **$202.00** | **6.46% – 15.45%** |

**Knowing less about the tax makes the recommendation look three times better.** And note what
6.46%–15.45% is: it is byte-identical to the pre-APD v4 headline I confirmed in my last review.
An unknown APD returns the number to the state where APD did not exist.

The leg line is honest and says so explicitly —
*"UNKNOWN IS NOT ZERO: real cash is owed and its size is not known here, so the points-side
total below is a LOWER BOUND."* That honesty reaches no further than the leg. Confirmed:
`grep -i apd src/formatter.py` returns exactly two hits, both the per-leg warning prefix. There
is **no APD row in the totals block, no counter for APD-unknown legs, and no mention of APD in
the headline caveat** — which on that run still reads *"The spread is carrier-imposed
surcharges that are not known,"* a sentence that is now actively false about what the spread
covers.

**How reachable is it?** Reachable on ordinary data, which is what raises this above a curio.
`data/apd_bands.csv` has 63 rows and **15 of them are band `UNKNOWN`**:

```
BR CO CR EC EG IL JO KR LK MV MX PA TR VN ZA
```

Mexico. Brazil. Turkey. South Korea. South Africa. A UK departure to any of these — and,
presumably, to any country not in the table at all — silently scores its departure tax as zero
in the number Tsuki is told to quote. It does **not** fire on Trip B (B4 is LHR→SFO; US is
band B and known), and `--new-trip` defaults the cabin to `Y` so the builder cannot produce an
unknown-cabin leg. That is the only reason this is a ship.

**Ask**: an owed-but-unknown APD must move the pessimistic end of the range, exactly as an
unknown carrier surcharge already does, and must appear as its own counter row in the totals
block — *"Legs where UK APD is OWED but its amount is UNKNOWN (NOT $0)"* — with the leg ids.
The headline caveat sentence must name government departure tax alongside carrier surcharges.
The same mechanism applies one notch down to the `APD_INCLUSION_UNVERIFIED` branch (live and
snapshot legs, and now captured-surcharge legs after H-3): there the amount *is* known and the
exclusion is defensible, but the headline still says nothing about a tax it left out. Fix the
unknown case properly; make the unverified case at least visible at trip level.

And answer the general question, because this is the eleventh instance: **every fact the leg
level writes down as UNKNOWN needs an answer for what the trip level does with it.** Way nine
forced that answer for storage via a classification the module checks at import. Nothing forces
it for aggregation.

### MR5-2. Way nine's guard has two ways round it — Coder

**Medium.** The mechanism works and I confirmed it; these are the edges of what it covers, and
they are worth closing while the machinery is fresh rather than after the next round finds
them.

**(a) An invariant one call away is invisible.** `_fields_read_by_invariants` AST-parses
`inspect.getsource(cls.__post_init__)` — only that function's own body. I added a field plus a
helper method that enforces it, and called the helper from `__post_init__`:

```
IMPORTED FINE - no refusal
INVARIANT_FIELDS has way_ten_probe? False
… ValueError: probe: truncated-by-another-name   # the invariant fires at construction
```

So the invariant is enforced at construction, the field is not classified, not persisted, and
**the module imports happily** — which is way nine's exact shape, reintroduced by a coder doing
something entirely reasonable (extracting a long `__post_init__` into helpers). Given
`__post_init__` is already long enough that extraction is likely, this is a live risk, not a
theoretical one.

**Ask**: walk the call graph one level — resolve `self._foo()` calls in `__post_init__` and
union in their `self.X` reads — or forbid `__post_init__` from calling instance methods and
say so in the docstring. Either is fine; silently covering only the top frame is not.

**(b) `LOCAL_TO_THIS_RUN` is a free escape hatch.** The check forces a *decision*, not a
*fix*, and the cheapest of the three choices requires no other work: adding a field name to
`LOCAL_TO_THIS_RUN` makes the error go away with one line and no storage change. Nothing tests
that the classification chosen was the right one. That is acceptable as long as it is
deliberate — but the docstring should say plainly that `LOCAL_TO_THIS_RUN` is the answer that
needs the most justification, not the least, and a classification change should be a reviewable
event.

### MR5-3. The totals block prints a negative leg count — Coder

**Medium, and embarrassing rather than dangerous.** On the withheld run I ran to confirm exit 3:

```
│ Legs with NO UR path at all                     │  -1 │
```

while the footer of the same run correctly says *"Legs with NO points path at all (no partner
exists): B5, B6, B7."* The table and the footer of one run disagree, and one of them is a
negative count of legs.

**Cause**, confirmed: `src/formatter.py:872-876` prints
`legs_without_points_path - legs_never_priced`. `legs_without_points_path`
(`optimizer.py:2039`) counts legs whose **verdict** is `cash (no points path)`;
`legs_never_priced` (`:2048`) counts legs whose **`points_absence`** is `never_priced`,
regardless of verdict. On the live path `live_trip.py:856` rewrites the verdict to
`cash (no live points data)` and leaves `points_absence` set — so the four flight legs leave
the first set, stay in the second, and 3 − 4 = −1. `main.py:809` builds the footer list with
the correct compound condition; the formatter's subtraction assumes a nesting that the live
path breaks.

This is H-4's fix landing on the offline path and not the live one — the *instance* fixed, the
*shape* left. Same note as always.

**Ask**: compute the count with the same predicate the footer uses
(`verdict == "cash (no points path)" and points_absence != "never_priced"`) rather than by
subtracting two independently-derived counts. A derived count that can go negative is a count
nobody checked.

### MR5-4. The snapshot corpus is empty, and any cache write pollutes it — Coder / Tsuki

**High for quotability; this is the one that decides whether a v5 number can ever be quoted.**

`git ls-files tests/fixtures/seats_aero/` returns four files. `live_trip_b/` — the directory
`--from-snapshot` replays — contains **only `README.md`**. There is **no committed MANIFEST.md
and not one committed snapshot.** The single real response, `sfo_mad_real.json`, sits outside
that directory and is in no manifest.

So v5's headline feature has never replayed a committed manifest, because none exists. Every
manifest in every test, in the Tester's 98 probes and in both reports was synthesised at
runtime by the same code that reads it. The Architect flagged this in §8.2 and the Coder
re-flagged it in his §6.1; both were right, and it is worse than "the corpus is thin" — it is
*empty*, and `test_every_committed_snapshot_parses_into_valid_awards` still passes vacuously,
which is what I said about it in my last review.

**And I found a second half to this by accident.** `ResponseCache.snapshot_dir` defaults to
`DEFAULT_SNAPSHOT_DIR` (`src/response_cache.py:43`) — the committed corpus path — and it is
**independent of the cache directory**. Constructing `ResponseCache(some_tmp_dir)`, which looks
completely isolated, still writes snapshots and appends manifest rows into
`tests/fixtures/seats_aero/live_trip_b/`. My MR-1 cache probe created a `MANIFEST.md` and an
`adhoc_LHR_SFO_…json` in the repo corpus without my asking. I removed both and the tree is
clean, but the point stands: **the corpus that `--from-snapshot` treats as trusted, committed,
hash-verified input can be appended to by any code that constructs a cache**, including a test
run, and nothing guards it.

**Ask**: (1) `snapshot_dir` should default to `None` and archiving should be off unless a
caller explicitly asks for it, so a temp-dir cache is genuinely a temp-dir cache; (2) the day
the first live run happens on Tsuki's Mac, commit the manifest and its snapshots and make
`test_every_committed_snapshot_parses_into_valid_awards` non-vacuous.

---

## Decisions Tsuki needs to make

**1. Trip B's fixture is still incoherent. Fifth version, fifth flag.** Unchanged since my last
review and still printed on every run: B5 and B6 are both booked Jan 15–19 (Madrid and
Amsterdam, the same four nights), B1 is SFO→MAD while the description says MRY→MAD, and flights
are priced for 1 adult while hotels are priced for 2. The plan put a proposed diff in §8.1 and
asked for approval; no approval came, so nothing changed. That is the correct behaviour by the
team and it now blocks the thing you actually want. **Recommendation: approve the §8.1 diff
before the live run.** A live margin over an incoherent itinerary spends real API calls being
precise about the wrong trip.

**2. `--require-all-live` is still satisfied by a `snapshot` provenance of arbitrary age.** The
Architect was 60/40 on this, the Tester found the opposite defect (C-2) and this one held in
every probe, and the Coder left it untouched and said so. It remains a judgement call and it is
yours. **Recommendation: leave it, but rename it.** The flag's name promises "live" and it
accepts a replay from any date; `--require-all-corroborated` would be honest. If you want it to
mean live, it needs a max-age.

**3. The multi-currency engine is still dead weight, and the Hilton hole is now visible.**
`data/ratios.csv` is still 18 UR-only rows. Trip B's B7 — the Hilton London Hyde Park, $680.57,
**the largest single line in the trip** — is reported as "no points path", which is true under
UR-only scope and false about your actual wallet, because you hold an Amex Hilton Surpass.
**Recommendation: populate the MR rows.** This is no longer a tidiness question; it is the
biggest line item in the only trip you have, and the tool is currently telling you to pay cash
for it without ever asking whether your Amex covers it.

---

## Should fix soon

- **The Coder's report is a fix report standing in for a build report.** A rate limit killed the
  original v5 coder report mid-build, and §1 of the fix report reconstructs what Steps 1–8
  shipped *by reading the code back*. That reconstruction is honest and I spot-checked it as
  accurate — but it means no document describes v5 as it was *intended*, only as it *is*, and a
  deviation between plan and build would be invisible in exactly that gap. Two were disclosed
  (Step 6's same-line qualifier and Step 7's J-cabin rate, both unimplemented until the fix
  round found them as C-2 and H-2). I could not rule out a third. **Unverified, by construction.**
- **Exit code 4 is still undocumented.** Flagged in my last review, not addressed. Exit codes 1,
  2, 3 and 4 now all exist and only some are in `--help`. One table, one place.
- **The internal changelog is still leaking into user-facing output.** Also flagged last time,
  also unaddressed — Trip B's B7 and B6 flags still explain what previous versions did and cite
  finding numbers. Tsuki is reading a booking recommendation, not a commit log.
- **`_taxes_are_the_whole_carrier_cash_figure` is still a guess**, still resting on one captured
  row for one program that levies no YQ. Step 8 resolves it empirically and cheaply.
- **The APD approximation is disclosed and correct to leave**: APD is applied *after* the greedy
  trip-balance ordering, so legs are ranked pre-APD. Stated in the docstring. Fine until a real
  wallet shows a visibly wrong demotion — but note MR5-1 makes it worse, since an unknown APD
  is ranked as zero *and* scored as zero.

---

## Agent performance

**Architect** — Strong, and predictive again. §8.2 said the empty corpus was the thing most
likely to hurt and it is (MR5-4); §8.3 correctly designed v5 to be indifferent to the missing
`tier1-output.txt` and that indifference held. §10's three-most-likely-wrong list named
`--require-all-live`, and the Tester found the defect in the opposite direction — worth noting
that naming the right *area* is not the same as naming the right *failure*. **To improve**: the
plan asked Tsuki for approval on the Trip B fixture diff (§8.1) and then let the whole build
proceed without it. When a plan's own acceptance depends on an answer, say what happens if the
answer does not arrive.

**Coder** — The best round in this project. The way-nine fix is the first structural fix here
that survives being attacked rather than being read, and I attacked it. Ten findings, ten
fixes, every one reproduced first, nothing disputed, and the *where else does this shape appear?*
question — which I asked for last time — was actually applied and actually found things (H-3's
captured mandatory fee, H-4's real cause being the verdict rather than the live path). The
disclosure that the C-2 output was captured before M-1 landed and is now unreachable is exactly
the kind of detail most reports quietly drop. **To improve**: the same criticism, one level up.
You asked "where else does this shape appear?" of the *storage* layer and answered it
completely. You did not ask it of the *aggregation* layer, and that is where way ten was
(MR5-1) and where H-4's own fix left a negative number on screen (MR5-3). The leg level in this
tool is now scrupulously honest and the trip level has not been audited once.

**Tester** — Excellent, and the single most valuable finding in five rounds. "Way nine is that
way (6) does not survive way (7) or way (8)" is the correct generalisation, arrived at by
noticing that *my* MR-1 fix was dead code with a comment claiming otherwise — which I did not
catch and should have. Ten findings, all real, all reproduced, severities honest (M-1 correctly
held at Medium because the margin was withheld anyway). The 79 held-up probes are written
against clean inputs specifically so fixes cannot destroy them, which is the fix I asked for
last time, applied. **To improve**: the attack stayed inside the leg. Every finding is about one
leg's outcome, one row, one field; not one probe asks whether the *trip totals* tell the truth
about what the legs said. That is where both of my new findings live, and one of them prints a
negative number in the middle of the summary table on a run you made yourself.

**Team lead / process** — Much improved. The review→fix→re-review loop worked this time: my
five must-fixes were taken seriously, all five landed, and the one I got wrong (MR-1, fixed in
name only) was caught by the Tester rather than shipped. The remaining process gap is that the
same two "should fix soon" items from my last review — exit code 4, changelog in user output —
were carried forward untouched. A "should fix soon" list nobody ever executes is a "won't fix"
list with better manners.

---

## What must happen on Tsuki's Mac before any number from this tool is quotable

Seats.aero is blocked from this sandbox and works on your Mac. **No full live trip run has ever
happened.** In order:

1. **Send `tier1-output.txt`.** Still not received, and it is still gating two unknowns: whether
   the trips endpoint returns **operating metal** (if it does, most live legs stop resolving to
   UNKNOWN surcharges and the margin narrows sharply), and what the **pagination tail** looks
   like. MR-1's motivating payload — `hasMore` with no cursor — has still never been observed.
   The tool is honest about that shape if it occurs; nobody knows if it occurs.
2. **Approve or reject the Trip B fixture diff (§8.1).** Before spending API calls, not after.
3. **Run Step 8, the APD gate.** `--new-trip apd_probe --leg LHR:SFO:<date>:500 --live`, read
   the live row's `TotalTaxes`, open the same route/date/cabin on BA.com, and write down which
   of (a)/(b)/(c) is true with the date and the screenshot. Until that is written down, every
   live UK-departure leg carries a flagged, unadded duty and the margin on those legs is
   ambiguous by design. H-3's fix means the first real capture will no longer land on a $138
   double charge while you decide, which is the right order to do this in.
4. **Run live Trip B, then commit the manifest and its snapshots.** This is MR5-4. Until a
   *committed* manifest exists, `--from-snapshot` is a replay feature with nothing committed to
   replay, `test_every_committed_snapshot_parses_into_valid_awards` passes vacuously, and every
   `mh_` hash quoted so far certifies bytes that the same code wrote and read. **Do this before
   quoting any percentage to anyone.**
5. **Then, and only then, re-verify Step 2.** The Architect's §8.2 mitigation is that the hash
   is over snapshot *content*, not manifest format — so a new manifest column does not
   invalidate an existing quote, but a new *envelope* field does. If the first real response
   shows the envelope needs a field v5 did not put in it, every hash quoted before that day is
   quoted against a format that changed. Check the envelope shape first, on day one.

**And before you run anything**: `chmod 600` your key file if you put one at
`~/.config/points-optimizer/.env`. L-2 added a warning that reports the mode and the exact
command; it reports and does not refuse, so it is on you to act on it.

---

## What's solid

Only things I ran myself.

- **785 tests pass, under `-O` as well as normally.** The `raise`-not-`assert` discipline holds
  across the whole honesty layer, including the new import-time way-nine check.
- **Way nine is closed as a class, and I broke it to prove it.** Adding an unclassified
  invariant field makes `src/models.py` refuse to import, with a message that explains the rule.
  It refuses under `-O` too.
- **Exactly one adversarial probe moved, for exactly the stated reason, and nothing regressed** —
  confirmed by diffing failure lists by name against `c2b2b7a`, not by comparing counts.
- **All five of my prior must-fixes stayed fixed**, and I re-derived MR-1 and MR-2 with my own
  repros rather than the maintained tests. MR-1 in particular is alive for the first time.
- **No API key anywhere in 23 commits or the working tree.** `.env` ignored and untracked.
- **The withhold path is honest.** No transport and no `--offline` gives exit 3, `WITHHELD`,
  a named reason, per-leg `api_error` on all four flight legs, and **no percentage of any kind**
  anywhere in the output. That is the single most important safety property in the tool and it
  works.
- **Trip B's offline number is unchanged to the cent** across the APD wiring and ten fixes:
  $3,126.11 all-cash, $63.89 saving, 2.04%–11.03% carrying its `(badge)` qualifier on the same
  physical line as the number. One APD line, on B4, at the reduced rate, correctly.
- **`--new-trip` is well built.** It refuses what it should, writes nothing when it refuses,
  defaults the cabin to `Y` rather than leaving it blank (which is what keeps MR5-1 off the
  builder path), and the fixture it writes scores end to end with the right words about what it
  does and does not know.
