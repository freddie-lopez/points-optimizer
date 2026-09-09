# Live Seats.aero snapshot corpus (`live_trip_b/`)

**This directory is EMPTY of responses as of v3's build.** That is the honest
state: the build sandbox has no network egress, so no live call has been made
from it, and no response has been invented to fill the gap. Step 10 of
`docs/plans/v3.md` is the run that populates it, and it can only happen on
Tsuki's Mac.

## What lands here

Every live fetch writes two files in one operation (`ResponseCache.put`): a
cache entry under `data/cache/seats_aero/` keyed by request hash, and a
**snapshot** here under a browsable name:

```
<leg_id>_<origin>_<destination>_<start_date>_<yyyymmddThhmmZ>.json
```

Both are the **same envelope format**, so any file here is directly loadable as
a test fixture, and `MANIFEST.md` joins the two — one row per fetch, recording
what was asked, when, how many rows came back and what state the leg resolved
to.

## Why raw pages and not parsed Awards

The v2 parser read `cost` / `taxes` / `Source` / `Carriers`, none of which exist
in the real payload. It produced **zero awards from a response containing a real
bookable 9-seat award**, and that emptiness was reported as "no award
availability". The fix was only possible because one raw response happened to
have been pasted into a file.

Caching parsed objects would have cached the bug. Caching raw means a future
parser fix replays every response this project has ever seen — offline, in CI,
for zero API calls.

## Rules this directory lives by

- **No key material, ever.** The `Partner-Authorization` header is not part of
  the request key, is not written into `_meta`, and every envelope states
  `"key_redacted": true`. `assert_no_key_material()` refuses the write, and
  `tests/test_response_cache.py` greps the committed tree.
- **Deduplicated by content hash.** A re-fetch returning byte-identical data
  adds a manifest row but no second file. The corpus is a set of distinct
  observations, not a log.
- **The corpus is executable, not decorative.**
  `test_every_committed_snapshot_parses_into_valid_awards` replays every file
  here through the parser and asserts each award has a program, a positive cost
  and a non-empty source note. A snapshot nobody replays rots.
- **Snapshots are committed; the cache under `data/cache/` is gitignored.** One
  is evidence, the other is runtime state.
