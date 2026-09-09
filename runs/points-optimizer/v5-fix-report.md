# v5 fix round — all ten findings, and what v5 itself shipped

**Author**: Coder
**Date**: 2026-09-09
**Fixed on top of**: `c2b2b7a` (v5 as shipped, nine commits)
**Head**: `d8cf8f2`, eleven commits added (one for the Tester's artifacts, ten fixes)
**Suite**: `python -O -m pytest` → **785 passed, 13 skipped**, up from 737/13. No test was
deleted, weakened or skipped.
**Probes**: `python -O -m pytest docs/test-reports/v5-probes -p no:randomly` →
**19 failed, 79 passed**. Every one of the 19 defect-asserting probes is red; every one of the
79 held-up probes is green.
**No network was used and no live behaviour is claimed.** Every number below is pasted from a
run in this sandbox.

---

## 1. What v5 Steps 1–8 actually shipped

The v5 coder report was cut off by a rate limit. This is what is in the tree at `c2b2b7a`,
read back from the code rather than from the plan.

| Step | Commit | What landed |
|---|---|---|
| 1 | `ed9815c` | Four-source key resolution (`--api-key` → env → repo `.env` → `~/.config/points-optimizer/.env`), first hit wins and later sources are never consulted; `mask_key` as the only function that formats a key (`pro_…jwV`, and a bare `…` under eight characters); the `_ENV_INJECTED` bookkeeping so a key that `load_env` copied out of the repo `.env` reports as `repo .env` and not as `environment`; a not-found error naming all four places in order. |
| 2 | `355c8b9` | Manifest v2: `content_hash`, `parser_version` and `trip_id` columns; `PARSER_VERSION`; `manifest_hash` = sha256 over the *selected rows' recomputed content*, sorted, printed as `mh_` + 16 hex. Pre-v5 rows read `unknown`, never "matches". |
| 3 | `62f9c7d`, `c2b2b7a` | `--from-snapshot MANIFEST.md`: `parse_manifest` / `select_replay_set` (latest `fetched_at` per (leg, route, dates), duplicates reported as superseded) / `verify` / `verify_covers_legs`, all refusing *before* anything is scored; `SnapshotTransport`, a `SeatsClient`-shaped object that reads a committed file, spends no budget and has instance-level caches; **way (8)** on `LiveLegOutcome` (`replayed_from_snapshot` + four fields, three `raise`s, an unconditional `_replay_clause()`). |
| 4 | `6103fe1` | `--new-trip` in flags mode: `--leg ORIGIN:DEST:DATE:CASH`, `--hotel`, `--travelers`, `--cabin`; twenty-two classes of bad input refused with **nothing written**; no `points_candidates` key on any leg, by design, with the `LIVE_ONLY_FLAG` sentence written inside the file. |
| 5 | `e1d411a` | `--new-trip` interactive prompting over the same validators, and chaining a freshly built fixture into `--live`. |
| 6 | `e7acabd` | Live-first defaults: no transport and no `--offline` withholds the margin and exits 3; `--offline`/`--live` mutually exclusive; `--from-snapshot` refuses a second transport or `--new-trip`; exit 2 written down; `badge_fallback` split out of `badge` so "a live run that produced nothing" stopped sharing a label with "a run that never asked". |
| 7+8 | `4d2ae45` | APD wired into scoring: `data/apd.csv` + `data/apd_bands.csv`, the 2027-04-01 boundary, `apd_for_leg` returning `None` for a non-GB departure and a written-down UNKNOWN for an uncovered one, per-passenger arithmetic shown, ADDED on the offline path and STATED-not-added on live/replayed; Step 8 is the gate Tsuki runs on his Mac, recorded in the research doc. |

Two things Step 6's own acceptance criteria asked for were **not** implemented until this round:
"the qualifier is on the same line" was done for the hash only (C-2), and Step 7's "a J-cabin UK
departure picks standard (£244)" was true only through a points candidate (H-2).

---

## 2. Way nine — fixed as a class

**The finding**: all eight ways to fail to know were enforced *at `LiveLegOutcome` construction*.
None survived a round trip through storage. `ResponseCache.put` built `_meta` from an explicit key
list that did not contain `incomplete` / `incomplete_reason`; `SnapshotTransport.search_raw` passed
`incomplete=False` literally, under a comment claiming the parser recomputed it. So the same bytes
answered `ANSWERED_INCOMPLETE` on the fetch and "THIS IS A FINDING … there is no award to buy on
this date" on every read afterwards — including the ordinary second run inside the six-hour TTL.

### How omission was made structurally impossible

Not "add two keys to a list". The persisted set is **derived from the fields the invariants read**:

1. `models._fields_read_by_invariants(LiveLegOutcome)` parses the AST of `__post_init__` **itself**
   and returns every `self.X` the invariants touch. The question "which fields do the invariants
   depend on?" has exactly one truthful answer and it is the code.
2. Every one of those fields must appear in exactly one of three classifications:
   * `RECOMPUTED_FROM_BYTES` — the parser recomputes it on every read; persisting it would create a
     second source of truth that can disagree with the pages;
   * `CARRIED_BY_THE_TRANSPORT` — a fact about the fetch the bytes do not state, so it **must**
     round-trip through every storage layer;
   * `LOCAL_TO_THIS_RUN` — who answered, on this run; persisting it would launder provenance.
3. `_check_way_nine_classification()` runs **at import** and raises `ValueError` (not `assert` —
   this must survive `-O`) if a field an invariant reads is unclassified, classified twice, or is
   not a field at all. **A tenth field cannot be added to an invariant without `src/models.py`
   refusing to import.**
4. `PERSISTED_PROVENANCE_KEYS` is derived from the carried classification, and **every storage layer
   loops over it**: `ResponseCache.put` (which also refuses to write an envelope missing one, via
   `assert_persists_provenance`), the disk-cache read, the in-process `CACHE_META`, and
   `SnapshotTransport.search_raw`. None of them has a key list of its own, so a field cannot be
   written and not read, or read and not written. `seats_client` calls
   `models.assert_transport_carries(RawSearchResult)` at import, so "what the invariants need" and
   "what the transport can carry" are checked in the running process.
5. `seats_client.coverage_of_pages()` makes the false comment true: coverage is **recomputed from
   the stored bytes** by asking the last archived page the same question the live pagination loop
   asks it, with the same `_next_page_params` code. Snapshots written before way (9) existed — which
   carry no coverage keys at all — therefore still replay as INCOMPLETE. Persisted flag and
   recomputed coverage are **unioned**; neither can quietly answer "complete" for the other.

The three carried fields today are `incomplete`, `incomplete_reason` and `fetched_at`. That last one
is the proof of the diagnosis: it was the only invariant-bearing field that already round-tripped,
and way (7) is the only way that already survived storage.

### The docstring entry

Way (9) is in the `LiveLegOutcome` docstring as **AXIS 6 — WILL THE ANSWER STILL BE THE SAME ANSWER
NEXT TIME IT IS READ?**, following the list's own extension rule, and the extension rule itself was
amended: a tenth way now needs a field, a `raise`, a render clause, a numbered entry **and a storage
classification**. The way's own entry says why it has no new field — it is a property of the storage
layers, not of one axis — and where its invariant lives instead (`__post_init__` cannot see a file).

### Proof, run under `-O`

Before (the Tester's repro) and after, same script:

```
RUN 1 state: answered_incomplete | incomplete: True
RUN 2 state: answered_incomplete | incomplete: True | from cache: True
RUN 2 render: Seats.aero ANSWERED for LHR->SFO on 2027-01-27 and we READ every row it sent
 (0 rows), and found no award - but the result set was TRUNCATED and we never saw the rest of
 it. THIS IS NOT A FINDING: an empty page of an incomplete answer says NOTHING about whether
 award space exists on this date. ... COVERAGE IS INCOMPLETE: ...
persisted: {'incomplete': True, 'incomplete_reason': 'pagination stopped after page 1 because
 the response says there are more results (hasMore) but carries no cursor and no usable offset,
 so there is no way to ask for the rest.', 'provenance_keys': ['fetched_at', 'incomplete',
 'incomplete_reason']}
```

(RUN 2 was `no_award_space | incomplete: False` with "THIS IS A FINDING …" before the fix.)

**Round-trip tests**: `tests/test_way_nine_round_trip.py`, 13 tests — the classification is derived;
an unclassified tenth field is refused; the transport must be able to carry every persisted field;
`put` persists all of them and the file on disk says so; a truncated fetch reads back truncated from
the disk cache, from the in-process cache, and through snapshot replay; a snapshot that never stored
coverage is recomputed from its bytes; and the construction-time invariant still refuses the
combination that started all this.

---

## 3. Every finding

| # | Verdict | Probe(s) that flipped | Commit |
|---|---|---|---|
| C-1 | Fixed | `C1a`, `C1b`, `C1c` | `e362675` |
| C-2 | Fixed | `C2` | `d63e9bb` |
| H-1 | Fixed | `H1a`, `H1b`, `H1c` | `45015db` |
| H-2 | Fixed | `H2a`, `H2b`, `H2c` | `4499c53` |
| H-3 | Fixed | `H3` | `fc6474c` |
| H-4 | Fixed | `H4` | `274e534` |
| M-1 | Fixed | `M1` | `afecf96` |
| M-2 | Fixed | `M2` | `18df91b` |
| L-1 | Fixed | `L1` ×4 | `0ed7081` |
| L-2 | Fixed | `L2` | `d8cf8f2` |

Nothing is disputed. Every finding was reproduced before it was fixed.

### C-2 — the false certificate

`totals["manifest_hash"]` was set on every `--from-snapshot` run and the formatter appended it to
**every** percentage cell, so a replay that scored nothing printed the pure Google-badge margin with
a 16-hex certificate on it.

The qualifier now rides the number **for every provenance**, and the hash is quoted only against a
number the hashed bytes produced (`margin_provenance == "snapshot"` — every scoreable leg replayed).
Kept deliberately short, because a suffix long enough to wrap puts the number on one physical line
and its qualifier on another; the first draft of this fix did exactly that and was shortened.

Real output, the C-2 corpus (four valid, hash-verified, award-free snapshots) with
`--allow-badge-fallback`:

```
│ Optimizer beats paying cash by                     │ 2.04% - 11.03%  (badge_fallback - NOT from mh_3131325b90bdc950) │
│   low end = what is actually defensible            │          2.04%  (badge_fallback - NOT from mh_3131325b90bdc950) │
│   high end = only if every unknown surcharge is $0 │         11.03%  (badge_fallback - NOT from mh_3131325b90bdc950) │
│   replayed from manifest (these bytes produced
│   NO part of the margin above)                     │                                             mh_3131325b90bdc950 │
│   margin provenance                                │                                                  badge_fallback │
```

and a clean replay, where the hash is earned:

```
│ Optimizer beats paying cash by │ 0.00%  (snapshot mh_85cbb9107c0486c5) │
```

The held-up probe "a clean replay never prints a percentage without its `mh_`" still passes.
`tests/test_margin_qualifier.py` (9 tests) holds the rule for all five provenances, including that a
withheld margin prints no percentage and therefore no hash.

*(Since M-1 also landed, the C-2 corpus — four zero-page snapshots — is now refused outright with
exit 1. The badge_fallback output above was captured before that commit; the qualifier logic is
exercised directly by `tests/test_margin_qualifier.py`.)*

### H-1 — the hash did not bind the trip

`search_raw` took `origin`, `destination` and `date_range` and discarded all three; rows were found
by leg id, and a leg id is three characters a human chose.

* `ManifestRow.covers()` compares the row's `route` and its **date span** (so a `--flex-days`
  capture still legitimately covers a date inside it) against the leg being scored. An unreadable
  route column is UNKNOWN, never "matches".
* `verify_covers_legs` takes `LegQuery(leg_id, origin, destination, date)` instead of bare ids and
  **refuses** a mismatch before anything is scored.
* `search_raw` re-checks the same thing and raises a transport error — never an absence of award
  space — so the transport cannot answer a question it was not asked even if a caller skips
  verification.
* An absent `trip_id` is `trip_id_unknown`, the rule `content_hash` already followed. One blanked
  cell no longer matches every trip.
* `manifest_hash` now covers the trip id and the scored itinerary, so one certificate cannot cover
  two itineraries even in principle.

```
$ python -m src.main --trip-fixture edited.json --from-snapshot .../MANIFEST.md ...
THIS MANIFEST CANNOT BE REPLAYED and NOTHING has been scored.
  - [row_does_not_match_the_leg] row 7: B1 SFO->MAD 2027-01-15..2027-01-15 ->
    B1_SFO_MAD_2027-01-15.json: the row holds bytes captured for SFO->MAD and the leg being
    scored is MRY->MAD. A snapshot of a different route is not an answer about this one.
    The leg being scored is B1 MRY->MAD on 2027-01-15.
EXIT 1
```

### H-2 — APD's cabin

`Leg` now has a `cabin` field, the loader reads the key the builder has been writing since Step 4,
and `_apd_cabin()` states the order: **the leg's cabin wins**, a scored candidate's answers when the
leg is silent, a disagreement between the two is reported rather than resolved silently (the two
HMRC rates are £142 apart), and nothing at all is UNKNOWN — which L-1 renders as a written-down
unknown rather than a rate.

```
$ python -m src.main --new-trip apdprobe_j --leg LHR:SFO:2027-01-27:500 --cabin J
$ python -m src.main --trip-fixture apdprobe_j.json --offline ...
  APD: UK AIR PASSENGER DUTY on L1: this leg DEPARTS GB for US. ADDED to the points-side cash
  total: GBP 244.00 x 1 = GBP 244.00 = $330.38 per the standard rate.
```

A `--cabin W` fixture now prints the CABIN MAPPING CAVEAT (asserted directly; the probe that says it
never prints is red).

### H-3 — APD on a captured surcharge

The rule is now stated once, in `_apd_inclusion_unverified()`: **APD is ADDED only when this tool
modelled the whole points-side cash figure**. *Where else does this shape appear?* — a captured
mandatory fee payable on the points side is the same thing wearing another field name, so it is
covered here too rather than waiting to be found separately.

```
│ L1 │ LHR->SFO │ $900.00 │ 90,000 │ British Airways Executive Club @ 1:1 │ 20,000 │ $330.00 │
     captured │ $530.00 │ $900.00 │
  APD: ... It owes GBP 102.00 x 1 = GBP 102.00 = $138.11 per the reduced rate. ... IT IS NOT
  ADDED HERE: this leg's points-side cash includes a CAPTURED surcharge of USD 330.00 taken
  from a booking page ('BA award; taxes+carrier charges CAPTURED from BA.com'), and NOBODY HAS
  CHECKED whether that figure already includes APD.
```

$668.11 → **$530.00**. Same reasoning and the same words as the live path, plus
`inclusion_source` naming *which* figure to go and check.

### H-4 — "no partner exists"

The real cause was not that `annotate_live_verdicts` runs on the live path only. It is that
`evaluate_leg`'s last `else` made a claim about Chase's partner list out of the **absence of an
input** — way (9) wearing another hat, on the verdict instead of on a storage layer. So the fix is
where the verdict is decided, on every path: a flight leg with no candidates gets
`points_absence="never_priced"` and its own words; a leg whose recorded programs reach nothing keeps
the old words, which are true there; the footer and the totals row are split so "(no partner
exists)" is printed only about legs it is true of.

```
Verdict: CASH (NO POINTS PATH) - NO AWARD PRICE WAS CAPTURED FOR THIS LEG and none was fetched,
so the points side was never priced. NOTHING is claimed about whether a UR transfer partner
covers SFO->MAD - that question was never reached. Score it with --live or --from-snapshot, or
add a captured award price to the fixture.

Legs whose points side was NEVER PRICED (no award price captured and none fetched - this says
NOTHING about whether a partner covers them): L1
```

The verdict string stays `cash (no points path)` and `legs_without_points_path` still counts them,
so Trip B's three hotels and the held-up probe that asserts `CASH (NO POINTS PATH)` are unaffected.

### M-1, M-2, L-1, L-2

* **M-1** — a zero-page snapshot is `snapshot_empty` and refuses the run: a fetch that answered
  writes at least one page, and a budget or HTTP failure writes no file, so zero pages records no
  observation. *Where else?* — the disk cache, where a zero-page envelope is now a MISS WITH A
  WARNING like a corrupt one (the file is kept; a response is evidence).
* **M-2** — the `content_hash` column must be 16 or 64 hex characters, compared for **equality**.
  A shorter column is `content_hash_malformed`: unverifiable, which is never "matches".
* **L-1** — `CABIN_TO_CLASS.get(cabin, "standard")` and `cabin or "Y"` were guesses in opposite
  directions (`'X'` → £244, `''` → £102, so absence made the tax *smaller*). Both are gone; an
  unrecognised cabin is an is_known=False charge that prints "UNKNOWN IS NOT ZERO" and no `$0.00`.
* **L-2** — `key_file_permission_warning()` reports the mode, who can read it and the exact `chmod`,
  emitted in the **same string** as the source line. It reports and does not refuse; a mode this
  process cannot read produces no claim either way; the key is still never printed.

```
Seats.aero key: user…uuu   (source: user config /tmp/.../user.env)
  [KEY FILE PERMISSIONS] /tmp/.../user.env is mode 0644, so it is readable by your group and
  every user on this machine. This file is outside the repository, so .gitignore protects
  nothing here. Run: chmod 600 /tmp/.../user.env
```

---

## 4. Trip B's offline numbers

**Unchanged, to the cent.** The only difference is the new qualifier on the number:

```
$ python -m src.main --trip-fixture trip_b_europe.json --offline --balance UR=160000 \
    --card "Chase Sapphire Preferred" --transfer-date 2026-09-15
  APD: UK AIR PASSENGER DUTY on B4 ... ADDED to the points-side cash total:
       GBP 102.00 x 1 = GBP 102.00 = $138.11 per the reduced rate.
│ Pay cash for everything            │ $3,126.11 │
│ Optimizer's recommendation         │ $3,062.22 │
│ Saving                             │    $63.89 │
│ Optimizer beats paying cash by     │ 2.04% - 11.03%  (badge) │
│   low end = what is actually defensible          │  2.04%  (badge) │
│   high end = only if every unknown surcharge is $0│ 11.03%  (badge) │
│   margin provenance                │     badge │
```

Trip B carries exactly one APD line (B4), on the reduced rate, because B4's scored award is a Y
cabin and the fixture records no leg-level cabin — H-2's resolution order preserves that. Its
badges model the surcharge rather than capturing it, so H-3 does not suppress the addition.

---

## 5. Regression

* Full suite under `-O`: **785 passed, 13 skipped** (737/13 before; +48 tests, no removals).
* v5 probes: **19 failed / 79 passed** — all 19 defect probes red, all 79 held-up probes green.
* v1–v3 probes (`docs/test-reports/adversarial-probes/`): **40 failed / 38 passed**, against the
  39/39 baseline. The one that moved is
  `test_B6e_a_cache_file_whose_pages_are_an_empty_list_is_a_HIT`, which asserts a *defect* is
  present; M-1 fixed it. The baseline moved in the right direction and nothing regressed.
* `git grep pro_` over the committed tree before each commit: only `pro_…jwV` masks, the
  `mask_key` docstring and `"pro_" + "x"*24 + "jwV"` in tests. No key material.

## 6. For the Manager and the Architect

1. **The corpus warning in §8.2 is the live one.** C-1 and H-1 were both writer-and-reader agreeing
   with each other and disagreeing with reality. Way (9)'s classification closes the *field* version
   of that; it does not close the *semantic* version, and Step 10 (a corpus this code did not write)
   is still the thing most likely to find the next one.
2. **`--require-all-live` is still satisfied by a `snapshot` provenance of arbitrary age.** The
   Architect was 60/40 on this and the Tester found the opposite defect (C-2). It remains untouched
   and it remains a judgement call.
3. **H-3 changes what Step 8 will measure.** A captured ex-LHR surcharge now flags rather than
   double-charges, so the gate's outcome (a) / (b) / (c) is still exactly as decisive as the plan
   says, and the first real capture will no longer land on a $138 double charge while it is decided.
