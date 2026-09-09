# v5 adversarial report — replay, `--new-trip`, key resolution, the default flip, APD

**Author**: Tester
**Date**: 2026-09-09
**Under attack**: `c2b2b7a` on `master`, nine commits. 737 passed / 13 skipped under `python -O`.
**Probes**: `docs/test-reports/v5-probes/` — 98 tests, all green, run with

```
python -O -m pytest docs/test-reports/v5-probes -p no:randomly
```

`test_v5_findings.py` (19 tests) asserts every defect below is **present**, so each goes red
when it is fixed. `test_v5_held_up.py` (79 tests) asserts the things that held, written against
clean unmutated inputs so no fix for a finding can destroy them.

**No code was changed.** One scratch fixture (`tests/fixtures/trips/apdprobe_j.json`) was
written during the APD probe and deleted; `git status` on `tests/fixtures/trips/` is clean.

---

## 1. Did I find way nine?

**Yes. Way nine is that way (6) does not survive way (7) or way (8).**

The `LiveLegOutcome` docstring lists eight ways to fail to know, each with its own field and its
own `raise`. Every one of them is enforced *at the moment the outcome is constructed*. None of
them is enforced across a **round trip through storage** — and v5 added a second storage round
trip (`--from-snapshot`) on top of the one that already existed (the disk cache).

Way (6), coverage, is a field on `RawSearchResult`. It is computed during pagination and it is
**never written to disk**. `ResponseCache.put` builds `_meta` from an explicit key list that does
not include `incomplete` / `incomplete_reason`, so both are dropped; `SnapshotTransport.search_raw`
hard-codes `incomplete=False`. So the same bytes answer differently depending on how many times
they have been read:

| read | state | render |
|---|---|---|
| the live fetch | `ANSWERED_INCOMPLETE` | "COVERAGE IS INCOMPLETE …" |
| the same bytes, from the disk cache | `NO_AWARD_SPACE` | "**THIS IS A FINDING**: … there is no award to buy on this date" |
| the same bytes, from `--from-snapshot` | `NO_AWARD_SPACE` | same |

That is the house failure — a failure to know reported as a finding — for the **sixth** time, on
a new axis: not "which branch forgot to say it", but "the fact was never persisted, so no branch
*could* say it". The `LiveLegOutcome` invariant that forbids `NO_AWARD_SPACE + result_incomplete`
does not fire, because by the time the outcome is built the truncation no longer exists.

Two things make this worse than an ordinary miss. First, MR-1's fix for this exact class is
**dead code with a comment claiming otherwise** (`seats_client.py:893-898`: *"A truncated result
set that was cached used to come back with incomplete=False, because the flag was never persisted
— so the SECOND run of a truncated query lost the coverage warning entirely and reported a clean
finding."* It still does). Second, the `-O`-proof `raise` discipline gives no protection here, so
the eight-way list reads as complete while the ninth way sits underneath it.

**The generalisation, for the docstring:** every axis field that a future run may read back from
storage needs an invariant on the *write*, not only on the construction. Today `content_hash` is
the only field with that property, and it is the only one that survives a round trip.

---

## 2. Findings, worst first

| # | Sev | Title |
|---|---|---|
| C-1 | **Critical** | Way nine: truncation is lost by the disk cache and by replay, and a truncated result becomes a confident finding of no award space |
| C-2 | **Critical** | A replay that scored nothing prints the pure Google-badge margin with a manifest hash glued to it |
| H-1 | High | The manifest hash does not bind the trip: a replay answers with bytes captured for a different route |
| H-2 | High | APD's cabin comes from the points candidate, never from the leg — so a `--new-trip` fixture is always charged the reduced rate |
| H-3 | High | APD is added on top of a **captured** surcharge — the double charge the whole live/offline asymmetry exists to prevent |
| H-4 | High | A `--new-trip` fixture scored `--offline` reports its designed absence of points prices as "no partner exists" |
| M-1 | Medium | A snapshot with zero pages replays as a confident finding of no award space |
| M-2 | Medium | The `content_hash` column is compared with `startswith`, so a one-character column passes |
| L-1 | Low | An unrecognised cabin silently resolves to `standard`; an empty one to `reduced` |
| L-2 | Low | The user-config key file's permissions are never checked |

---

### C-1 — CRITICAL — Truncation does not survive storage; a truncated result becomes a finding

**Where**
- `src/response_cache.py:333-374` — `put()` builds `envelope["_meta"]` from an explicit key list.
  `incomplete` and `incomplete_reason` are handed to it in `meta` and never copied.
- `src/seats_client.py:1063-1065` — the caller that hands them over.
- `src/seats_client.py:897-898` — the cache-hit branch reads `hit.meta.get("incomplete", False)`,
  which is therefore always `False`.
- `src/snapshot_replay.py:625-627` — `SnapshotTransport.search_raw` passes `incomplete=False`
  literally, with a comment claiming coverage "is RECOMPUTED from the archived pages by the
  parser". It is not recomputed anywhere; `search()` copies `raw.incomplete` verbatim
  (`seats_client.py:1263`).
- The snapshot envelope has no `incomplete` field at all, so the fact is not even archived.

**Repro** — `test_C1a`, `test_C1b`, `test_C1c`.

```
$ python -c "... cache.put(k, req, [{'data': [], 'hasMore': True}],
                           meta={'incomplete': True, 'incomplete_reason': '...'}) ..."
_meta keys: ['awards_parsed', 'cache_schema', 'canonical_request', 'content_hash',
 'fetched_at', 'fetched_by', 'http_status', 'key_note', 'key_redacted', 'leg_id',
 'manifest_key', 'pages', 'pagination_note', 'parser_version', 'request',
 'request_key', 'rows_seen', 'snapshot', 'state', 'trip_id']
hit.meta.get('incomplete') -> False
hit.meta.get('incomplete_reason') -> ''
```

End to end, one leg, one stubbed transport returning `{"data": [], "hasMore": true}` twice:

```
RUN 1 state: answered_incomplete | incomplete: True
RUN 2 state: no_award_space      | incomplete: False | from cache: True

RUN 2 render:
 Seats.aero returned NO award space for LHR->SFO on 2027-01-27 (searched LHR->SFO on
 2027-01-27, 0 rows). THIS IS A FINDING: the API answered, every row it sent was READ
 SUCCESSFULLY, and the answer is that there is no award to buy on this date.
```

And on the replay path, with a manifest whose snapshots carry `hasMore` and whose `_meta`
records the INCOMPLETE pagination note:

```
$ python -m src.main --trip-fixture trip_b_europe.json --from-snapshot .../MANIFEST.md ...
EXIT 0
has 'COVERAGE IS INCOMPLETE': False
```

**Expected**: `incomplete` / `incomplete_reason` persisted in the snapshot and cache envelopes;
a cache hit or a replay over truncated bytes yields `ANSWERED_INCOMPLETE` and prints the
`_truncation_clause()`. **Actual**: the flag is silently discarded on write; the second and every
subsequent read of the same bytes asserts a finding about award space, and the render prints no
coverage clause at all.

**Why it is Critical rather than High**: the default TTL means the *normal* second run of any
live trip reads from the disk cache, so this is not an edge case; and the state it produces
(`NO_AWARD_SPACE`) is the one state the tool treats as a positive fact about the world — it flows
into `is_a_finding_about_award_space`, into `annotate_live_verdicts`' "That is a FINDING: there is
no award to buy here", and into `--require-all-live` as a satisfied leg.

---

### C-2 — CRITICAL — A badge margin printed with a manifest hash on the same line

**Where**
- `src/main.py:781-789` — `totals["manifest_hash"]` is set on every `--from-snapshot` run,
  unconditionally, regardless of what the replay actually contributed.
- `src/formatter.py:745-746, 768-786` — `hash_suffix` is appended to **every** cell that carries a
  percentage.
- `src/live_trip.py:945-963` — the run is correctly labelled `badge_fallback`, but that label is
  printed on a **different table row**.

**Repro** — `test_C2`. A manifest whose four snapshots are valid, hash-verified and empty of
awards, replayed with `--allow-badge-fallback`:

```
EXIT 0
Optimizer beats paying cash by │ 2.04% - 11.03%  mh_3131325b90bdc950 │
  low end = what is actually defensible        │ 2.04%  mh_3131325b90bdc950 │
  high end = only if every unknown surcharge is $0 │ 11.03%  mh_3131325b90bdc950 │
  replayed from manifest │ mh_3131325b90bdc950 │
```

and, for comparison, the same fixture with no transport at all:

```
$ python -m src.main --trip-fixture trip_b_europe.json --offline ...
Optimizer beats paying cash by │ 2.04% - 11.03% │
  margin provenance            │ badge          │
```

**Expected**: the hash is quoted only against a number the hashed bytes produced; a
`badge_fallback` margin gets no `mh_`. **Actual**: the number is byte-identical to the offline
Google-badge margin — not one of the four snapshots contributed a single point — and it carries a
16-hex certificate saying "anyone with this repo gets this exact number from these bytes".

The plan calls the same-call emission of percentage-and-hash *"the one structural defence in this
codebase that has actually worked"*. It works in the wrong direction here: the only token glued to
the number is the false one, and the qualifier that would correct it (`badge_fallback`) is two rows
below, where a copy-paste loses it. Step 6's acceptance criterion — *"the qualifier is on the same
line"* — is not implemented for any provenance; only the hash is.

---

### H-1 — HIGH — The hash does not bind the trip; a replay answers with another route's bytes

**Where**
- `src/snapshot_replay.py:567-594` — `SnapshotTransport.search_raw` takes `origin`, `destination`
  and `date_range` and **discards all three**. The row is looked up by `leg_id` alone.
- `src/snapshot_replay.py:630-665` — `verify_covers_legs` compares leg ids only. `ManifestRow`
  parses `route` and `dates` (`:88-89`), and `manifest_hash` even hashes them (`:496`), but
  nothing ever compares them to the leg being scored.
- `src/snapshot_replay.py:273-277` — `select_replay_set` treats a row whose `trip_id` is absent as
  matching **any** trip.

**Repro A** — `test_H1a` / `test_H1b`. Capture a manifest for Trip B, then make the fixture edit
the plan itself proposes in §8.1 (B1's origin):

```
$ python -m src.main --trip-fixture edited.json --from-snapshot .../MANIFEST.md ...
EXIT 0
│ B1 │ MRY->MAD │ $395.00 │ 39,500 │ Air Canada Aeroplan @ 1:1 │ 50,000 │ $32.36 │ captured │ ...
  B1 - MRY->MAD, Jan 15 2027 ... snapshot B1_SFO_MAD_2027-01-15.json
Optimizer beats paying cash by │ 0.00%  mh_85cbb9107c0486c5 │
```

The unedited fixture and the MRY fixture print **the same hash**, `mh_85cbb9107c0486c5`. Two
different itineraries, one certificate.

**Repro B** — `test_H1c`. A manifest written for another trip is correctly refused while its
`trip_id` column is populated; replacing that column's value with `-` (which `_cell` reads as
absent) makes it replay against Trip B, exit 0, with a hash:

```
other trip_id:            1 | (no margin row)
trip_id blanked to '-':   0 | Optimizer beats paying cash by │ 0.00%  mh_85cbb9107c0486c5 │
```

**Expected**: a row whose `route`/`dates` do not match the leg's `origin`/`destination`/`date`
refuses the run, and the hash covers the itinerary as well as the bytes.
**Actual**: the leg id is the only binding, and a leg id is three characters a human chose.

The module docstring says *"a percentage printed beside a hash must cover everything the trip
contains, or the hash certifies a subset and looks like it certifies the whole."* It certifies a
*different* trip and looks the same.

---

### H-2 — HIGH — APD picks reduced/standard from the points candidate, never from the leg

**Where**
- `src/optimizer.py:1522-1526` — the cabin is `result.best_points.cabin`, else the first
  candidate's cabin, else the literal `"Y"`.
- `src/trip_loader.py:96` — `cabin` is read on **points candidates only**.
- `src/models.py` — `Leg` has no `cabin` field at all.
- `src/trip_builder.py:321` — the builder writes a leg-level `"cabin"` key that nothing reads.

A `--new-trip` fixture has no `points_candidates` **by design**, so on the offline path its APD
cabin is always the `"Y"` default.

**Repro** — `test_H2a`, `test_H2b`, `test_H2c`.

```
$ python -m src.main --new-trip apdprobe_j --leg LHR:SFO:2027-01-27:500 --cabin J
  L1  LHR->SFO, Jan 27 2027, one-way, business, 1 adult  ->  $500.00 USD
...  "cabin": "J",

$ python -m src.main --trip-fixture apdprobe_j.json --offline ...
  APD: UK AIR PASSENGER DUTY on L1: this leg DEPARTS GB for US. ADDED to the points-side
  cash total: GBP 102.00 x 1 = GBP 102.00 = $138.11 per the reduced rate.
```

**Expected**: `GBP 244.00 … per the standard rate` (plan Step 7's own acceptance criterion:
*"A J-cabin UK departure to the US picks standard (£244)"*).
**Actual**: £102, described confidently as "per the reduced rate", understating by £142 (~$192)
per passenger. A `--cabin W` fixture gets the same £102 and **never prints the pitch caveat**,
which the plan lists as a named acceptance item.

The existing `test_apd_scoring.py` passes because it drives the cabin through a candidate. Nothing
tests the shape v5's own builder produces.

---

### H-3 — HIGH — APD is added on top of a captured surcharge

**Where**: `src/optimizer.py:1529-1572`. The only distinction drawn is
`points_provenance in (LIVE, SNAPSHOT)`. `result.best_points.surcharge_captured` is never read.

The plan's asymmetry rests on: *"On the offline path the tool owns the whole cash figure and knows
APD is missing from it."* That is true of a **modelled** surcharge and false of a **captured** one:
a booking page's "taxes, fees and carrier charges" line for a UK departure necessarily contains
APD, for exactly the reason `TotalTaxes` might.

**Repro** — `test_H3`. A fixture leg LHR→SFO whose candidate carries
`surcharge_captured: true, cash_surcharge: 330.0` and a source note saying the figure includes the
£102 APD line:

```
│ L1 │ LHR->SFO │ $900.00 │ 90,000 │ British Airways Executive Club @ 1:1 │ 20,000 │
     $330.00 │ captured │ $668.11 │ ...

APD: ... ADDED to the points-side cash total: GBP 102.00 x 1 = GBP 102.00 = $138.11
```

$200 (20,000 pts) + $330 (captured, already containing APD) + $138.11 (APD again) = $668.11.

**Expected**: a captured surcharge is flagged `apd_inclusion_unverified` exactly as a live one is —
same reasoning, same words. **Actual**: it is charged twice, and the leg's warning says "APD is
owed on award tickets too" as if the capture had not already paid it. No current fixture exercises
this, so it is a latent defect that lands the first time a real ex-LHR surcharge is captured —
which is precisely what plan Step 8 asks Tsuki to go and do.

---

### H-4 — HIGH — A `--new-trip` fixture scored offline reports "no partner exists"

**Where**: `src/main.py:745-746` — `annotate_live_verdicts` runs only when `live_opts.live`.
Its docstring says it exists because `evaluate_leg` would otherwise report a leg with no points
data as *"cash (no points path)", whose reason text says "No UR transfer partner covers this leg"
— a claim about PARTNERSHIPS that we have no evidence for here.* On the offline path nothing
re-labels it.

Before v5 this mattered less, because an offline fixture carried badges. v5's builder writes
fixtures with **no points prices at all, by design**, and `--offline` is a documented mode for
them.

**Repro** — `test_H4`.

```
$ python -m src.main --new-trip e2e --leg SFO:MAD:2027-01-15:395
$ python -m src.main --trip-fixture e2e.json --offline ...
Verdict: CASH (NO POINTS PATH) - No UR transfer partner covers this leg. Cash is the
only option.
Legs with NO points path at all (no partner exists): L1
```

**Expected**: "no award price was captured for this leg", the state that is actually true.
**Actual**: a claim that no UR transfer partner covers SFO→MAD — on the same route where Trip B's
own B1 scores an Air Canada Aeroplan path in the same run of the same binary.

---

### M-1 — MEDIUM — A snapshot with zero pages replays as a finding

**Where**: `src/snapshot_replay.py:356-470` — `verify` checks existence, loadability, both hashes
and the archived state, and never checks whether `pages` contains anything. An empty list hashes
cleanly, so it passes every check.

**Repro** — `test_M1`. Four snapshots with `"pages": []`:

```
EXIT 3
Verdict: CASH (NO LIVE POINTS DATA) - Pay cash. Seats.aero ANSWERED for SFO->MAD on
2027-01-15 and returned NO award space on this date. That is a FINDING: there is no
award to buy here.
```

The margin is correctly withheld (provenance `none`), which is why this is Medium and not High.
Every **leg** still asserts a finding about award space derived from a file containing no response.
A live fetch never writes a zero-page envelope — `put` is skipped on a budget failure and an HTTP
failure archives nothing — so a zero-page snapshot means truncation or corruption, i.e. exactly
the state that must not become a finding.

---

### M-2 — MEDIUM — `content_hash` is compared with `startswith`

**Where**: `src/snapshot_replay.py:446` — `elif not recomputed.startswith(row.content_hash):`.

**Repro** — `test_M2`. Truncating every row's `content_hash` column to its first hex character:

```
1-char content_hash: 0 | Optimizer beats paying cash by │ 0.00%  mh_85cbb9107c0486c5 │
```

**Expected**: the column is 16 hex characters or the row is not verifiable. **Actual**: any prefix
matches, so the column's strength is `16^len`. At one character the check the module calls *"the
check that cannot be defeated by editing the file"* is a 1-in-16 coin flip, and combined with a
swapped snapshot file it is the difference between a refusal and a scored run. Blanking the column
is correctly caught (`content_hash_unknown`); shortening it is not.

---

### L-1 — LOW — An unrecognised cabin resolves to a rate instead of UNKNOWN

**Where**: `src/apd.py:564` and `src/apd.py:425` — `CABIN_TO_CLASS.get(cabin, "standard")`;
`src/apd.py:541` — `cabin = (cabin or "Y")`.

**Repro** — `test_L1`:

```
cabin J           : known=True band=B class=standard gbp=244.0
cabin 'X'         : known=True band=B class=standard gbp=244.0
cabin 'economy'   : known=True band=B class=standard gbp=244.0
cabin ''          : known=True band=B class=reduced  gbp=102.0
```

Both defaults are guesses, in opposite directions, in the module whose own docstring says *"a
departure this table does not cover is a departure whose tax is UNKNOWN"* and whose band lookup
goes to great lengths to return `None` rather than band A. The live parser only ever emits
`Y/W/J/F`, so today this is reachable through a hand-written fixture candidate; it becomes
reachable from data the day any other cabin vocabulary enters the tool.

---

### L-2 — LOW — The user-config key file's permissions are never checked

**Where**: `src/config.py:54-68, 160-166`. Plan risk 9.5 notes that
`~/.config/points-optimizer/.env` is outside the repo and outside gitignore's protection. Nothing
inspects its mode; a world-readable key file is read and used with a banner that says only
"user config". `st_mode` and `S_IRGRP` appear nowhere in `config.py` or `main.py`. Reported as an
observation, not a defect — but it is the one new place a key can live and nothing looks at it.

---

## 3. What held up — 79 probes, `test_v5_held_up.py`

Written against clean inputs so that fixing anything above cannot make them red.

**Key resolution (Step 1) — clean.** All four orderings correct with the higher-priority sources
present (flag → env → repo `.env` → user config); an empty `--api-key` does not win; the
not-found error names all four locations in order; `mask_key` gives `pro_…jwV` for a long key and
a bare `…` for anything under eight characters; the full key appears in no captured output and in
no file written under `--snapshot-dir` on a failing live run. The `_ENV_INJECTED` bookkeeping does
what it was built for — a key that came out of the repo `.env` reports as `repo .env` and not as
`environment`.

**The default flip (Step 6) — clean.** No network and no `--offline` gives exit 3, `WITHHELD`, and
**no percentage of any kind** in the output. `--offline --live` is exit 1 with the mutual-exclusion
message. `--from-snapshot` with each of `--live`, `--offline`, `--refresh`, `--cache-ttl`,
`--flex-days` is exit 1, naming the flag. `--new-trip … --from-snapshot` is exit 1.

**Manifest refusals — clean, and this is the strongest part of v5.** Every one of these exits 1
with `THIS MANIFEST CANNOT BE REPLAYED` and **no percentage anywhere in the output**: deleted
snapshot; flipped byte (`meta_hash_mismatch`); flipped byte with `_meta.content_hash` repaired
(`manifest_hash_mismatch` — the recomputation catches it, as designed); zero-byte snapshot
(`snapshot_unloadable`, explicitly *not* read as an empty result); a manifest covering 3 of 4 legs;
a manifest naming a leg the trip does not have; a manifest whose `trip_id` names another trip; two
rows sharing the latest `fetched_at` and naming different snapshots (`ambiguous_latest_row` — the
tie is refused rather than resolved by file order); a pre-v5 row with no `content_hash` column
(`content_hash_unknown`, never "matches"); a row naming `../../../etc/passwd`
(`snapshot_outside_corpus`); a file that is not a manifest; a missing manifest.

**A clean replay — clean.** No percentage is ever printed on a line without its `mh_`; two replays
of the same manifest are identical modulo the wall clock; the manifest and snapshot directory are
byte-for-byte unchanged after a replay (a file does not write itself); a replayed leg renders
`REPLAYED FROM A COMMITTED SNAPSHOT` and margin provenance `snapshot`, never `live`; a duplicate
row is reported as `4 groups, 4 selected, 1 superseded` rather than double-counted; a stale
`parser_version` replays with the prominent `REPARSED UNDER A DIFFERENT PARSER VERSION` banner and
exit 0, which is the documented trade.

**APD (Step 7) — mostly clean.** The 2027-04-01 boundary is exact on both sides and on the day
(2027-03-31 → £102.00, 2027-04-01 → £105.33). A date before every published period is UNKNOWN, not
the nearest rate, and prints no `$0.00`. A non-GB departure returns `None` — no field, not a zero.
Arriving at LHR (B3, AMS→LHR) is not a UK departure. LHR→EDI resolves to `DOMESTIC` (£8), not band
A. A country the band table marks UNKNOWN (EG) returns `band=None`, `is_known=False`, and its
render contains no `$0.00`. The per-passenger multiplication is shown (`GBP 102.00 x 2 = GBP
204.00`). A live/replayed UK leg states the amount and does **not** add it
(`IT IS NOT ADDED HERE … NOBODY HAS CHECKED`). Offline Trip B carries exactly **one** APD line, on
B4, and B4's points side lands at $418.11 against $482 cash — the $63.89 saving the plan predicted,
down from $202.

**`--new-trip` validation — clean.** Twenty-two bad inputs each refuse with **no file written**
(directory listing asserted unchanged after every one): `../../etc/foo`, `a/b`, `.`; unknown,
2-character and 4-character IATA; `origin == destination`; `2027-02-30`; `15/01/2027`; a past
date; cash `0`, `-1`, `abc`, `$395`, `nan`; 3 and 5 colon fields; `--travelers 0` and `-1`;
`--cabin X`; zero legs; a hotel with `NIGHTS=0`. Lowercase and space-padded codes are upper-cased
rather than refused. An existing fixture is not overwritten without `--force`. No
`points_candidates` key is written on any leg, the `LIVE_ONLY_FLAG` sentence travels inside the
file, hotel legs carry `nights` and **no** `origin`/`destination`, a colon inside a hotel name
survives the `rsplit`, and the description is generated from the codes. A built fixture loads and
scores end to end.

**The eight-way invariants under `-O`** — all eight `raise` clauses still fire with assertions
disabled: `NO_AWARD_SPACE` with an error / with unreadable rows / on an incomplete result set;
`API_ERROR` with no error; `NOT_QUERIED` with no note; `ANSWERED_UNREADABLE` with zero unreadable
rows; `served_from_cache` with no `cache_fetched_at`; `replayed_from_snapshot` with no
`snapshot_content_hash`, together with `served_from_cache`, or with provenance `LIVE`.

**Regression** — `docs/test-reports/adversarial-probes/`: **39 failed / 39 passed**, exactly the
v1–v3 baseline. No v4 fix regressed. Full suite under `-O`: **737 passed, 13 skipped**.

---

## 4. Two notes for the Architect, not findings

**The plan's §10.3 worry is real but it is not the one that bit.** The Architect was 60/40 on
`--require-all-live` being satisfied by bytes of arbitrary age. That mechanism worked correctly in
every probe. What failed instead is the *opposite* direction: a replay that satisfied nothing at
all still got the certificate (C-2). The risk was on the age of the bytes; the defect is on whether
the bytes were used.

**The corpus warning in §8.2 has already come true, in miniature.** Every manifest in the test
suite and in this report was written by the same code that reads it, so the only properties under
test are the ones both halves agree on. C-1 and H-1 are both cases where the writer and the reader
agree with each other and disagree with reality: the writer never stores coverage and the reader
never asks for it; the writer records a route and the reader never reads it. Step 10 is still the
thing most likely to find the next one.
