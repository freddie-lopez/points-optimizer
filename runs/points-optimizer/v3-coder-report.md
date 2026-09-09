# v3 Live Trip Mode — coder report

**Date:** 2026-09-08
**Plan:** `docs/plans/v3.md`
**Baseline:** 395 tests passing (v1 + the Seats.aero parser fix).
**Now:** **470 passed, 9 skipped** — the 9 skips are the Step 10 gate, which
cannot run without network egress.

```
$ python -m pytest -q
================== 470 passed, 9 skipped, 1 warning in 6.75s ===================
```

## Read this first

**No live call was made. This sandbox has no network egress and none was
attempted.** Every "live" behaviour below was built and tested against stubs and
against the one recorded response this project owns
(`tests/fixtures/seats_aero/sfo_mad_real.json`). `tests/fixtures/seats_aero/live_trip_b/`
contains **no responses**; Step 10 is built, gated and skipping.

Steps 0–9 are complete. Step 10 is **built and left gated** for Tsuki's Mac.
Step 11 (the trips-endpoint probe) was **not probed** — no egress; it is
explicitly non-blocking.

---

## The headline FX effect, stated before anything else

The plan's Step 0 changed GBP from an unsourced **1.2700** to a sourced
**1.3540**, and added **CAD 0.7256**. Trip B's badge-only margin moves:

| | v2 | v3 (badge-only, no `--live`) |
|---|---:|---:|
| Pay cash for everything | $3,083.89 | **$3,126.11** |
| Optimizer's recommendation | $2,881.89 | $2,924.11 |
| Dollar saving (point) | $202.00 | **$202.00 — unchanged** |
| Beats cash by | 6.55% – 15.66% | **6.46% – 15.45%** |

```
$ python -m src.main --trip-fixture trip_b_europe.json \
    --balance UR=180000 --card "Chase Sapphire Preferred" --transfer-date 2026-09-15
│ Pay cash for everything                            │        $3,126.11 │
│ Optimizer beats paying cash by                     │   6.46% - 15.45% │
│   low end = what is actually defensible            │            6.46% │
│   high end = only if every unknown surcharge is $0 │           15.45% │
│   margin provenance                                │            badge │
```

**This is an FX effect and not a live-data effect.** The GBP correction adds
**$42.22** to the Hilton London leg ($638.35 → $680.57). That leg has no points
path at all under a UR-only wallet — Hilton Honors is an Amex partner — so the
$42.22 lands **only in the all-cash denominator**. The numerator, the dollars the
optimizer saves, is unchanged. A bigger denominator over the same saving is a
smaller percentage. **The range did not narrow because anything was confirmed; it
shrank because the trip was always $42 more expensive than the tool said.**

The CAD rate has a second, larger consequence: the captured CAD 44.60 tax on the
real SFO→MAD Aeroplan award now converts to **$32.36** instead of being carried
as an unconvertible unknown. That leg goes from "verdict robust from one
direction only" to a fully priced **$532.36 vs $395 → PAY CASH**. The parser-fix
report's open item for the architect — *"CAD needs a sourced FX rate"* — is
closed.

---

## The plan got §8 wrong: nine tests changed, not three

The plan names exactly three tests that Step 0 may break. **Step 0 broke nine.**
I implemented Step 0 first and measured rather than guessing:

```
$ python -m pytest -q      # immediately after the FX change, before any test edits
FAILED tests/test_acceptance.py::test_trip_b_headline_is_a_range_not_the_bare_16_percent
FAILED tests/test_acceptance.py::test_trip_b_london_hotel_is_marked_as_resting_on_an_unverified_rate
FAILED tests/test_cash_fallback.py::test_foreign_currency_converted_via_config
FAILED tests/test_fx_and_flagship.py::test_gbp_is_a_placeholder_and_usd_and_eur_are_not
FAILED tests/test_fx_and_flagship.py::test_the_marker_appears_in_the_fx_banner
FAILED tests/test_fx_and_flagship.py::test_a_gbp_leg_is_marked_as_resting_on_an_unverified_rate
FAILED tests/test_seats_client.py::test_an_unsupported_currency_degrades_to_unknown_never_to_zero
FAILED tests/test_seats_client.py::test_a_placeholder_fx_rate_is_disclosed_on_the_converted_figure
FAILED tests/test_seats_client.py::test_the_optimizer_marks_an_unconvertible_tax_as_a_lower_bound
=================== 9 failed, 386 passed, 1 warning in 6.21s ===================
```

Every one is a **mechanical consequence of the FX table change**, and none is a
v3 bug. Two of the six unlisted ones are *required* by Step 0's own acceptance
criteria, which contradict §8's list.

| # | Test | In §8? | Why it changed |
|---|---|---|---|
| 1 | `test_gbp_is_a_placeholder…` → `test_the_provenance_tiers_are_distinguishable` | ✅ item 1 | GBP is `sourced`, not a placeholder. Re-aimed; the real subject (tiers are machine-distinguishable) is preserved and extended to CAD and EUR. |
| 2 | `test_foreign_currency_converted_via_config` | ✅ item 2 | `convert_to_usd(502.64,"GBP")` → 680.57. **The $42.22 correction.** EUR assertion untouched. |
| 3 | `test_the_marker_appears_in_the_fx_banner` | ✅ item 3 | The `--fx GBP=1.29` example became `1.36`, and `UNVERIFIED RATE` no longer appears because nothing in the default table is unsourced. Re-aimed onto `CONFIRM BEFORE TRUSTING` + source date + age. |
| 4 | `test_a_gbp_leg_is_marked_as_resting_on_an_unverified_rate` | ❌ | `rests_on_placeholder_fx` is set inside `evaluate_leg` from `is_placeholder_rate`, now False for GBP. **`evaluate_leg` was not touched.** The claim "rests on a rate with NO SOURCE" is simply no longer true; asserting it would assert a falsehood. |
| 5 | `test_trip_b_london_hotel_is_marked_as_resting_on_an_unverified_rate` | ❌ | Same cause, acceptance-test copy. |
| 6 | `test_trip_b_headline_is_a_range_not_the_bare_16_percent` | ❌ | The GBP denominator moved: 6.55/15.66 → 6.46/15.45. The new test also pins `all_cash_usd == 3126.11` **and** that the dollar saving is still exactly $202, so an FX effect can never again be mistaken for a data effect. |
| 7 | `test_an_unsupported_currency_degrades_to_unknown_never_to_zero` | ❌ | **Required by Step 0's acceptance**: "`Award.cash_component_known` is True for it". CAD is no longer unsupported. |
| 8 | `test_a_placeholder_fx_rate_is_disclosed_on_the_converted_figure` | ❌ | GBP-denominated taxes no longer print "PLACEHOLDER". |
| 9 | `test_the_optimizer_marks_an_unconvertible_tax_as_a_lower_bound` | ❌ | **Required by Step 0's acceptance** (§4.6's closing paragraph): the leg becomes "a fully priced $532.36-vs-$395 PAY CASH". |

**Nothing was weakened.** Where a re-aimed test stopped covering a rule, I
re-pinned that rule somewhere it cannot go stale:

- `test_a_genuinely_unsupported_currency_still_degrades_to_unknown_never_to_zero`
  — the unknown-not-zero rule, on **JPY**, which nobody will ever source.
- `test_an_award_with_no_tax_figure_is_still_a_lower_bound` — the lower-bound
  machinery, on a response carrying no tax figure at all.
- `test_a_true_placeholder_rate_is_still_disclosed_as_a_placeholder` — the
  placeholder path, on a synthetic currency.
- `test_a_sourced_rate_goes_stale_and_says_so` — new: 40 days on, GBP is STALE
  and so is any total that used it.

**Everything else in the suite stayed green at every step.** Exactly one further
test went red later, and it was a genuine v3 bug of mine, fixed rather than
re-aimed: `test_the_recorded_response_carries_no_api_key` used `iterdir()` and
crashed on the new `live_trip_b/` subdirectory. It now uses `rglob()`, so the
key check actually covers the corpus that will hold real responses.

---

## Steps

### Step 0 — FX: sourced rates, dated, with staleness ✅

`src/config.py`, `src/formatter.py`, four test files.

New `sourced` tier between `placeholder` and `user_supplied`; `FX_SOURCED_ON`;
`FX_STALE_AFTER_DAYS = 30`; `needs_confirmation()`; `is_stale_rate()`.
`is_placeholder_rate()` returns False for `sourced` — but the confirm marker
stays. EUR keeps its own `booking_site_implied` tier so its string keeps saying
it is back-derived from one booking page, not a market rate.

Acceptance, all verified by test:
`convert_to_usd(44.60,"CAD") == 32.36`; `convert_to_usd(502.64,"GBP") == 680.57`;
banner shows provenance + source date + age; clock frozen 40 days on produces
`STALE RATE` on GBP **and** on the trip totals; `--fx` still clears everything and
records `user_supplied`; CAD converts and `cash_component_known` is True.

**One addition beyond the plan's file list.** `convert_taxes()` in
`seats_client.py` already disclosed a *placeholder* rate on the converted tax
figure itself. With GBP/CAD no longer placeholders that disclosure would have
silently vanished, printing a foreign tax as a clean number — strictly worse than
v2. I added a `needs_confirmation` branch to the note builder. **No parsing
logic, no values, no control flow changed**; it appends to a string.

### Step 1 — Transport/parse split + raw-response disk cache ✅

`src/seats_client.py`, `src/response_cache.py` (new), `src/config.py`,
`tests/test_response_cache.py` (new, 24 tests).

`search_raw()` returns verbatim pages; `parse_pages()` is the old loop body
lifted out unchanged; `search()` is now exactly `parse_pages(search_raw(...))`.
**The parser body did not move** — the proof is that all 54 parser tests passed
untouched immediately after the split, and
`test_the_real_capture_round_trips_through_the_cache_envelope_unchanged` parses
the committed capture directly and again through the cache envelope and asserts
the Awards are equal object-for-object.

The disk cache **defaults to OFF** (`cache=None`), so every pre-existing caller
and test is byte-for-byte unaffected; only live mode supplies one. The in-memory
`SeatsClient.CACHE` and `clear_cache()` are untouched and their four tests pass
as written.

Acceptance verified: two identical `search_raw` calls → **one** HTTP call, second
reports `served_from_cache=True` with the **original** `fetched_at`; `--refresh`
re-fetches and overwrites; an entry past TTL is a miss **and the file still
exists**; every request parameter and `cache_schema` changes the key; no cache or
snapshot file contains key material.

### Step 2 — Snapshot archival and the manifest ✅

Every `put()` writes the cache entry, the snapshot and a manifest row as **one
event** — there is no opt-in flag to forget. Snapshots are deduplicated by
content hash; a byte-identical re-fetch adds a manifest row but no second file; a
changed response adds a second file.

`test_every_committed_snapshot_parses_into_valid_awards` makes the corpus
executable rather than decorative. It passes vacuously today because the corpus
is empty, and it says so in its own docstring — an empty corpus is the honest
state, not something to paper over.

### Step 3 — Typed per-leg outcomes ✅

`src/models.py`, `src/live_trip.py` (new), `tests/test_live_trip.py` (new).

`PointsProvenance`, `LiveQueryState`, `LiveQuerySpec`, `LiveLegOutcome`,
`FlexibleFinding`. `query_leg()` catches `SeatsAeroError`, every `requests`
exception **and bare `Exception`**, and never raises.

All acceptance criteria pass, including `LiveLegOutcome(state=NO_AWARD_SPACE,
error="timeout")` raising, and four legs with the second raising producing four
outcomes with no escape.

**Deviation, deliberate:** the plan's §4.3 sketch enforces the invariant with
`assert`. **`python -O` deletes asserts.** This is the one rule in the codebase
that must survive every optimisation flag, so it raises `ValueError`. There is a
test (`test_the_invariant_is_a_raise_not_an_assert`) that greps the class body
and fails if an `assert` reappears there.

I also added `LiveQueryState.is_a_finding_about_award_space`, so any future code
wanting to say something about availability must go through one property — a
sixth state cannot silently default to "yes, this tells you about award space".

### Step 4 — Per-leg query with the flexibility window ✅

Trip B with `--live --flex-days 0` issues **exactly 4** HTTP calls, one per
flight leg, none for the three hotels. With `--flex-days 3`: still exactly 4,
with `start_date`/`end_date` widened by 3 either side (asserted on the actual
`params` dict). An award dated 2027-01-17 on the B1 query lands in
`flexible_date_findings`, not in `points_candidates`, and
`test_an_off_date_award_does_not_move_the_margin_by_a_cent` asserts the totals
are **bit-identical** with and without it. Add a captured cash option dated
2027-01-17 and the same award **is** promoted and the leg records `DATE_SHIFTED`.

Budget: a stubbed 2 remaining calls produces 2 `OK` and 2 `BUDGET_EXHAUSTED`, and
the exhausted ones render "THIS IS A BUDGET FAILURE, not a finding".

### Step 5 — The merge, two provenances tracked separately ✅

`Leg` gained `cash_provenance` / `cash_captured_on` / `points_provenance` /
`live_outcome` / `superseded_candidates` / `flexible_date_findings` /
`date_shifted`. `CashOption` gained `source` / `captured_on` / `date`.

**Implementation note.** The plan says `LegResult` gains these fields. I put them
on `Leg` and exposed **read-through properties** on `LegResult`. The reason is
structural: `apply_live` transforms the *fixture* and hands it to the *unchanged*
`evaluate_leg`, so provenance has to live on the object that crosses that
boundary. Reading through also makes it impossible for a `LegResult`'s provenance
to disagree with its own `Leg`'s. The plan explicitly permits "or a sibling
record threaded through it".

`test_apply_live_never_mutates_the_cash_side` deep-compares all eight cash fields
on every leg before and after. B1's badges appear in `superseded_candidates` and
render as "the fixture claimed X, live returned Y".

### Step 6 — Partial failure ✅

New verdict sub-state `cash (no live points data)`. **Implemented as a post-pass
(`annotate_live_verdicts`) in `live_trip.py`, deliberately not inside
`evaluate_leg`.** Without it, such a leg would report "cash (no points path)",
whose reason text reads *"No UR transfer partner covers this leg"* — a claim
about partnerships we have no evidence for. The two failure modes get different
verdict text, and the literal string `"no award availability"` is asserted absent
from the entire rendered output of an error run.

### Step 7 — The margin with its provenance ✅

`trip_totals` gained the counters and `margin_provenance` /
`margin_provenance_note`. The percentage and the provenance line are emitted by
**the same `print_trip_totals` call**, and
`test_the_percentage_never_renders_without_its_provenance_line` renders five
fixtures and asserts adjacency. `--require-all-live` withholds the percentage
entirely and exits **3**:

```
$ python -m src.main --trip-fixture trip_b_europe.json --live --require-all-live ...
EXIT=3
│ Optimizer beats paying cash by │                                     WITHHELD │
│   withheld because             │ --require-all-live and provenance is 'mixed' │
THE TRIP MARGIN IS WITHHELD. ... 3 of 4 flight legs live (B1, B2, B4); 1 badge-derived (B3).
```

### Step 8 — Every live leg is useful ✅

§4.6's two-case rule implemented in `_taxes_are_the_whole_carrier_cash_figure`.
The tier-5 program-policy path is exercised **by the real SFO→MAD Aeroplan row**
and scores at $532.36. Every other live leg renders a floor **and** a break-even.
Tests assert no live leg has a blank or `-` surcharge cell and that `UNKNOWN`
never renders on a line containing `$0.00`.

**Deviation, flagged rather than done quietly.** §4.6's conversion table says a
single-entry carrier list becomes `carrier_source="seats_aero"` (i.e. known
metal). Taken literally that **contradicts the rule stated three paragraphs
below the same table**: a live award whose surcharge is a nonzero modeled band
must be unscoreable, because we cannot tell whether `{X}TotalTaxes` already
includes it. Following the table would have scored such a leg with the modeled
surcharge and **silently dropped the API's own tax figure**. I followed the rule:
such a candidate gets `carrier_source="seats_aero_live_taxes_unresolved"`, which
`has_known_metal` reads as False, routing it to the floor + break-even path.

For Trip B this is currently unreachable — every non-tier-5 surcharge row in
`data/surcharges.csv` is cabin `J` and Trip B is all economy — so it changes no
current number. It will matter the moment a business-cabin live leg appears.

### Step 9 — CLI, formatter, README ✅

`--live`, `--flex-days`, `--refresh`, `--cache-ttl`, `--require-all-live`,
`--snapshot-dir`, all in `--help`. `--live` without `--trip-fixture` exits **1**
with an actionable message.

**Bug I introduced and fixed:** the guard first lived in `build_live`, which is
only reached from `run_fixture`, so `--live` alone printed *"--origin,
--destination and --date are all required"* — answering a question the user did
not ask. Moved ahead of dispatch.

**Second bug I introduced and fixed:** the new provenance column made the per-leg
table 13 columns wide and rich starved the **Leg ID column to zero width** —
every row anonymous, every cross-reference in the rest of the output unusable. I
merged the two provenance columns into one (`points | cash`), added `min_width`
to Leg and What, and widened the console 170 → 190. Now pinned by
`test_every_leg_id_is_actually_visible_in_the_rendered_table`.

Malformed-input matrix: HTTP 401 / 429 / 500, a truncated JSON body, and a
corrupt cache file all covered. All five are `API_ERROR`; none is reported as a
finding about award space; none raises.

### Step 10 — Live Trip B ⛔ GATED, NOT RUN

Built and left gated. `tests/test_live_trip_b.py` skips with an explicit reason
and becomes a real offline regression test the moment the snapshots land — **no
edits required**. Its `_ReplayClient` holds no credentials and cannot reach the
network by construction, so a passing replay cannot be quietly passing on fresh
data.

### Step 11 — Trips endpoint ⛔ NOT PROBED

`GET /partnerapi/trips/{id}` was **not probed**. No egress. Explicitly
non-blocking per the plan. It remains the single most valuable unknown: if it
returns real segments and resolves operating metal, most live legs would convert
from UNKNOWN surcharge to a real figure.

---

## The exact commands for Tsuki's Mac

Run from the repo root, with `SEATS_AERO_KEY` in `.env`. Substitute the real
balance and card — §9.1 of the plan is right that the stranding constraint has
never bound on real data, and the report should say which numbers were used.

**1. The live run (spends 4 API calls):**

```bash
python -m src.main --trip-fixture trip_b_europe.json --live \
  --balance UR=<real> --card "<real card name>" \
  --transfer-date 2026-09-15 --flex-days 0
```

**2. Immediately again — proves the cache serves it for 0 API calls:**

```bash
python -m src.main --trip-fixture trip_b_europe.json --live \
  --balance UR=<real> --card "<real card name>" \
  --transfer-date 2026-09-15 --flex-days 0
```

Expect `served from the disk cache: 4 of 4` and the budget line unchanged from
before run 1.

**3. Once with a window, to exercise the advisory path (4 more calls):**

```bash
python -m src.main --trip-fixture trip_b_europe.json --live \
  --balance UR=<real> --card "<real card name>" \
  --transfer-date 2026-09-15 --flex-days 3
```

**4. Then commit the corpus and check the replay goes green:**

```bash
git add tests/fixtures/seats_aero/live_trip_b/
python -m pytest -q tests/test_live_trip_b.py
```

**Optional, Step 11 — probe the trips endpoint while the terminal is open:**

```bash
curl -s -H "Partner-Authorization: $SEATS_AERO_KEY" \
  "https://seats.aero/partnerapi/trips/<an ID from a search response>" | head -c 4000
```

Redact the key before committing anything from that.

**Before spending the calls, confirm the four legs.** §9.5 of the plan is right
and it has now been flagged in v0, v1, v2 and here: the fixture's B1 is
**SFO**→MAD while its description says MRY→MAD, and B3 is AMS→**LHR** while its
description says AMS→LON. **Live mode queries the airports the fixture names.**
A live margin over an incoherent itinerary is precise about the wrong trip.

---

## Deviations, gaps, and what the plan got wrong

**The plan got wrong**

1. **§8 names three tests; Step 0 breaks nine.** Six are unlisted, and two of
   those six are *required* by Step 0's own acceptance criteria. Table above.
2. **§4.6's conversion table contradicts §4.6's rule** for a single-carrier award
   with a nonzero modeled surcharge band. I followed the rule. Documented at the
   constant, in the module docstring and above.
3. **§4.3's `assert`-based invariant evaporates under `python -O`.** Changed to
   `raise ValueError`, with a test that keeps it that way.

**Deliberate deviations**

4. Provenance fields live on `Leg` with read-through properties on `LegResult`
   (§5 permits "or a sibling record threaded through it").
5. The new verdict sub-state is applied by a post-pass in `live_trip.py`, so
   `evaluate_leg` stays untouched — verified programmatically: its body contains
   none of `live`, `provenance`, `snapshot`, `PointsProvenance`.
6. `convert_taxes`'s disclosure string gained a `needs_confirmation` branch
   (Step 0's file list did not mention `seats_client.py`). String-only.
7. Console width 170 → 190 and the two provenance columns merged into one, so
   the leg ID stays visible.

**§7's "must not change" list, checked**

- `evaluate_leg` scoring logic — untouched (verified by source inspection).
- `parse_availability_row` / `resolve_source` — untouched.
- `data/surcharges.csv` — 10 rows, unchanged. The economy-cabin gap stays open.
- `data/ratios.csv` — unchanged.
- `data/bonuses.csv` — still header-only.
- The candidate-carrier list is never collapsed to one carrier (tested).
- `*Raw` fields still never scored.

**Gaps and things I could not do**

8. **No live behaviour is verified.** Everything is stubs and one recorded
   response. Pagination is still **never observed** — the client handles the
   documented shapes defensively and logs which path it took; Step 10 is the
   first real chance to see a multi-page response, and
   `test_the_pagination_path_each_leg_took_is_recorded` will fail the replay if a
   leg records no coverage note.
9. **The snapshot corpus is empty**, so
   `test_every_committed_snapshot_parses_into_valid_awards` passes vacuously. It
   becomes load-bearing the instant Step 10 runs.
10. **§11.1's taxes/surcharge rule is still a guess** about what Seats.aero's
    "total taxes" contains, made from one row on one program that levies no YQ.
    Step 10 may produce a YQ-levying row by accident; if it does, this rule
    should be revisited before anything is built on it.
11. **§11.3 is right and unaddressed.** A 6-hour TTL makes the margin a function
    of *when* you ran the tool. The unit that actually makes a result
    reproducible is the manifest, and v3 builds it but treats it as
    documentation. `--from-snapshot <manifest>` was left out to keep v3 to one
    feature. **If Step 10's margin is going to be quoted anywhere, it should be
    quoted against a manifest, not against a cache.** I'd put it first in v4.
12. **Real balances and the full card list are still unsupplied.** Every run to
    date used a synthetic `UR=180000` or no ceiling. The stranding constraint has
    still never bound on real data.

**One small design improvement over the plan.** The manifest's `awards` and
`state` columns cannot be filled at fetch time — only the parser knows how many
awards a response yields, and keeping that separation is the whole point of Step
1. Rather than leave them permanently `-` (a manifest that is not reviewable
defeats its own purpose), the live run comes back and completes its own row via
`ResponseCache.annotate_manifest`. Verified end to end against the stub:

```
| fetched_at (UTC)     | leg | route    | dates                  | rows | awards | state          | snapshot |
| 2026-09-08T21:52:26Z | B1  | SFO->MAD | 2027-01-15..2027-01-15 |    1 |      1 | ok             | B1_SFO_MAD_2027-01-15_20260908T2152Z.json |
| 2026-09-08T21:52:26Z | B3  | AMS->LHR | 2027-01-23..2027-01-23 |    0 |      0 | no_award_space | B3_AMS_LHR_2027-01-23_20260908T2152Z.json |
```

---

## What the tool can now say, and what it still cannot

It **can** now produce, for the first time, either of the plan's two target
sentences — the shape was exercised end to end against a stub with one leg
answering empty:

> Trip B beats cash by X% — 3 of 4 flight legs live, 1 leg (B3) badge-derived
> because Seats.aero returned no award space on that date. This margin mixes two
> provenances and must not be quoted as a live number.

It **cannot** yet say the live one, because no live call has been made. The
feature is the capability; the number is contingent on the world, and §10 of the
plan is right that the most likely outcome of Step 10 is a **mixed** margin. That
is a real answer and the design treats it as one.
