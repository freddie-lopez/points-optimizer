# Plan: restyle the local UI to direction C ("Graphite")

Architect, 2026-09-13, on feature/ui-restyle (forked from feature/ui at 386b2fc).
Inputs: docs/design/UI-BRIEF.md (binding: §2, §4 "what prettier may change", §5, §8),
docs/design/restyle-ref/{tokens-c.txt, DirectionC.dc.html, DirectionC.png},
docs/plans/ui.md §4.7/§4.8, src/ui/static/*, tests/test_ui_static_rules.py,
tests/test_ui_security.py, docs/test-reports/ui-probes/.

## 1. Summary

Only the look changes. app.css is rewritten on a new token sheet (blue-black ground,
blue accent for identity, coral for the one primary action, Figtree + Red Hat Mono);
index.html swaps the font link and the key-source span's initial state; app.js gets
four small, enumerated edits (key-source chip, run-strip row structure, trip-row kind
class, nothing else). Every user-facing sentence, every §2 marker, every data-testid,
the CSP and the security model are untouched. Two inherited to-dos ride along: #3 (run
strip vs a long REPLAY reason) is fixed by the strip's new structure, #4 (trip kinds
look alike) by per-kind row forms that use only the captions already rendered. One
behaviour change, decided by Tsuki: the top-bar key chip is hidden when a key is found
and shows `key: not found` in warn form when it is not.

## 2. Assumptions & decisions

Nobody could be asked this turn; each of these is a call, stated so it can be reversed.

1. **Token names change** (semantic names, not `--oxblood: #7DA9FF`). Old→new is a
   mechanical rename in app.css (§4.1). `tests/test_ui_static_rules.py::
   test_the_design_tokens_are_the_plans` pins the old hex values and **must be updated
   to the new sheet** — it is a unit test of "the CSS matches the plan", not a tester
   probe, and the plan is what is changing. The manager must see this edit; it is the
   only test file this round touches.
2. **Chips keep their forms and their rectangular radius (4px).** The mockup draws the
   top-bar pills at 999px; the semantic chips (POINTS fill, CASH-qualified 3px left edge,
   WITHHELD hatch, UNKNOWN dashed, UNVERIFIED small caps) stay rectangular so the left
   edge and the hatch read as edges and hatches. Only the mode pill and wallet chips go
   fully round.
3. **Section labels are 11px / .06em** as in the approved DirectionC.dc.html, not the
   12px the task text says; the mockup is the artefact Tsuki approved. (Cheap to flip.)
4. **Table rows stay dense**: 32px rows, 6px 10px cell padding. The mockup's 12px 14px
   padding would cost the middle of the legs table at 400px, which probe K27 pins at
   ≥100px. Only the header band adopts the mockup (raised bg, 11px uppercase .06em).
5. **Cash figures are never green.** The PNG renders `USD 150.00` in `--win`; that is a
   canvas artefact (the .dc.html sets no colour on it). Cash is `--text` mono. Brief §2.
6. **Glow**: `body` background = `--glow` over `--bg`; the sticky top bar becomes
   `rgba(15,20,27,.88)` + `backdrop-filter: blur(10px)` so the glow shows through at the
   top and the bar stays legible when scrolled. The old "no gradients" rule is amended by
   direction C for exactly two gradients: the glow and the WITHHELD hatch.
7. **No icons this round.** The mockup's warning triangles and the Run play glyph need
   SVG built in app.js; that is more JS than the restyle warrants. Wordmark mark is a CSS
   `::before` square. Icons can be a follow-up under the brief's "SVG only" rule.
8. **Key-source chip**: element stays in index.html (TESTIDS scans the HTML), starts
   `hidden` with empty text; `renderTopbar` fills it only in the not-found state.
   `/api/state` is unchanged. No test or probe asserts the found-state text (verified:
   `key-source` appears only in test_ui_static_rules TESTIDS and index.html; drive.js
   snapshots `.topbar` text but no probe asserts on it).
9. **Mode selector** becomes three cards (mockup) — CSS on the existing `.seg button`s.
   Same buttons, same testids, same `aria-pressed`; disabled card at .55 opacity.
10. **Old mockup**: `docs/design/ui-mockup.html` is re-skinned in place to C (same
    token/selector edits applied to its copy of the stylesheet) so docs/plans/ui.md's
    addendum pointer stays true. DirectionC.dc.html remains the approved reference for
    the Trips screen and wins on any disagreement.
11. **ui.md §4.7** is rewritten to the C system (it is the design system, and it is what
    changed). **§4.8** text stays; the `Key [key-source]` row gets a one-line decision
    note. UI-BRIEF.md §4 gets a one-line "superseded by direction C, see this plan"
    pointer, nothing else.
12. **Focus ring stays 2px** (probes H/K assert `outlineWidth == "2px"`), colour → accent.
13. **Fonts**: Google Fonts stays the single external request; CSP unchanged
    (`style-src … fonts.googleapis.com; font-src fonts.gstatic.com`). README already
    says "two webfonts" without naming them — no README change.

## 3. Out of scope

Any user-facing wording; engine/CLI/server/serializer changes; new dependencies, build
steps or external requests; SVG iconography; brief to-dos #1, #2, #5, #6, #7, #8; the
tester's probes and reports (docs/test-reports/**) — never edited to pass; light theme.

## 4. Architecture

### 4.1 Token sheet (app.css `:root`) — old → new

| Old | New | Value | Used for |
|---|---|---|---|
| `--ink` | `--bg` | `#0F141B` | page ground, transcript/echo/cli pre, math cells |
| `--coal` | `--panel` | `#171D26` | panels, table body, drawer, modal, mode cards |
| `--ash` | `--raised` | `#1F2733` | inputs, hover rows, selected rows, table header band |
| `--seam` | `--line` | `#2A3442` | every hairline |
| `--bone` | `--text` | `#E9EDF3` | primary text |
| `--smoke` | `--muted` | `#8C98A8` | labels, secondary text, dim |
| `--oxblood` | `--accent` | `#7DA9FF` | wordmark mark, active tab, selected trip/row, active mode card, IATA codes, links, focus ring, input focus |
| — | `--accent-ink` | `#0B1220` | text on an accent fill (LIVE pill) |
| — | `--accent-tint` | `rgba(125,169,255,.10)` | selected trip, active mode card, selected search cell |
| `--ember` (primary hover) | `--accent2` | `#FF8A65` | the ONE primary action: `.btn-primary` (RUN, Run search, Spend up to N calls, Write, Apply, Score against a fare) |
| — | `--accent2-ink` | `#1A0E08` | text on coral |
| — | `--accent2-hover` | `#FFA184` | `.btn-primary:hover` |
| `--warn` | `--warn` | `#F0B85A` | caution text, chip borders, flags, exit-warn |
| — | `--warn-bg` | `#2B2314` | warn fills (wallet-error chip, banner-error, no-wallet, broken trip row) |
| — | `--warn-line` | `#6A5320` | border of warn fills |
| `--win` | `--win` | `#5FD3A0` | POINTS chip only. Never a cash figure |
| `--unknown` | `--unknown` | `#8791A0` | the dashed UNKNOWN border |
| `--sans` | `--sans` | `"Figtree", -apple-system, "Helvetica Neue", Arial, sans-serif` | |
| `--mono` | `--mono` | `"Red Hat Mono", ui-monospace, "SF Mono", Menlo, monospace` | |
| — | `--radius` | `6px` | panels, buttons, inputs, cards, modal |
| — | `--radius-chip` | `4px` | `.chip`, `.runchip`, `.x` |
| — | `--glow` | `radial-gradient(900px 260px at 18% 0%, rgba(125,169,255,.16), transparent 70%)` | `body` |
| `--s12…--display` | unchanged | | |

Points fill → `rgba(95,211,160,.16)`; WITHHELD hatch → `repeating-linear-gradient(135deg,
rgba(240,184,90,.16) 0 3px, transparent 3px 7px)`; selected search cell → `--accent-tint`;
scrim → `rgba(15,20,27,.78)`; drawer shadow → `0 0 0 1px var(--line), -12px 0 32px rgba(0,0,0,.55)`.

CSS comments may cite `docs/design/restyle-ref/…`; they must not contain `docs/plans/`,
`docs/test-reports/`, `vN`, `Step N` or `[HCML]-N` (test_no_changelog rules apply to app.css).

### 4.2 Selectors that change (app.css) — by block

- **Globals**: drop every `font-stretch: 125%` (Figtree is not variable-width). `.label`
  → 11px 600 .06em uppercase `--muted`. `a`, `:focus-visible` → `--accent` (2px, offset 2px).
  `body` gets the glow. `.mono` unchanged.
- **Top bar**: `.topbar` translucent + blur (D6); `.topbar-in` min-height 48px, gap 28px.
  `.wordmark` → 16px 600, no uppercase/letterspacing, `::before` 10×10 3px-radius
  `--accent` square; `.wordmark span` → inherit (no dimmed second word). `.tab` → 14px 500
  sentence case, min-height 46px, active = `--text` + 2px `--accent` underline.
  `.pill` → 999px, 12px 500 sans (not mono), 24px tall: `-live` accent fill/`--accent-ink`;
  `-replay` 1px `--text` outline; `-offline` `--muted` outline+text; `-unavailable` `--warn`
  outline+text. `.wallet` → 999px, mono 12px; `.wallet.warn` → `--warn` on `--warn-bg`,
  `--warn-line` border (chip-warn form). New `.keysrc` (12px `--muted`) and `.keysrc.warn`
  (chip-warn form, 999px) for the key chip.
- **Banners**: `.banner-error`, `.no-wallet` → `--warn-bg` fill, 1px `--warn-line`, radius
  6px, text `--text`; keep the 3px left edge (form, not just colour).
- **Chips**: recolour only, radius `--radius-chip`; forms exactly as the brief's table.
  `.chip-neutral`/`.chip-notfund` border `--line`. `.tag-unv` unchanged but `--warn`.
- **Buttons**: `.btn` → 36px tall, 13px 600 sentence case, `--panel` on 1px `--line`,
  radius 6px; `.btn-primary` → `--accent2`/`--accent2-ink`, hover `--accent2-hover`;
  `.btn-small` → 28px. `.field input/select` → `--raised`, radius 6px, focus `--accent`.
- **Mode selector**: `.seg` → `display:flex; gap:12px; border:0; overflow:visible; flex-wrap:
  wrap`; `.seg button` → card: `flex: 1 1 200px; max-width: 420px; padding: 14px 16px;
  border:1px solid var(--line); border-radius: var(--radius); background: var(--panel);
  border-right` removed; `.t` → sans 13px 600 .04em; `.s` 12px `--muted`;
  `[aria-pressed=true]` → border+inset ring `--accent`, bg `--accent-tint`, `.t` in
  `--accent`; `:disabled` → opacity .55 (override the global .45 for this control).
- **Run strip (to-do #3)**: `.runstrip` → `display:grid; gap:12px; padding:16px`. New
  `.runrow` → `display:flex; align-items:center; gap:12px; flex-wrap:wrap`; `.runrow
  .mode-note` → `flex: 1 1 240px; min-width:0; margin:0; overflow-wrap:anywhere`; new
  `.runacts` → `display:flex; gap:8px; margin-left:auto; flex:none`. The note can never
  push the buttons out because it is the only flexible item and the buttons are
  `flex:none`; at 400px the note takes a full line and the buttons wrap under it, right-
  aligned. `.mode-note .path` keeps `overflow-wrap:anywhere` for a 200-char single-segment
  path (`shortPath` only shortens paths with ≥3 segments).
- **Trip list (to-do #4)**: `.trow` → padding 12px 14px, radius 6px, no left edge; hover
  `--panel`; `[aria-current=true]` → `--accent-tint` bg, 1px `--line` border, `.n` in
  `--accent`. Per-kind forms via classes app.js adds (§4.3 #3): `.trow-trip` plain;
  `.trow-search` → 3px `--line` left rail + `.n` weight 400 `--muted` (a request, not a
  trip); `.trow-broken` → `--warn-bg` fill + 1px `--warn-line` border. All three keep
  their existing captions (`NOT A PER-LEG TRIP`, `CANNOT LOAD`, flags) — the words are
  the non-colour signal, the form is the second one. Not dashed: dashed means UNKNOWN.
- **Tables**: `.tscroll` radius 6px; `table.grid th` → `--raised` band, 11px .06em, padding
  8px 10px; `td` unchanged density; hover `--raised`; selected row → `--raised` + inset
  3px `--accent`; `.pin-left/.pin-right` backgrounds follow `--panel`/`--raised` (K26 needs
  an opaque rgb(); keep `background: inherit` on td and `tr { background: var(--panel) }`).
  `.route` → `--accent` (IATA codes), `.route .arr` `--muted`.
- **Headline / kv / prov / notes / folds / drawer / modal / forms / search**: token rename
  only, radius 6px on boxes, `.modal` top edge 2px `--accent`. `.hl-value` unchanged
  (28px mono; a range is two numbers, one element).
- **Media queries**: unchanged breakpoints (1180, 720). Add nothing that widens the page
  at 400px.

### 4.3 app.js edits — exactly these, nothing else

1. **Key-source chip — `renderTopbar`, line 239.** Replace the one assignment with:
   found → `ks.textContent = ""; ks.hidden = true; ks.className = "keysrc"`;
   not found → `ks.textContent = "key: not found"; ks.hidden = false;
   ks.className = "keysrc warn"`. The preflight box (line 759) already prints the source.
2. **Run strip row — `renderRunStrip`, lines 484–523.** Create `var row = el("div",
   "runrow")` and `var acts = el("div", "runacts")`. Line 492 `add(field, note)` →
   `add(row, note)`. Line 519 `add(strip, optBtn, runBtn)` → `add(acts, optBtn, runBtn);
   add(row, acts)`. Lines 520–523: the busy note goes into `row` before `acts` (so it
   sits in the same line as the buttons). Then `add(strip, row)` after the manifest
   select block (line 512) — so the strip's column is: field (label + cards) · key-error
   fold · manifest select · runrow. Testids `replay-unavailable`, `run-options`, `run-go`,
   `run-busy`, `key-error`, `replay-manifest` unchanged. `startBusy`'s ticker finds
   `run-busy` by testid — unaffected.
3. **Trip row kind — `renderTripList`, after line 352.** One line:
   `b.classList.add(t.load_error ? "trow-broken" : (t.no_legs_note ? "trow-search" : "trow-trip"));`
4. Nothing else. No wording, no testid, no `innerHTML`, no new inline `style`.

### 4.4 index.html

Line 9: font link → `https://fonts.googleapis.com/css2?family=Figtree:wght@400;500;600&amp;family=Red+Hat+Mono:wght@400;500&amp;display=swap`.
Line 25: `<span class="keysrc" id="key-source" data-testid="key-source" hidden></span>`.
Nothing else (no inline script, no inline style, the token meta stays).

### 4.5 Docs

- `docs/plans/ui.md` §4.7 rewritten to §4.1/4.2 above (tokens, type, chips, mode pill,
  layout: 48px bar, radius 6px, header band). §4.8 G-row `Key [key-source]`: append
  "Decision (restyle): rendered only when `key.found` is false, as `key: not found` in
  warn form; when found the element is hidden and the run-details box carries the
  source." No other §4.8 text moves.
- `docs/design/ui-mockup.html`: re-skinned in place (D10).
- `docs/design/UI-BRIEF.md` §4: one pointer line (D11).
- `tests/test_ui_static_rules.py::test_the_design_tokens_are_the_plans`: the twelve
  needles become `--bg: #0F141B`, `--panel: #171D26`, `--raised: #1F2733`, `--line:
  #2A3442`, `--text: #E9EDF3`, `--muted: #8C98A8`, `--accent: #7DA9FF`, `--accent2:
  #FF8A65`, `--warn: #F0B85A`, `--win: #5FD3A0`, `--unknown: #8791A0`, `--display: 28px`.

## 5. Tech choices

| Choice | Why | Rejected |
|---|---|---|
| Rename tokens semantically | `--oxblood: #7DA9FF` would lie to the next reader; the rename is a sed over 282 lines | keep names, change values |
| CSS-only mode cards on the existing buttons | same DOM, same testids, same keyboard behaviour | rebuilding the selector in JS |
| One `.runrow` wrapper for note + buttons | the only structure in which a long reason cannot displace RUN; 3 lines of JS | grid tricks on the current flat structure (the note lives inside `.field`, so CSS alone cannot put it beside the buttons) |
| Kind classes on `.trow` | one line of JS; forms in CSS; captions untouched | new caption text (forbidden), icons (JS SVG) |
| Translucent blurred top bar | keeps the glow visible at the top and the bar readable when scrolled | opaque bar (kills the glow), transparent bar (unreadable over tables) |
| Google Fonts via the existing `<link>` | already the single allowed external request; CSP already permits it | self-hosting (new binary files, "no dependencies") |

## 6. Build steps

Each step names its files and its acceptance. Do not run the full suite until step 8
(a baseline run is in progress); `tests/test_ui_static_rules.py` alone is cheap and runs
after every step.

1. **Tokens + fonts.** app.css `:root` → §4.1 sheet; sed old→new names through the file;
   `body` glow; index.html font link; test_ui_static_rules token needles updated.
   *Accept*: page loads, is blue-black with Figtree/Red Hat Mono, no `--ink|--coal|--ash|
   --seam|--bone|--smoke|--oxblood|--ember` left in app.css (grep = 0);
   `pytest tests/test_ui_static_rules.py -q` green.
2. **Type and chrome.** Drop `font-stretch`; `.label`, `.wordmark` (+`::before` mark), `.tab`,
   `.topbar` 48px translucent, `.pill*`, `.wallet*`, `.keysrc`, `.btn*`, `.field` inputs,
   radius tokens. *Accept*: tabs/buttons sentence case; LIVE pill accent-filled with dark
   text; RUN is coral and is the only coral thing on the Trips screen; focus ring 2px accent.
3. **Key-source behaviour.** app.js #1, index.html line 25, `.keysrc.warn`.
   *Accept*: with `POINTS_OPTIMIZER_SEATS_AERO_KEY` set the span is `hidden` with empty
   text and the run-details box still says `(source: environment)`; unset, the bar shows
   `key: not found` in warn form; `/api/state.key` byte-identical to before.
4. **Chips and semantic colour.** `.chip*`, `.tag*`, `.seg-*`, `.warnline`, `.flags`,
   `.refusal`, `.funding`, `.banner-error`, `.no-wallet`. *Accept*: on Trip B offline, the
   four forms are visible and distinct with colour removed (screenshot → grayscale):
   POINTS filled, `PAY CASH (…)` with a thick left edge, WITHHELD hatched, UNKNOWN dashed;
   `unverified` small caps in the fixture legs table.
5. **Mode cards + run strip (to-do #3).** `.seg`, `.seg button`, `.runstrip`, `.runrow`,
   `.runacts`; app.js #2. *Accept*: with `replay_reason_detail` stubbed to a 200-char
   sentence and a 120-char path (a) with slashes, (b) without, at 1440 and 400px: RUN and
   Options remain fully inside `run-strip`'s box, no page-level horizontal scroll
   (`document.body.scrollWidth <= 400`), the note's `title` holds the whole path.
6. **Trip list (to-do #4).** `.trow*`; app.js #3. *Accept*: a fixture dir holding a
   per-leg trip, `trip_001` (search request) and a broken JSON file shows three row forms
   (plain / left rail / warn fill) whose captions are unchanged; grayscale still tells
   them apart; the selected row is tinted with its name in accent.
7. **Tables, headline, drawer, modal, search, forms.** Remaining blocks of §4.2.
   *Accept*: header band raised; `IATA → IATA` in accent mono; cash figures in `--text`;
   drawer sticky ≥1180 / fixed below (H probe); pinned cells report an opaque `rgb(…)`.
8. **Docs + mockup.** ui.md §4.7/§4.8 note, UI-BRIEF pointer, ui-mockup.html re-skin.
   *Accept*: ui-mockup.html opened offline renders in C with system fallbacks; no
   `docs/plans/` or `vN` strings in anything under src/ui/static.
9. **Full verification** (after the background baseline finishes): full suite, the same
   under `-O`, then the ui-probes suite (~11 min). *Accept*: 3,692 passed / 13 skipped
   both ways; ui-probes 0 red by test id.

## 7. Testing strategy — what the tester attacks

- **§2 markers in the DOM, not colour-only.** For every ui-probe scenario, every marker
  the CLI prints for a leg (UNKNOWN, NOT $0, WITHHELD, the range + its label, UNVERIFIED,
  NOT LOOKED UP, NOT RECORDED, NEVER PRICED, API FAILED, UNREADABLE, badge, snapshot,
  CONFIRM BEFORE TRUSTING, REPARSED, exit N) is in the rendered DOM text. Then the
  grayscale test: a screenshot with saturation removed must still show the four chip forms
  and the mode pill's state. The headline value and its qualifier are one element.
- **Chip forms by computed style**: `.chip-unknown` `border-style: dashed`;
  `.chip-cashq` `border-left-width: 3px`; `.chip-withheld` `background-image` contains
  `repeating-linear-gradient`; `.chip-points` a non-transparent background; `.tag-unv`
  `font-variant-caps: all-small-caps`. Assert against Trip B offline and the couple trip.
- **Run strip, to-do #3**: 200-char reason, 120-char path with and without `/`, at 1440
  and 400; also with `S.busy` true (note + "Scoring… N s" + buttons on one row) and with
  the key-error fold open. `bodySW <= 400`; `run-go` inside `run-strip`'s box.
- **Trip list, to-do #4**: the three kinds side by side; captions unchanged; classes
  `trow-trip|trow-search|trow-broken` present; `aria-current` still drives selection.
- **Offline fonts**: block fonts.googleapis.com and fonts.gstatic.com (drive.js already
  aborts offsite) — page renders in system fallbacks with no layout break at 400/1440,
  no console error, no other offsite request (H13 stays: only fonts.googleapis.com).
- **Security unchanged**: `test_ui_security.py` whole; static rules (no innerHTML etc.,
  no inline script/handler, only `/static/app.js` + `/static/app.css` + fonts CSS);
  CSP header identical to 386b2fc; token never in app.css/app.js.
- **Key source, both states**: with a key — `key-source` hidden, `textContent === ""`,
  run-details line names the source; without — `key: not found` visible, warn class,
  `mode-pill` reads `LIVE UNAVAILABLE` on Search and `mode-live` disabled with the
  `key-error` fold. `/api/state` unchanged in both.
- **Focus and keyboard**: ring 2px (probes H/K), Enter/Esc/close-button focus return
  (L16–L19) at 1440 and 400.
- **Pinned columns**: K25–K27 at 400 and 1440 (opaque `rgb()` backgrounds, ≥100px middle).
- **Wording diff**: `git diff 386b2fc -- src/ui/static/app.js | grep '^[-+].*"'` shows
  no changed string literal except `"key: not found"`/class names; parity suite and
  goldens untouched.
- **Full suite under `-O`** and the ui-probes suite, compared by test id.

## 8. Open risks

- Figtree/Red Hat Mono metrics differ from Archivo/Plex: 400px layouts that were tight
  (search strip, new-trip row, K27's middle) may shift a few px. Measure, don't assume.
- `backdrop-filter` on the top bar over a sticky table header: two stacking contexts;
  check the header band does not paint over the bar when scrolled (z-index 20 vs sticky th).
- Removing the trip row's 3px selected edge relies on tint + accent name + `aria-current`;
  if grayscale loses the selection, restore the edge in `--accent`.
- The mode cards at 400px wrap to one per row: the strip gets tall; the busy note and
  buttons still fit but the fold-out options panel below pushes the legs table down.
- ui-mockup.html's re-skin is by hand and may drift from app.css; DirectionC.dc.html is
  the tie-breaker, and the mockup carries no test.

## 9. The 3 decisions most likely to be wrong

1. **Renaming the tokens** (D1) rather than keeping the names. It touches every line of
   app.css and the token test; if Tsuki wanted a minimal diff, revert to old names with
   new values (§4.1 maps both ways).
2. **Hiding the found-state key chip completely** (D8) rather than rendering an empty,
   visible placeholder. If a probe or a future reader expects `key: <source>` in the bar,
   the information is only in the run-details fold, which is collapsed by default.
3. **Cards-with-`.runrow` for the strip** (D9 + app.js #2) instead of a CSS-only fix. It
   moves the `replay-unavailable` node out of `.field`. No current probe references that
   testid or walks `.field` (verified by grep), so the risk is the tester's new probes
   or a screenshot diff, not the existing suite — and the 200-char path case has never
   been exercised by a probe at all, so the "fix" may still be wrong at 400px.
