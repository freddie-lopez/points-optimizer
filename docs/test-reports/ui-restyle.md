# Adversarial test report: UI restyle to direction C ("Graphite")

Tester, 2026-09-13. Branch `feature/ui-restyle` at 7fba6e0 (10 commits over `feature/ui`
386b2fc). Inputs: `docs/design/UI-BRIEF.md` §2/§5/§8, `docs/plans/ui-restyle.md`,
`runs/ui-restyle/coder-report.md`, the diff `386b2fc..HEAD`.

Nothing here calls Seats.aero: every probe server refuses `connect()` for every address,
Chromium runs with every name but 127.0.0.1 unresolvable and an interceptor that aborts
and records anything offsite (the Google Fonts link is the only entry, and it never loads).
Nothing was written into the repo tree: trips, wallet, cache and snapshots are tmp copies.
No existing probe or test was edited. No application code was touched.

New probes: `docs/test-reports/ui-restyle-probes/` (harness: `conftest.py`,
`restyle_probe_server.py`; probes `test_r_a…k`; screenshots under `shots/`). Run with
`python3 -O -m pytest -q -p no:cacheprovider -p no:randomly docs/test-reports/ui-restyle-probes`
(~3.5 min; Playwright's Python API on the same Chromium the round-1 drive.js uses).
RED = a defect that is present. To tell "new" from "pre-existing" I ran the same probe
server against a `git worktree` of 386b2fc (`PO_PROBE_ROOT`); those comparisons are cited
per finding and the worktree has been removed.

## Findings

### 1. High — RUN goes off-screen at 400px when the "Why LIVE is unavailable" fold is open

- **Repro**: `test_r_d_layout.py::test_D1_RUN_stays_inside_the_strip_whatever_the_reason[nokey-slashes-400]`
  and `[nokey-noslash-400]`. By hand: a server with no key (`restyle_probe_server.py no_key`),
  Trips → Trip B, viewport 400, open the `key-error` fold. Screenshot `shots/d1-400-slash-nokey.png`.
- **Expected** (plan §6 step 5 acceptance, §7 "also … with the key-error fold open …
  `bodySW <= 400`; `run-go` inside `run-strip`"; brief §4 "no page-level horizontal scroll"):
  the strip stays 400 wide; RUN reachable.
- **Actual**: `document.body.scrollWidth` = **1020** at a 400 viewport. The engine's real
  error text (six lines, the longest 133 chars, two of them absolute paths) sits in a
  `pre.transcript` inside a `details.fold` that is a grid item of `.runstrip`; the grid
  item's automatic minimum widens the strip to the longest line, the three mode cards
  stretch with it, and the run row's `margin-left:auto` puts Options/RUN at x≈870–1020 —
  off the right of a 400px phone. RUN is inside the (now 1020-wide) strip, so the coder's
  "run-go inside run-strip" check passed; the page scrolls sideways, which is the thing the
  rule forbids. With the fold closed the page is 400 wide.
- **New or pre-existing**: pre-existing (386b2fc: 1016). But the plan made this case an
  explicit acceptance criterion of step 5, the coder reported it verified ("with and without
  the key-error fold open … `scrollWidth` = 1440 / 400"), and it is not. The coder's
  verification stubbed `/api/state` and evidently used a short `error_text`; the real one
  is what a user with no key sees, which is the first-run state (brief §7 #7).
- **Location**: `src/ui/static/app.css:170` (`.runstrip { display: grid; … }`) with
  `app.css:227` (`pre.transcript … overflow: auto` — the scroll container does not stop a
  grid item's min-content contribution); the fold is built at `src/ui/static/app.js:505-509`.
  A `min-width: 0` on the fold (or `.runstrip > * { min-width: 0 }`) is the shape of the fix;
  the tester does not fix.

### 2. Medium — the sticky top bar at 400px grew from 106px to 149px (21% of a 700px phone)

- **Repro**: `test_r_k_misc.py::test_K1_top_bar_height_at_400[offline_b|no_key|no_wallet]`;
  `shots/k1-bar-no_key-400.png`.
- **Expected**: the plan says "Media queries: unchanged breakpoints … Add nothing that
  widens the page at 400px"; the brief pins "works down to 400px". A sticky bar's height is
  viewport the content never gets back.
- **Actual**: four rows (wordmark+tabs / mode pill / CALLS line / wallet chips + key chip),
  **149px** at HEAD vs **105.6px** at 386b2fc for the same state — 48px `min-height`, 24px
  pills, 28px gaps and the 16px wordmark each add a little. The coder's report admits "~170px,
  the pre-existing wrap"; the wrap is pre-existing, the +43px is new. The mode pill sits
  alone on a row because `OFFLINE` (72px) + gap + the CALLS line (300px) is 386px > 368px
  available; the CALLS line is the pre-existing full sentence.
- **Location**: `src/ui/static/app.css:48` (`.topbar-in min-height:48px; gap:28px`),
  `:56` (`.bar-right gap:14px`), `:57` (`.pill height:24px`), and the `@media (max-width:
  720px)` block at `:290-300` which is unchanged and does not tighten any of it.

### 3. Medium — with a key found and an OFFLINE or REPLAY run, nowhere on the page says a key was found or where it came from

- **Repro**: `test_r_e_keychip.py::test_E1_key_found_the_chip_exists_is_hidden_and_the_state_is_unchanged`.
- **Expected**: decision D8 hides the chip on the strength of "the run-details box already
  prints the source". Plan §6 step 3 acceptance: "the run-details box still says
  `(source: environment)`".
- **Actual**: only after a **LIVE** run. After an OFFLINE run (the default mode, and the only
  mode a first-time user reaches without spending calls) `run-details` says
  `Seats.aero key: not used (offline: no transport)`; after REPLAY, `not required`. The
  word "environment" appears nowhere on the page. At 386b2fc the bar always read
  `key: environment`. So the found-state information was not moved, it was dropped, for
  every run that is not LIVE. §4.8's `Key [key-source]` row still lists `key: environment /
  key: repo .env / key: user config` as user-facing text that now never renders. The
  decision was Tsuki's; the premise it was sold on is only true for LIVE. Manager's call.
- **Location**: `src/ui/static/app.js:241-246` (the chip), `app.js:771-773` (run-details
  prints the source only when `c.key_source` is set, i.e. LIVE); `docs/plans/ui.md` §4.8
  Key row.

### 4. Low — the key chip and the LIVE card disagree on the same screen after the key changes (pre-existing race, now more visible)

- **Repro**: `test_r_e_keychip.py::test_E3_the_chip_follows_the_key_when_it_goes_away_and_comes_back`
  (server `no_key_toggle`, stdin `key on`, run OFFLINE).
- **Expected**: chip hidden ⇔ LIVE card enabled, at any instant.
- **Actual**: after the run, `refreshState()` re-renders the top bar asynchronously while
  `go(...)` has already rendered the strip from the previous state: the chip vanishes
  (key found) while the LIVE card still says "LIVE needs a Seats.aero key." with the
  `key-error` fold under it, until the next render (a tab click fixes it — asserted). Same
  code path at 386b2fc (there the bar said `key: environment` beside a disabled LIVE card).
  Restoring the key mid-session is exactly what a user does after reading that fold.
- **Location**: `src/ui/static/app.js:639-643` (`refreshState()` then `go()`), `app.js:168-175`.

### 5. Low — "the ONE primary action": the Search screen shows two coral buttons at once

- **Repro**: `test_r_c_colour.py::test_C5_how_many_primary_actions_are_on_screen_at_once[search_ok]`.
- **Expected**: plan §4.1 "`--accent2` … the ONE primary action"; step 2 acceptance "RUN … is
  the only coral thing on the Trips screen" (holds for Trips).
- **Actual**: on Search with an award cell open, `Run search` and the drawer's `Score against
  a fare →` are both `.btn-primary`, both coral, on screen together. The plan itself lists
  both as primary, so this is the plan being inconsistent with its own rule, not the coder
  deviating. Not a §2 issue.
- **Location**: `src/ui/static/app.js:1225`, `:1461`; `app.css:97`.

### 6. Low — the drawer is a fixed sheet at exactly 1180px, docs say docked "≥1180px"

- **Repro**: `test_r_d_layout.py::test_D5_drawer_is_a_full_sheet_below_1180_and_sticky_under_the_bar_above[1180]`.
- **Expected**: ui.md §4.7 / brief §4 "docked as a third column ≥1180px".
- **Actual**: `@media (max-width: 1180px)` is inclusive; at 1180 the drawer is `position:
  fixed`. Pre-existing (identical at 386b2fc); breakpoints were out of scope. Off-by-one in
  the docs or the query; noting it because the plan's step 7 acceptance quotes "≥1180".
- **Location**: `src/ui/static/app.css:285`.

### 7. Low — ui.md §4.7 says the mode cards are `flex: 1 1 200px`; app.css says `flex: 1 1 0; min-width: 200px`

- **Repro**: `test_r_g_fonts_security_docs.py::test_J2_ui_md_4_7_prose_matches_app_css_values`.
- **Expected**: §4.7 "the spec; implement exactly" matches the sheet.
- **Actual**: the coder disclosed deviation 1 in the report and rewrote §4.7 in the same
  round without carrying it in. The other nine prose values I spot-checked (POINTS fill,
  hatch, bar rgba/blur, drawer shadow, chip padding, label tracking, 440px, card padding)
  and all twelve hex tokens match (`test_J1` green).
- **Location**: `docs/plans/ui.md:387` vs `src/ui/static/app.css:111`.

### 8. Low — the reason a mode is unavailable is painted at 2.7:1

- **Repro**: `test_r_k_misc.py::test_K4_contrast_of_the_new_tokens_on_their_grounds` (prints
  every sampled ratio).
- **Expected**: "LIVE needs a Seats.aero key." / "no snapshot to replay yet" are the only
  place the strip says *why* a mode is off; brief §2 wants reasons in the user's face.
- **Actual**: `--muted` on `--panel` inside a `.55`-opacity disabled card = **2.69:1**
  (WCAG exempts disabled controls, but the text is information, not a control). At 386b2fc
  it was `.45` opacity — worse — so this is an improvement that still lands under 3:1. All
  other sampled pairs ≥ 4.5:1 except `pre.transcript` (`--muted` on `--bg`, 5.6:1 — fine)
  and the pressed-card title (accent on tint, 8.9:1).
- **Location**: `src/ui/static/app.css:117` (`.seg button:disabled { opacity: .55 }`), `:114`.

### Not findings, but stated for the manager

- **Trip C at 400 / hostile trip name** (`test_F7`, `shots/f7-hostile-400.png`): a 5,000-char
  name renders as text, the page stays 400 wide, `window.__pwned` is false — but the ≤720px
  trip list is a horizontal strip whose columns share the tallest row's height, so the broken
  file's warn fill becomes a 3,000px-tall brown column beside it. Pre-existing layout, new
  colour on it. Only with hostile data.
- **The disabled REPLAY card's caption** is the CLI's `replay_reason`, not the detail; the
  detail (with the path) is in the run row. Unchanged wording; noted because the two now sit
  in different visual places.
- **Coder's "Known gaps"** are as small as claimed: the busy state is placed by the same
  `.runrow` rule (measured in `test_D2` at 1440 and 400, `shots/d2-busy-*.png`: the note and
  the buttons share the row at 1440; at 400 the note takes its own line and RUN stays inside
  the strip); fonts were never seen (true here too — `document.fonts` reports nothing loaded,
  the fallback stack renders with zero clipped `nowrap` elements at 400 and 1440, `test_G1`).
- **Undisclosed CSS changes** beyond the plan's list, from the diff (`test_J8` pins the only
  removed rule, `.seg button:last-child`): `.triplist h2` padding, `.newtrip margin-left`,
  `.chip` line-height 1.45→1.5, `.trow .flag` and `.nolegs .flag` from 10px mono to 11px
  sans uppercase, `.flags/.refusal/.funding` got `.label { color: var(--warn) }`, the
  `@media (prefers-reduced-motion: no-preference)` transition block is unchanged. None
  changes text or a marker; `.trow .flag` is a form change to a caption (still the same
  words, `test_F2`). Nothing here is wrong; it is more than "exactly these, nothing else".

## What I could not break

- **§2 markers** (`test_r_a`, 159 green over 13 scenarios: Trip B/C offline, LIVE stub,
  REPLAY, API down, never-priced couple, exit 4, no wallet, wallet-file error, no key,
  Search ok / API error / no awards): every capitalised marker the transcript carries is in
  the rendered DOM outside the transcript fold and outside the drawer's CLI line
  (`UNKNOWN`, `NOT $0`, `WITHHELD`, `UNVERIFIED`, `NEVER PRICED`, `API FAILED`, `CONFIRM
  BEFORE TRUSTING`, `NOT ADDED`, `NOT FUNDABLE`, the range labels, the exit chips…). Tallies
  of zero (`NOT RECORDED 0`, `UNREADABLE: 0`) are excluded as tallies, not markers.
- **Forms by computed style**, in every table cell AND every leg drawer of every scenario:
  `.chip-unknown` `border-style: dashed` with the literal DOM text `UNKNOWN` (not CSS
  uppercase); `.chip-withheld` `repeating-linear-gradient(135deg, rgba(240, 184, 90, 0.16)…)`;
  `.chip-cashq` `border-left-width: 3px` in a colour different from its other edges;
  `.chip-points` a non-transparent fill; `.tag-unv` `font-variant-caps: all-small-caps`;
  exit 3/4 chips hatched, exit 1/2 chips warn-bordered. No `data-kind="unknown"` cell shows
  a number, blank, dash or zero. Headline percentage never without `(badge)/(live)/(snapshot
  mh_…)`, value and qualifier one element.
- **Grayscale by pixels** (`test_r_b`, 2× DPR element screenshots converted to L): UNKNOWN's
  top edge alternates (dashed); WITHHELD's padding band alternates (hatch); the qualified
  CASH edge is brighter than both its interior and its top border; POINTS' interior differs
  from a plain chip's; the selected trip row and the pressed mode card differ from their
  neighbours by edge luminance. `shots/b_gray_1440.png`. Note the CSP refused an injected
  `filter: grayscale(1)` stylesheet — which is the CSP working.
- **Colour semantics** (`test_r_c`): `var(--win)` is used by exactly one rule (`.chip-points`)
  and no rendered element outside `.chip-points` is painted `rgb(95, 211, 160)`; no element
  whose own text is a money figure is green; the coral fill/text appears on `.btn-primary`
  only; every chip carries a word.
- **Run strip, to-do #3** (`test_D1` with a key, 8 cases green): 200+-char reason around a
  120-char path with and without slashes, at 1440 and 400 — RUN and Options fully inside
  the strip, page 1440/400 wide, the `.path` `title` holds the whole path, RUN enabled.
  Busy state (`test_D2`, real 3-second run): `Scoring… N s` inside the strip, RUN disabled,
  on the buttons' line at 1440.
- **No horizontal scroll at 400** on Search, Trips list, trip detail with result, drawer
  open, wallet panel, new-trip form, options panel, fixture legs table, Search with drawer
  (`test_D3`, `test_D7`); LEG/VERDICT pinned, sticky, opaque `rgb()` backgrounds, ≥100px
  middle (`test_D4`, 400 and 1440); drawer fixed full-height sheet at 1179 and sticky under
  the bar at 1200/1280/1440 with a one-row (49px) bar at 1180–1440 (`test_D5/D6`; at
  386b2fc the bar was 83px at 1180, so the drawer's `top: 64px` is now right where it was
  wrong before).
- **Key chip** (`test_E2/E4/E5`): not found → visible, `key: not found`, `keysrc warn`, warn
  text on an opaque warn fill, solid border; `mode-pill` reads `LIVE UNAVAILABLE` in warn on
  Search with `search-run` disabled and the refusal panel; `mode-live` disabled with the
  `key-error` fold on Trips. Found → element present with its testid, `hidden`, empty.
  `index.html` starts it `hidden` and never says `key: …`. `/api/state` cannot have changed:
  `git diff 386b2fc..HEAD -- src/ui/*.py src/main.py src/formatter.py` is empty.
- **Trip-list kinds** (`test_r_f`, a `{ not json` file in a tmp trips dir): exactly one of
  `trow-trip|trow-search|trow-broken` per row; captions byte-identical (`CANNOT LOAD` +
  the JSON error, `NOT A PER-LEG TRIP` + `a single-route search request for SFO->LHR`,
  `N legs · N flights · N hotels`); forms differ by shape (3px rail / transparent 1px /
  visible 1px + fill), none dashed; grayscale tells the three apart; `aria-current` selects
  each kind with the accent name (broken row's border goes `--warn`, search rail goes accent).
- **Fonts offline** (`test_G1/G2`): the only offsite request is
  `https://fonts.googleapis.com/css2?family=Figtree…Red+Hat+Mono…`, aborted; computed
  families name Figtree / Red Hat Mono with the fallback rendering; zero page errors; zero
  `nowrap` elements overflowing their box at 400 or 1440; every top-bar item inside the viewport.
- **Security** (`test_r_g` H1–H7): server/api/engine/serializer byte-identical to 386b2fc
  (so the CSP is); no inline `<script>`, `<style>`, `style=` or `on*=` in the shell; no
  `url(`, `@import` or `http` in app.css; no `innerHTML`/`insertAdjacentHTML`/`cssText`/
  `eval` in app.js and no new `.style.` (2 at base, 2 now); with the fake key set, neither
  the key nor its mask is anywhere in `page.content()` or the transcript after a LIVE run,
  and `(masked key not sent to the browser)` is; every testid string of 386b2fc survives;
  `tests/test_ui_static_rules.py` + `tests/test_ui_security.py` green, also with
  `SEATS_AERO_KEY` set to a fake.
- **Hostile data on the new surfaces** (`test_I1/I2`, `test_F7`): `<img onerror>`,
  `</pre><script>`, U+202E and 5,000-char strings in a LIVE payload, a search payload and a
  trip's name/description render as text (no `img`/`script` elements, `__pwned` false),
  the drawers and the new trip rows stay inside 400/1440.
- **Docs and plan compliance** (`test_J1/J3–J8`): the twelve §4.7 hex tokens equal app.css;
  the mockup re-skin removed no marker string and no body text but `key: environment` (D8);
  no old token name, no `font-stretch`, no `docs/plans/`, `docs/test-reports/`, `vN`,
  `Step N` in anything the page ships; the app.js diff is five hunks whose added string
  literals are exactly the class/testid names and `key: not found`; the seven disclosed
  deviations match the diff.
- **Existing suites**: nothing red (counts below). The coder's numbers are reproduced.

## Counts

| Suite | Command | Result |
|---|---|---|
| Full suite, `-O` | `python3 -O -m pytest -q -p no:cacheprovider` | **3692 passed, 13 skipped** (128 s) |
| Full suite, no `-O` | `python3 -m pytest -q -p no:cacheprovider` | **3692 passed, 13 skipped** (131 s) |
| `tests/test_ui_static_rules.py` | `-O` | 82 passed |
| `tests/test_ui_security.py` | `-O` | 76 passed |
| both, `SEATS_AERO_KEY=fakekey_…` | `-O` | 158 passed |
| ui-probes (round 1–6) | `python3 -O -m pytest -q -p no:cacheprovider docs/test-reports/ui-probes -p no:randomly` | **663 passed, 0 failed** (507 s) — 0 red by id, as pinned |
| ui-restyle-probes (new) | `python3 -O -m pytest -q -p no:cacheprovider -p no:randomly docs/test-reports/ui-restyle-probes` | **246 passed, 11 failed, 10 skipped** (198 s) |

Red in the new suite, by test id (each maps to a finding above):

| Test id | Finding |
|---|---|
| `test_r_d_layout.py::test_D1_RUN_stays_inside_the_strip_whatever_the_reason[nokey-slashes-400]` | 1 |
| `test_r_d_layout.py::test_D1_RUN_stays_inside_the_strip_whatever_the_reason[nokey-noslash-400]` | 1 |
| `test_r_k_misc.py::test_K1_top_bar_height_at_400[offline_b]` | 2 |
| `test_r_k_misc.py::test_K1_top_bar_height_at_400[no_key]` | 2 |
| `test_r_k_misc.py::test_K1_top_bar_height_at_400[no_wallet]` | 2 |
| `test_r_e_keychip.py::test_E1_key_found_the_chip_exists_is_hidden_and_the_state_is_unchanged` | 3 |
| `test_r_e_keychip.py::test_E3_the_chip_follows_the_key_when_it_goes_away_and_comes_back` | 4 |
| `test_r_c_colour.py::test_C5_how_many_primary_actions_are_on_screen_at_once[search_ok]` | 5 |
| `test_r_d_layout.py::test_D5_drawer_is_a_full_sheet_below_1180_and_sticky_under_the_bar_above[1180]` | 6 |
| `test_r_g_fonts_security_docs.py::test_J2_ui_md_4_7_prose_matches_app_css_values` | 7 |
| `test_r_k_misc.py::test_K4_contrast_of_the_new_tokens_on_their_grounds` | 8 |

Skipped (10): `test_A1`/`A5` where the scenario has no transcript (no wallet, wallet error
render a banner/refusal, Search has no exit chip) and `test_A3` where there is no headline.

Green by file: `test_r_a_markers.py` 159/159 (10 skipped) · `test_r_b_grayscale.py` 7/7 ·
`test_r_c_colour.py` 21/22 · `test_r_d_layout.py` 26/29 · `test_r_e_keychip.py` 3/5 ·
`test_r_f_triplist.py` 8/8 · `test_r_g_fonts_security_docs.py` 20/21 · `test_r_k_misc.py` 2/6.

Screenshots: `docs/test-reports/ui-restyle-probes/shots/` (colour and grayscale at 1440,
every 400px view, the run strip in all D1/D2 cases, the bar, the modal, the hostile rows).

## Re-test: 1f535aa

Fix commits bb8ffc9 (D1), 86e1715 (K1), da6405d (J2+K4), 94ebb0c (D5); `runs/ui-restyle/
coder-report.md` "Fix round 1". The diff `7fba6e0..1f535aa` touches only `src/ui/static/app.css`
(+26/−6: the `.runstrip` column, `.seg button:disabled` and its title, the 720px block, the
media line), `docs/plans/ui.md` (one sentence) and the coder report. No JS, no HTML, no server.
Each fix was attacked on its own with new probes in `test_r_l_retest.py` (12 tests), then the
whole suite, the full suite both ways and ui-probes were re-run.

**Fixed**

- **Finding 1 (High, D1)** — fixed. `test_D1` 8/8 green. Re-attacked with the longest error
  text the engine can produce (`restyle_probe_server.py no_key_longpath`: both file paths it
  names are single ~270-char segments, longest line 288 chars, no break opportunity), fold
  open, at **400 and 360** (`test_L1`, `shots/l1-longkey-{400,360}.png`): page 400/360 wide,
  the strip ends inside the viewport, RUN and Options inside it and on screen, the `pre`
  scrolls inside its own box (`scrollWidth` > `clientWidth`, right edge ≤ viewport) and still
  carries the whole text including `[RELOCATED by POINTS_OPTIMIZER_ENV_FILE]` and the long
  path. The fix is `.runstrip { grid-template-columns: minmax(0, 1fr) }` + `> * { min-width: 0 }`.
- **Finding 2 (Medium, K1)** — fixed. Bar height at 400 and 360 (`test_L2`, six cases, plus a
  hand measurement): **82.75px** before a run, **96.5px** once the CALLS line carries a run's
  "this run" clause (was 149; base was 105.6). Three rows: wordmark+tabs / CALLS at 11px /
  pill + wallet chips + key chip. Nothing clipped (no `nowrap` element overflows its box),
  nothing outside the viewport at either width in the with-key, no-key and no-wallet states.
  Tab order from a fresh load reaches exactly the bar's focusables (`tab-search`, `tab-trips`,
  the wallet chip) in DOM order, each inside the viewport and inside the bar — the `.calls
  { order: -1 }` reorder is visual only, and the counter is not focusable. One observation:
  below 720px the screen-reader/DOM order (pill, CALLS, chips) now differs from the visual
  order (CALLS, pill, chips). Cosmetic; noting it because `order` is the kind of thing that
  bites later.
- **Finding 6 (Low, D5)** — fixed. `@media (max-width: 1179.98px)`: at **1179** the drawer is
  a fixed full-height sheet with a two-column grid; at **1180 and 1181** it is sticky, docked
  as a third column, its head below the bar, the page not wider than the viewport
  (`test_L4`, `test_D5`, `test_D6` green).
- **Finding 7 (Low, J2)** — fixed. ui.md §4.7 now names `flex: 1 1 0; min-width: 200px;
  max-width: 420px`; `test_J2` green.
- **Finding 8 (Low, K4)** — fixed. Disabled cards at `.8` opacity with the title in `--muted`:
  caption 4.18:1 (`test_K4` green). Grayscale (`test_L3`, 2× element screenshots): the
  disabled LIVE card's title is ≥40 luminance levels darker than the pressed card's and its
  edge ≥15 darker, so it still reads as disabled with colour removed.

**Not fixed (by instruction, unchanged)**: Finding 3 (`test_E1`, key source absent from the page
after OFFLINE/REPLAY runs), Finding 4 (`test_E3`, chip/LIVE-card disagreement for one render),
Finding 5 (`test_C5`, two coral buttons on Search). Still red, same repro, same text as above.

**New**

- **9. Low — a disabled mode card shows a pointer cursor.** `test_L3b`. `.seg button { cursor:
  pointer }` has the same specificity as the global `button:disabled { cursor: not-allowed }`
  and comes later, so LIVE-without-a-key and REPLAY-without-a-manifest invite a click. Present
  at 386b2fc (`.seg button` had `cursor: pointer` there too) — pre-existing, found while
  checking K4's disabled state. `src/ui/static/app.css:111`.

**My own probe, edited**: `test_J8_undisclosed_css_changes_the_coder_did_not_list` went red only
because 94ebb0c rewrote `@media (max-width: 1180px) {` to `1179.98px` and the probe parses a
changed selector line as a removed rule. I read the whole `7fba6e0..1f535aa` app.css diff: the
media line, the `.runstrip` rules, `.seg button:disabled(.t)` and the 720px block are its entire
content, so the story is complete. I refreshed J8's expected list to
`[".seg button:last-child", "@media (max-width: 1180px)"]` with a comment saying why. That is the
only edit to any probe in this round; no other probe or test was changed.

**Counts at 1f535aa**

| Suite | Result |
|---|---|
| Full suite, `-O` | **3692 passed, 13 skipped** (133 s) |
| Full suite, no `-O` | **3692 passed, 13 skipped** (131 s) |
| ui-probes (`-O`, `-p no:randomly`) | **663 passed, 0 failed** (484 s) — 0 red by id |
| ui-restyle-probes (`-O`, `-p no:randomly`) | **265 passed, 5 failed, 10 skipped** (223 s); with J8 refreshed: **266 passed, 4 failed, 10 skipped** |

Red in the new suite after the J8 refresh, by test id: `test_r_c_colour.py::test_C5_…[search_ok]`
(finding 5), `test_r_e_keychip.py::test_E1_…` (3), `test_r_e_keychip.py::test_E3_…` (4),
`test_r_l_retest.py::test_L3b_a_disabled_card_does_not_offer_a_pointer_cursor` (9, pre-existing).
Green: `test_r_l_retest.py` 11/12 (L1 ×2, L2 ×6, L3, L4 ×3); everything that was red for
findings 1, 2, 6, 7, 8 is green.

## Re-test: 257960a

Head 257960a: fix E1 (999fff3, `src/ui/static/app.js` run-details key line), fix L3b (1143dff,
`.seg button:disabled { cursor: not-allowed }`). Probes: `test_r_m_retest2.py` (16 tests, new).

- **Finding 3 (Medium, E1) — fixed.** Attacked in four states, Trip B each time, run-details
  opened and read from the DOM (`test_M1–M4`): OFFLINE with a key → `Seats.aero key: not used
  (offline: no transport)   (source: environment)`; REPLAY → `Seats.aero key: not required
  (--from-snapshot replays committed bytes)   (source: environment)`; LIVE against the stub →
  `Seats.aero key: (masked key not sent to the browser)   (source: environment)`; OFFLINE with
  no key → the base's line with **no** `(source:` suffix and the bar chip visible as
  `key: not found`. Exactly one key line each; the text before the suffix is byte-identical to
  386b2fc's three lines (`test_M4`); the fake key and its mask are nowhere in `page.content()`
  or the transcript in any of the four (`test_M3`). `test_E1` green.
- **Finding 9 (Low, L3b) — fixed.** `test_L3b` green: both disabled cards report
  `cursor: not-allowed`; `test_L3` still green (opacity .8, muted title, disabled reads as
  disabled in grayscale).
- **Open by decision**: Finding 4 (`test_E3`, chip and LIVE card disagree for one render after
  the key changes mid-session) and Finding 5 (`test_C5`, two coral primaries on Search with the
  drawer open). Both still red, unchanged.
- **My own probes, edited (housekeeping asked for by the coordinator)**: (a) screenshots now go
  to `docs/test-reports/ui-restyle-probes/shots-out/` (gitignored) so a run no longer rewrites
  the committed `shots/`, which stays as the record cited above — one line in `conftest.py`
  plus `.gitignore`; (b) `test_J6` counted the app.js hunks since 386b2fc (5) and their added
  string literals — fix E1 is a 6th hunk with the base's three key-line strings re-split and
  the `(source: ` suffix; I read the `1f535aa..257960a` app.js diff (that one hunk is its whole
  content) and refreshed J6's hunk count and allowed-string list with a comment. No other
  probe or test changed.

**Counts at 257960a**

| Suite | Result |
|---|---|
| ui-restyle-probes (`-O`, `-p no:randomly`) | **284 passed, 2 failed, 10 skipped** (234 s) — red: `test_C5[search_ok]` (5), `test_E3` (4), both open by decision |
| `tests/test_ui_static_rules.py` (`-O`) | **82 passed** |

(Full suite and ui-probes were last run at 1f535aa — 3692/13 both ways, 663/0 — and the two
commits since touch only `app.js:768-777`, one `app.css` declaration and docs; the static rules
that cover both files are green.)
