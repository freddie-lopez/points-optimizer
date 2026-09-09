# Pre-v5 fix report — Manager review MR-1 through MR-5

**Author**: Coder
**Date**: 2026-09-09
**Input**: `runs/points-optimizer/manager-review-v1-v4.md` ("ship with fixes", five must-fixes)
**Result**: all five fixed, plus the three cheap "should fix soon" items and the seed data.
**Commits**: `eacbda7` (v4 baseline) and `8101878` (this round).

---

## Test output, before and after

**Before** (v4 as found, matching the Manager's confirmed baseline):

```
$ python -m pytest -q
================== 512 passed, 9 skipped, 1 warning in 9.95s ===================
```

**After**:

```
$ python -m pytest -q
================== 550 passed, 9 skipped, 1 warning in 9.32s ===================

$ python -O -m pytest -q
================== 550 passed, 9 skipped, 1 warning in 9.39s ===================

$ python -OO -m pytest -q
================== 549 passed, 10 skipped, 1 warning in 9.20s ==================
```

The single `-OO` skip is `test_MR1_the_exhaustive_list_is_written_down`, which
asserts on a docstring; `-OO` strips docstrings, so the test skips itself rather
than asserting against the signature string `@dataclass` synthesises in their
place. Every honesty invariant is a `raise`, not an `assert`, and all of them
still hold under `-O` and `-OO`.

**Adversarial probes, unchanged from the v4 baseline:**

```
$ python -m pytest docs/test-reports/adversarial-probes/ -q
=================== 39 failed, 39 passed, 1 warning in 2.21s ===================
```

Same 39/39 split the Manager confirmed. No probe was edited and none regressed.

**Trip B is bit-for-bit unchanged:**

```
$ python -m src.main --trip-fixture trip_b_europe.json --balance UR=160000 \
    --card "Chase Sapphire Preferred" --transfer-date 2026-09-15
  low end = what is actually defensible                        6.46%
  high end = only if every unknown surcharge is $0            15.45%
  Pay cash for everything                                  $3,126.11
  Points spent                                                28,000
EXIT=0

$ python -m src.main --trip-fixture trip_b_europe.json --live --require-all-live ...
EXIT=3   (margin WITHHELD, 12 "THIS IS AN API FAILURE" renderings)
```

38 net new tests: 20 in `tests/test_pre_v5_manager_fixes.py`, 17 in
`tests/test_apd.py`, 1 in `tests/test_surcharge.py`.

---

## MR-1 — the failure-as-finding class on the pagination axis (Critical)

### The repro, before

```
payload: {"data": [], "hasMore": true}     # truncated
  state           : LiveQueryState.NO_AWARD_SPACE
  incomplete fld  : <<NO FIELD>>
  render          : Seats.aero returned NO award space for SFO->MAD on 2027-01-15
                    (searched SFO->MAD on 2027-01-15, 0 rows). THIS IS A FINDING:
                    the API answered, every row it sent was READ SUCCESSFULLY, and
                    the answer is that there is no award to buy on this date.

payload: {"data": []}                      # genuinely complete and empty
  render          : ...byte-identical to the above...
```

### After

```
payload: {"data": [], "hasMore": true}
  state           : LiveQueryState.ANSWERED_INCOMPLETE
  incomplete fld  : True
  render          : Seats.aero ANSWERED for SFO->MAD on 2027-01-15 and we READ every
                    row it sent (0 rows), and found no award - but the result set was
                    TRUNCATED and we never saw the rest of it. THIS IS NOT A FINDING:
                    an empty page of an incomplete answer says NOTHING about whether
                    award space exists on this date. It is a PAGINATION failure on our
                    side, reported as ours, not as the calendar's. COVERAGE IS
                    INCOMPLETE: pagination stopped after page 1 because the response
                    says there are more results (hasMore) but carries no cursor and no
                    usable offset, so there is no way to ask for the rest. Everything
                    above is true ONLY of the part of the result set we actually saw...

payload: {"data": []}
  state           : LiveQueryState.NO_AWARD_SPACE
  incomplete fld  : False
  render          : ...unchanged. Still "THIS IS A FINDING".
```

### What was fixed, as a class

| Change | Where |
|---|---|
| `result_incomplete` + `incomplete_reason` as **fields** | `models.LiveLegOutcome` |
| `SeatsClient.last_incomplete` / `last_incomplete_reason` | `seats_client.__init__`, `search` |
| `RawSearchResult.incomplete_reason` (machine-readable, not prose) | `seats_client` |
| `query_leg` reads the flag — **the wire that was missing** | `live_trip:196-315` |
| Seventh state `ANSWERED_INCOMPLETE`, outside the finding whitelist | `models.LiveQueryState` |
| `__post_init__` **raises** on `NO_AWARD_SPACE` + truncation | `models` |
| `_truncation_clause()` appended by `render()` itself | `models` |

Two structural points worth naming:

1. **`render()` no longer lets a branch forget a clause.** The state prose moved
   to `_render_state()`, and `render()` is now
   `_render_state() + _skipped_clause() + _truncation_clause()`. The previous
   fix left the INCOMPLETE marker on `pagination_note`, which
   `NO_AWARD_SPACE.render()` never read — which is verbatim the Tester's C-1
   complaint. A branch cannot omit a clause it does not write.

2. **The flag now survives both caches.** The disk-cache replay in `search_raw`
   built `RawSearchResult` with `incomplete` defaulting to `False`, so the
   *second* run of a truncated query lost the coverage warning entirely and
   reported a clean finding. `incomplete`/`incomplete_reason` are persisted in
   cache meta and restored on both the disk path and the in-process path.

### The exhaustive list of ways this tool can fail to know

Written into `LiveLegOutcome`'s docstring (`src/models.py`), pinned by
`test_MR1_the_exhaustive_list_is_written_down`. Five axes, seven entries, each a
field with an invariant:

**AXIS 1 — did we ask?**
1. **We never asked at all** — hotel leg, missing IATA codes, `--live` off, no
   client. → `NOT_QUERIED`, field `note`.
   *New invariant*: `NOT_QUERIED` with an empty note raises. `render()` used to
   supply a plausible **default** reason that may be false.

**AXIS 2 — did we get an answer?**
2. **Transport failed** — DNS, TLS, timeout, 4xx/5xx, unparseable envelope. →
   `API_ERROR`, field `error` (required; awards forbidden).
3. **We never sent the request** — our own daily cap was spent. →
   `BUDGET_EXHAUSTED`, field `error`, plus `result_incomplete` when the cap hit
   mid-pagination. Never cached, so never replayed as an observation (H-1).

**AXIS 3 — could we read the answer?**
4. **The parser could not read the rows** — bad date format, non-object row,
   unreadable price, wrong-shaped envelope. → `ANSWERED_UNREADABLE`, fields
   `rows_unreadable` / `unreadable_reasons`. `NO_AWARD_SPACE` + any unreadable
   row raises (C-1).
5. **Two documented row containers disagree** — `{"data": [], "results": [...]}`.
   → routed to `ANSWERED_UNREADABLE` by `envelope_shape_error`. **This is the
   fifth way, found while writing this list. See below.**

**AXIS 4 — did we see all of the answer?**
6. **The result set was truncated** — `hasMore` with no cursor, a non-advancing
   offset, the 25-page cap, or the budget breaking mid-pagination. → field
   `result_incomplete` + `incomplete_reason`; state `ANSWERED_INCOMPLETE` when it
   leaves zero awards. **This is MR-1.** A truncation with no stated reason also
   raises.

**AXIS 5 — is the answer still true?**
7. **The bytes are a replay of an older fetch** — fields `served_from_cache`,
   `cache_fetched_at`.
   *New invariant*: `served_from_cache` with no `cache_fetched_at` raises.
   `render()` would otherwise print the literal string `fetched None`.

The docstring ends with **HOW TO EXTEND THIS**: an eighth way gets a field, a
`raise`, an unconditional render clause, and a numbered entry — not a new branch
inside a renderer.

### The fifth way, found and fixed

```
payload: {"data": [], "results": [ ...20 real, bookable rows... ]}

  BEFORE: envelope_shape_error -> ''      (readable)
          rows_seen            -> 0
          rows_unreadable      -> 0
          awards built         -> 0
          => NO_AWARD_SPACE, "there is no award to buy on this date"
     (sanity: the same 20 rows under 'results' ALONE build 20 awards)

  AFTER:  envelope_shape_error -> "the response carried BOTH a 'data' list (0 rows)
                                   and a 'results' list (20 rows) and they are not
                                   the same rows. There is no way to tell which one
                                   is the answer, so neither is read..."
          rows_unreadable      -> 1
          => ANSWERED_UNREADABLE (the existing C-1 invariant then applies)
```

`_rows_of` prefers `data` and returns it whenever it is a list — including an
empty one — silently discarding a populated `results`. Choosing one of two
documented containers that disagree is a guess, and a guess is not knowledge.
Agreement is still readable: `{"data": [], "results": []}` is unaffected.

---

## MR-2 — L-1 at its third call site (High)

**Before** — Trip B with `surcharge_captured: true, surcharge_currency: "JPY"`:

```
Error: No FX rate configured for 'JPY'. Add it to config.FX_RATES_TO_USD.
EXIT=1
```

**After** — same fixture, exit 0, the run continues, the candidate is named:

```
EXIT=0
candidate       : British Airways SFO-MAD via LHR (badge 22.5k pts)
captured        : JPY 15000.0
is_known        : False
confidence      : unknown
message         : The captured surcharge for 'British Airways SFO-MAD via LHR
                  (badge 22.5k pts)' CANNOT BE PRICED. It was captured as JPY
                  15,000.00 and No FX rate configured for 'JPY'... The amount is
                  UNKNOWN - it is NOT $0 and it is NOT being converted at a rate
                  the tool invented. Supply one with --fx JPY=<rate> to score this
                  option. This leg's other options and the rest of the trip are
                  unaffected.
```

And with the JPY figure on the **winning** candidate, the leg degrades honestly
rather than silently — it falls back to the next option and says why:

```
B1 - MRY->MAD, Jan 15 2027, one-way, economy, 1 adult
  Verdict: CASH (SURCHARGE UNKNOWN) - A points path exists (British Airways
  Executive Club, 23,000 points) but the carrier-imposed surcharge on this metal
  is UNKNOWN, so it cannot be scored against $395.00 cash.
EXIT=0
```

Routed through the same shape `cash_options` uses (L-1) and `taxes_unconvertible`
uses (C-2): an unpriceable cash figure poisons **that option's** cash side and
nothing else.

---

## MR-3 + MR-4 — version control and the API key (High)

`.gitignore` was edited **first**, before any commit existed. It now covers
`.env`, `.venv/`, `__pycache__/`, `*.pyc`, `.pytest_cache/`, `tier1-output.txt`.

```
$ git check-ignore -v .env .venv/ src/__pycache__/ .pytest_cache tier1-output.txt
.gitignore:5:.env               .env
.gitignore:11:.venv/            .venv/
.gitignore:12:__pycache__/      src/__pycache__/
.gitignore:14:.pytest_cache/    .pytest_cache
.gitignore:17:tier1-output.txt  tier1-output.txt
```

Identity set locally, so the commits are attributed to Tsuki:

```
$ git config user.name "Tsuki" ; git config user.email "lionlord2330@gmail.com"
```

### The key is not in the history — proof

Run across **every blob in every commit**, not just the index:

```
$ KEY=$(grep '^SEATS_AERO_KEY=' .env | cut -d= -f2-)
$ echo "${#KEY} chars, prefix ${KEY:0:4}"
31 chars, prefix pro_

# A. the live key VALUE, every commit
$ git rev-list --all | while read c; do git grep -I -n -F -e "$KEY" $c --; done
(no output — zero hits)

# B. the string SEATS_AERO_KEY=pro_, every commit
$ git rev-list --all | while read c; do git grep -I -n 'SEATS_AERO_KEY=pro_' $c --; done
8101878:.env.example:2:SEATS_AERO_KEY=pro_your_key_here
eacbda7:.env.example:2:SEATS_AERO_KEY=pro_your_key_here

# C. was .env ever tracked?
$ git log --all --name-only --pretty=format: -- .env | grep -c .
0
```

**B's two hits are `.env.example`'s placeholder `pro_your_key_here`, not a key.**
That file is the template and is meant to be tracked. The real 31-character
value appears nowhere in the history, and `.env` has never been tracked in any
commit.

### On "one commit per version"

The Manager asked for one commit per version so the four rounds are separable.
**That history does not exist and I did not fabricate it.** There is no record of
which files v1 touched versus v2, v3 or v4, and manufacturing four commits from a
single working tree would be inventing provenance in the one project that cannot
afford it. The commit body says so in those words.

I did make **two** commits rather than one, and this is a deliberate deviation
from the brief:

- `eacbda7` **v4 baseline: v0–v4 as shipped, first commit** — made *before* I
  touched any source file, so a commit labelled "as shipped" contains what
  actually shipped. Its only deviation is `.gitignore`, which had to land first
  or the commit would have contained the key; the body says that too.
- `8101878` **pre-v5 fix round** — everything in this report.

The alternative was one commit whose message claims "v0–v4 as shipped" over a
tree containing this fix round, which would have been false. Separability now
starts at `eacbda7`, and `git diff eacbda7 8101878` is exactly this round.

---

## MR-5 — `cash_component_known` defaulted to `True` (Must-fix)

Flipped to `False` in `models.Award`. Two further copies of the same unsafe
default, both flipped:

- `Strategy.cash_cost_known` — same flag one layer up, same `True` default.
- `getattr(award, "cash_component_known", True)` in `optimizer.py` — a second
  copy of the default that **would have survived flipping the field**. An object
  that cannot say whether it knows does not know.

```
Award(cash_component=0.0, source="google_badge_unverified")   # no tax data
  cash_component_known = False        (was True)
```

The only production `Award` constructor is the Seats.aero parser, which already
passed the flag explicitly — so **no production number moved and no existing test
changed.** That is itself the Manager's point: the exposure was on the
`--origin/--destination` single-route path *and on every future construction
site*, including v5's `--new-trip`. The default is what protects those.

Four new tests pin it, including one end-to-end through `_html_cash_cell` that
asserts the export renders `UNKNOWN` and **not** `$0.00</td>`.

---

## Cheap fixes (Manager's "should fix soon")

- **Exit codes 0 / 1 / 3 / 4 documented in one place**, in the `--help` epilog
  and quoted in `README.md`, each saying what it means and why 3 and 4 are
  distinct. The README names `--help` as authoritative if the two ever disagree.
  Verified: `--help` prints the table; `--require-all-live` still exits 3.
- **`--help` and README examples** now use `--balance UR=160000`, not the
  deprecated `--balance-ur 180000` with its synthetic balance.
- **Internal changelog stripped from user-facing output** — three places:
  B7's flag no longer explains what it said before v4 or cites "finding L-7";
  B6's city-tax flag no longer explains what v0 did; and `optimizer`'s
  unscoreable-surcharge verdict now reads "It is NOT $0, and it must not be
  treated as $0" instead of citing v0. Current fact in the output, history here.
- **README's live-state table** was missing `answered_unreadable` entirely; it now
  lists all seven states and points at the exhaustive list in `models.py`.

---

## Seed data

### `data/apd.csv` — new, separate, NOT wired in

16 rows: 4 bands x 2 cabin classes (HMRC's own `reduced`/`standard`) x both
published rate periods (2026-04-01..2027-03-31 and 2027-04-01 onward), keyed on
`(departure_country, band, cabin_class, effective_from, effective_to)`, every row
carrying the GOV.UK URL and a verification date.

`src/apd.py` loads and validates it. `tests/test_apd.py` (17 tests) covers the
loader, both rate periods, the 2027-04-01 boundary, and the validator's refusals
(negative rate, no source, unknown cabin class, backwards window, overlapping
windows, missing file).

Three deliberate design choices:

- **`lookup()` returns `None`, never `0.0`.** A departure this table does not
  cover is a departure whose tax is UNKNOWN. Returning zero would be a claim
  about the world made from an absence of data.
- **Its own file, its own loader, nothing shared with `SurchargeTable`.** A
  government departure tax and a carrier YQ are different quantities: United
  charges no YQ and that does not exempt anyone from APD. Merging them is how
  "United charges no surcharge" becomes "this leg costs nothing in cash".
- **`test_apd_does_not_yet_affect_any_score` asserts `optimizer` does not import
  `src.apd`.** Wiring it cuts Trip B's reported $202 saving on LHR->SFO to about
  $64, and **nobody has checked whether Seats.aero's `TotalTaxes` already
  includes APD** — there has never been a live LHR-departure row. That test is
  what v5 deletes on purpose, in the same commit as the plan.

### `data/surcharges.csv` — 5 new sourced rows, 4 provenance upgrades

**New rows, all previously UNKNOWN** (so they add information without moving an
existing answer):

| Program | Metal | Cabin | Dep | Point | Basis |
|---|---|---|---|---|---|
| Club Iberia Plus | BA | J | US | $729 | one_way |
| BA Executive Club | BA | J | US | $748 | one_way |
| Club Iberia Plus | IB | Y | * | $151 | round_trip |
| BA Executive Club | IB | Y | * | $494 | round_trip |
| BA Executive Club | IB | J | * | $1,494 | round_trip |

**Provenance-only upgrades — `modeled` → `sourced` with a URL and a date, with
not a single figure changed**: Iberia Plus IB/J, Flying Blue AF/J, Flying Blue
KL/J, Virgin VS/J. The research confirms the v1 architect's point values; one
article does not justify narrowing his bands.

`confidence` gained a third tier, `sourced`, sitting between `captured` (read off
a booking page for *this* itinerary) and `modeled` (reasoned). It behaves exactly
like `modeled` — it never wins the captured-beats-table precedence — and prints
as itself so the output never calls one the other.

**The GAPS stay UNKNOWN**, as instructed: BA metal economy ex-UK, BA metal
economy ex-US, Iberia Plus on EI metal, AerClub on BA metal.

**Two sourced figures were deliberately NOT seeded** — see below.

---

## Two latent bugs found while seeding, both dormant until these rows

### 1. `surcharges.csv` has a `currency` column and the scorer ignores it

`optimizer` does `score = plan.score_usd + surcharge.amount_point + ...` and
assigns `points_surcharge_usd = surcharge.amount_point`. **No conversion.** Every
row happened to be USD, so the bug never fired.

Two of the sourced figures are quoted in GBP — Iberia Plus MAD→NYC one-way J at
~£115, and BA Club LHR↔NYC round-trip J at £850. Seeding them would have scored
£850 as $850, understating it by roughly $300, in the direction that makes points
look cheaper. That is this project's signature bug wearing a unit label.

`SurchargeTable.validate` now **refuses a non-USD row** with a message naming the
cause. Those two figures are therefore **not seeded and remain UNKNOWN**. Lifting
the check is not the fix; teaching `optimizer` to convert is, and the comment says
to delete the check along with it.

### 2. Basis conversion was one-directional

`resolve` halved a `round_trip` row for a one-way leg and disclosed it. It did
**not** double a `one_way` row for a round-trip leg — it returned the one-way
number and the scorer added it to a round-trip total. Every row was `round_trip`
before this round, so it was dormant; these are the table's first `one_way` rows,
and they would have understated the points side by about half. Conversion is now
symmetric, with the same directional caveat in the notes.

### A third thing, reported not fixed

**The ex-GB BA row is now the least-well-evidenced row in the table and is
probably an underestimate.** It is still the v1 architect's modeled
750/900/1100 USD, while the dep=US row beside it is now sourced. Round-trip, that
puts ex-GB at $900 against a US departure at $1,496 — so the row whose own note
says "WORST CASE IN THE MODEL" is no longer the worst case. Its sourced
replacement is the £850 figure (~$1,150, and it *does* sit above the US number),
which bug 1 blocks. This is written into
`test_ex_gb_row_beats_the_generic_ba_row`, whose magnitude assertion I removed
and replaced with the specificity property it actually exists to defend, plus an
assertion that fails if anyone marks the ex-GB row `sourced` without re-deriving
the relationship.

---

## Tests I changed, and the question each one asked

Six existing tests changed. None was edited to make a fix look complete; each is
a real question the new data or the new invariants asked for the first time.

| Test | Question | Answer |
|---|---|---|
| `test_production_table_validates` | Frozen "today" was 2026-09-08; rows verified 2026-09-09 are legitimately "in the future" to it. | Moved the frozen date to 2026-09-09 — the day the newest row was verified. Still not `date.today()`, so the suite stays deterministic. |
| *(new)* `test_no_row_claims_to_have_been_verified_in_the_future` | Pinning that date forward can no longer catch a genuinely fabricated future date. | Added a test that checks every row against **real** today. This is why moving the pin is safe. |
| `test_M2_..._validated_by_default_table` | Same frozen date. | Same move, comment cross-references the above. |
| `test_ranges_are_preserved_not_collapsed_to_a_midpoint` | A US departure now matches a more specific sourced row (508/748/1050, not 600/800/1000). | Updated, and strengthened: it now also asserts the point value is **not** the midpoint, which is the property the test is named for. |
| `test_ex_gb_row_beats_the_generic_ba_row` | Is ex-GB still the worst case? **No** — see above. | Magnitude assertion removed and documented; specificity property (which is what the test defends) kept and strengthened. |
| `test_iberia_is_a_much_lower_band_on_the_same_avios` | `ba - ib >= 600` was comparing a **one-way** row to a **round-trip** row. | Both sides resolved to the same basis before subtracting, plus a ratio assertion (>= 5x), which is the sentence the tool actually wants to say. |

---

## What is NOT verified

**No live behaviour was verified.** There is no network egress in this sandbox
and no Seats.aero call was made. Every live-mode claim in this report — MR-1's
repro included — rests on fixtures and mocked transports. The nine skipped tests
are still the Step 10 live gate and it has still never run.

Specifically unverified:

- Whether Seats.aero's `TotalTaxes` already includes UK APD. This is the question
  that decides how v5 wires `data/apd.csv`, and it needs one live LHR-departure
  row compared against a real booking page.
- `_taxes_are_the_whole_carrier_cash_figure` is unchanged and still a guess.
- The `hasMore`-with-no-cursor payload that motivates MR-1 is a **hypothesis**
  about Seats.aero's pagination, not an observation. Pagination has never been
  seen — Tsuki's one capture was truncated at 2000 characters. MR-1 makes the
  tool honest *if* that shape occurs; it does not establish that it does.
- The 14 sourced surcharge figures are each one article on one date. Bands are
  wide on purpose.

## Still open, for the Manager and Tsuki

- The three **decisions** in the review (Trip B's incoherent fixture, whether v5
  starts before Step 10, the multi-currency engine) are untouched — they are
  Tsuki's calls, not mine.
- The two latent bugs above are fixed or fenced, but the **currency fence is a
  refusal, not a capability**. Two sourced figures are sitting unusable behind
  it. Teaching `optimizer` to convert a surcharge estimate is a small, contained
  piece of v5 work and it unblocks them.
- `data/ratios.csv` still has 18 rows and all 18 are UR. Unchanged.
