/* Points Optimizer - local UI. Vanilla JS, no build step, no framework.

   THE RULES THIS FILE KEEPS (the UI plan's security and design sections):
   * The DOM is built with createElement + textContent ONLY. No HTML strings.
   * Every word about a trip comes from the API, which gets it from the CLI's own
     builders or verbatim terminal lines. This file never re-words a verdict.
   * An unknown arrives as null with a status or a `kind` beside it, and is drawn
     as the UNKNOWN chip - never blank, never a dash, never $0. A cell's chip is
     chosen by its `kind`, never by reading its text.
   * A headline number is never drawn without its qualifier. */
(function () {
  "use strict";

  var TOKEN = (document.querySelector('meta[name="po-token"]') || {}).content;
  var CALLS_NOTE = "Seats.aero also counts your other runs today, which this tool cannot see.";
  var CALLS_DAY_NOTE = "Calls since this server started; the daily budget beside it is Seats.aero's and resets at midnight.";
  var UNV_WORD = "unverified";

  var S = {
    state: null,
    view: "trips",
    trips: null,
    tripId: null,
    trip: null,
    tripError: null,
    mode: "offline",
    manifestId: 0,
    opts: { transfer_date: "", flex_days: "0", trips: "auto", trips_cap: "10", refresh: false },
    optsOpen: false,
    runs: {},          // trip id -> [run JSON, newest last]
    runId: null,
    legSel: null,
    // The testid of the element a drawer was opened from.
    cameFrom: null,
    busy: false,
    busyStart: 0,
    busyText: "",
    search: { from: "", to: "", date: "", dateTo: "", cabin: "All", errors: {}, run: null,
              sel: null, busy: false },
    nt: null,          // new-trip form state
    walletOpen: false
  };

  /* ------------------------------------------------------------------ DOM */

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) { n.className = cls; }
    if (text !== undefined && text !== null) { n.textContent = String(text); }
    return n;
  }
  function add(parent) {
    for (var i = 1; i < arguments.length; i++) {
      var c = arguments[i];
      if (c === null || c === undefined || c === false) { continue; }
      if (Array.isArray(c)) { for (var j = 0; j < c.length; j++) { add(parent, c[j]); } continue; }
      parent.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    }
    return parent;
  }
  function clear(n) { while (n.firstChild) { n.removeChild(n.firstChild); } return n; }
  function $(id) { return document.getElementById(id); }
  function tid(n, id) { n.setAttribute("data-testid", id); return n; }
  function btn(cls, text, onClick, testid) {
    var b = el("button", cls, text);
    b.type = "button";
    if (onClick) { b.addEventListener("click", onClick); }
    if (testid) { tid(b, testid); }
    return b;
  }
  function isNum(x) { return typeof x === "number" && isFinite(x); }
  function fmtInt(n) { return isNum(n) ? Math.round(n).toLocaleString("en-US") : null; }
  // The tail of a path, for somewhere a whole one will not fit. Never the only
  // copy: every caller puts the whole path in the element's title.
  function shortPath(path) {
    var parts = String(path || "").split("/").filter(function (x) { return x !== ""; });
    return parts.length <= 2 ? String(path) : "…/" + parts.slice(-2).join("/");
  }
  // The confirm dialog says which count is which, for the same reason.
  function callsSentence(c) {
    return "Calls since this server started: " + fmtInt(c.since_launch) +
      ". Spent today against Seats.aero's daily budget: " + fmtInt(c.spent_today) +
      " of " + fmtInt(c.cap) + " (it resets at midnight). " + CALLS_NOTE;
  }

  /* rich style -> class (4.7 "Segments") */
  function styleClass(style) {
    var s = String(style || "").replace(/^bold /, "").replace(/^italic /, "");
    if (s === "red") { return "seg-alert"; }
    if (s === "yellow") { return "seg-caution"; }
    if (s === "green") { return "seg-ok"; }
    if (s === "cyan") { return "seg-replay"; }
    if (s === "dim") { return "seg-dim"; }
    return "";
  }
  function segs(list, cls) {
    var wrap = el("span", cls || "");
    (list || []).forEach(function (sg) { add(wrap, el("span", styleClass(sg.style), sg.text)); });
    return wrap;
  }
  function styled(text, style, tag) { return el(tag || "span", styleClass(style), text); }

  function chip(kind, text, q) {
    var c = el("span", "chip chip-" + kind);
    add(c, text);
    if (q) { add(c, el("span", "q", q)); }
    return c;
  }
  function unknownChip() { return tid(chip("unknown", "UNKNOWN"), "unknown-chip"); }
  function unvTag(text) { return el("span", "tag-unv", text || UNV_WORD); }
  function flagTag(text) { return el("span", "tag-flag", text); }
  function section(title, testid) {
    var s = el("section", "dsec");
    add(s, el("div", "label", title));
    if (testid) { tid(s, testid); }
    return s;
  }
  function p(text, cls) { return el("p", cls || "", text); }
  function routeEl(o, d) {
    var s = el("span", "route");
    add(s, o || "", el("span", "arr", "→"), d || "");
    return s;
  }
  function cmdRow(text, testid) {
    var row = el("div", "cmdrow");
    var c = el("div", "cmd", "Equivalent command: " + text);
    if (testid) { tid(c, testid); }
    var copy = btn("btn btn-small", "Copy", function () { copyText(text, copy); });
    add(row, c, copy);
    return row;
  }
  function copyText(text, button) {
    function done(ok) { if (button) { button.textContent = ok ? "Copied" : "Select and copy"; } }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () { done(true); }, function () { done(false); });
    } else { done(false); }
  }

  /* ------------------------------------------------------------------ API */

  function api(method, path, body) {
    var opts = { method: method, headers: { "X-PO-Token": TOKEN }, credentials: "same-origin" };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    return fetch(path, opts).then(function (r) {
      return r.json().then(function (j) { return { status: r.status, body: j }; },
        function () { return { status: r.status, body: null }; });
    }, function () {
      showBanner("The app is not running. Start it again with .venv/bin/python -m src.ui");
      return { status: 0, body: null };
    }).then(function (res) {
      if (res.status === 403 && res.body && res.body.error === "stale_page") {
        showBanner(res.body.message);
      } else if (res.status === 409 && res.body && res.body.error === "busy") {
        showBanner(res.body.message);
      } else if (res.status === 500 && res.body && res.body.message) {
        showBanner(res.body.message);
      }
      return res;
    });
  }

  function showBanner(text) { var b = $("banner-error"); b.textContent = text; b.hidden = false; }
  function hideBanner() { $("banner-error").hidden = true; }

  function refreshState() {
    return api("GET", "/api/state").then(function (res) {
      if (res.status === 200) { S.state = res.body; }
      renderTopbar();
      renderNoWallet();
      return res;
    });
  }

  /* -------------------------------------------------------------- top bar */

  function currentRun() {
    if (!S.tripId || !S.runId) { return null; }
    var list = S.runs[S.tripId] || [];
    for (var i = 0; i < list.length; i++) { if (list[i].run_id === S.runId) { return list[i]; } }
    return null;
  }

  function nextMode() { return S.view === "search" ? "live" : S.mode; }

  function renderTopbar() {
    var st = S.state;
    if (!st) { return; }
    var pill = $("mode-pill");
    var mode = nextMode();
    if (mode === "live" && !st.key.found) {
      pill.className = "pill pill-unavailable"; pill.textContent = "LIVE UNAVAILABLE";
    } else {
      pill.className = "pill pill-" + mode; pill.textContent = mode.toUpperCase();
    }

    var calls = $("calls-counter");
    clear(calls);
    var shown = S.view === "search" ? S.search.run : currentRun();
    var since = st.calls.since_launch;
    add(calls, "CALLS ");
    if (shown && shown.calls) {
      var tr = shown.calls.this_run;
      if (shown.mode === "offline") {
        add(calls, el("b", "", "0"), " this run (offline: no transport) · ");
      } else if (shown.mode === "replay") {
        add(calls, el("b", "", "0"), " this run (replay asks nothing) · ");
      } else if (tr && isNum(tr.search) && isNum(tr.trips)) {
        add(calls, el("b", "", String(tr.search + tr.trips)), " this run · ");
      } else {
        add(calls, "this run: not measured · ");
      }
    }
    // Two numbers, not one. "since launch" is what THIS server has spent
    // and never resets; the budget it is spending is Seats.aero's daily one,
    // which does reset at midnight. Showing "N since launch / 1,000" read as
    // one number against the other and understated the server after midnight.
    add(calls, el("b", "", fmtInt(since)), " since launch · ");
    add(calls, el("b", "", fmtInt(st.calls.spent_today)), " of " + fmtInt(st.calls.cap) + " today");
    calls.title = CALLS_DAY_NOTE + " " + CALLS_NOTE;

    var chips = clear($("wallet-chips"));
    var w = st.wallet;
    if (w.missing) {
      add(chips, tid(btn("wallet warn", "NO WALLET", openWallet), "wallet-chip-none"));
    } else if (w.error) {
      add(chips, tid(btn("wallet warn", "WALLET ERROR", openWallet), "wallet-chip-error"));
    } else {
      Object.keys(w.balances).sort().forEach(function (cur) {
        var bal = w.balances[cur];
        var text = cur + " " + (bal === null ? "UNCONSTRAINED" : fmtInt(bal));
        var b = btn("wallet", text, openWallet, "wallet-chip-" + cur);
        b.title = "Wallet: " + (w.source || "");
        add(chips, b);
      });
    }
    $("key-source").textContent = "key: " + (st.key.found ? st.key.source : "not found");
    $("tab-trips").setAttribute("aria-selected", String(S.view !== "search"));
    $("tab-search").setAttribute("aria-selected", String(S.view === "search"));
  }

  function renderNoWallet() {
    var box = $("no-wallet");
    var w = S.state && S.state.wallet;
    if (w && w.missing) {
      box.textContent = "No wallet. The tool refuses to assume which cards and points you hold, " +
        "because a default wallet changes real answers. Add balances in the wallet panel, or " +
        "start the app with --wallet wallet.json.";
      box.hidden = false;
    } else { box.hidden = true; }
  }

  /* -------------------------------------------------------------- routing */

  function go(hash) { if (location.hash !== hash) { location.hash = hash; } else { onHash(); } }

  function onHash() {
    var h = decodeURIComponent(location.hash.replace(/^#/, ""));
    var parts = h.split("/");
    hideBanner();
    if (parts[0] === "search") {
      S.view = "search";
    } else if (parts[0] === "new-trip") {
      S.view = "new-trip";
      if (!S.nt) { S.nt = newTripState(); }
    } else {
      S.view = "trips";
      var id = parts[0] === "trips" && parts[1] ? parts[1] : null;
      var rid = parts[2] === "run" && parts[3] ? parts[3] : null;
      if (id && id !== S.tripId) {
        S.tripId = id; S.trip = null; S.tripError = null; S.legSel = null; S.cameFrom = null;
        S.runId = rid;
        loadTrip(id);
      } else {
        S.tripId = id;
        if (rid !== S.runId) { S.legSel = null; S.cameFrom = null; }
        S.runId = rid;
      }
    }
    render();
  }

  function render() {
    $("view-trips").hidden = S.view === "search";
    $("view-search").hidden = S.view !== "search";
    renderTopbar();
    if (S.view === "search") { renderSearch(); } else { renderTrips(); }
    setDrawerClass();
  }

  function setDrawerClass() {
    var d = S.view === "search" ? $("drawer-search") : $("drawer-trips");
    $("page").classList.toggle("has-drawer", !d.hidden);
  }

  /* ---------------------------------------------------------------- trips */

  function loadTrips() {
    return api("GET", "/api/trips").then(function (res) {
      if (res.status === 200) { S.trips = res.body; }
      renderTripList();
    });
  }

  function loadTrip(id) {
    api("GET", "/api/trips/" + encodeURIComponent(id)).then(function (res) {
      if (S.tripId !== id) { return; }
      if (res.status === 200) { S.trip = res.body; S.tripError = null; }
      else { S.trip = null; S.tripError = (res.body && res.body.message) || ("HTTP " + res.status); }
      renderTrips();
    });
  }

  function renderTrips() {
    renderTripList();
    var main = clear($("trip-main"));
    var drawer = $("drawer-trips");
    if (S.view === "new-trip") {
      drawer.hidden = true; clear(drawer);
      renderNewTrip(main);
      setDrawerClass();
      return;
    }
    if (!S.tripId) {
      add(main, tid(p("Pick a trip on the left, or build a new one with + New trip.", "note"),
        "trip-empty"));
      drawer.hidden = true; clear(drawer); setDrawerClass();
      return;
    }
    if (S.tripError) {
      add(main, tid(panelRefusal("This trip could not be loaded.", S.tripError), "trip-error"));
      drawer.hidden = true; setDrawerClass();
      return;
    }
    if (!S.trip) { add(main, p("Loading…", "note")); return; }
    renderTripHead(main, S.trip);
    renderRunStrip(main);
    var run = currentRun();
    renderRunChips(main);
    if (run) { renderResult(main, run); } else { renderFixtureLegs(main, S.trip); }
    renderLegDrawer(run);
    setDrawerClass();
  }

  function renderTripList() {
    var nav = clear($("trip-list"));
    add(nav, el("h2", "label", "Trips"));
    (S.trips || []).forEach(function (t) {
      var b = btn("trow", null, function () { S.view = "trips"; go("#trips/" + t.id); }, "trip-row-" + t.id);
      b.setAttribute("aria-current", String(S.view === "trips" && S.tripId === t.id));
      add(b, el("span", "n", t.name));
      if (t.load_error) {
        add(b, el("span", "flag", "CANNOT LOAD"), el("span", "m", t.load_error));
      } else {
        // A file with no legs is not a trip with nothing in it: say which it
        // is. "0 legs · 0 flights" reads as a finding about the trip.
        if (t.no_legs_note) {
          add(b, el("span", "flag", "NOT A PER-LEG TRIP"), el("span", "m", t.no_legs_note.short));
        } else {
          var m = t.legs + " legs · " + t.flights + " flights";
          if (t.hotels) { m += " · " + t.hotels + (t.hotels === 1 ? " hotel" : " hotels"); }
          add(b, el("span", "m", m));
        }
        if (t.live_only) { add(b, el("span", "flag", "NO POINTS PRICES — LIVE OR REPLAY ONLY")); }
        if (t.max_flight_travellers > 1) {
          add(b, el("span", "flag", "2+ TRAVELLERS ON A FLIGHT — NOT SCORED ON POINTS"));
        }
      }
      add(nav, b);
    });
    add(nav, btn("btn newtrip", "+ New trip", function () { S.nt = newTripState(); go("#new-trip"); },
      "new-trip"));
  }

  function renderTripHead(main, trip) {
    if (S.lastWrite && S.lastWrite.id === trip.id) {
      var wrote = tid(el("div", "panel funding"), "nt-wrote");
      S.lastWrite.lines.forEach(function (l) { add(wrote, el("div", "", l)); });
      add(main, wrote);
    }
    var head = tid(el("div", "trip-head"), "trip-detail");
    add(head, el("div", "label", "Trip"), el("h1", "", trip.name), el("div", "d", trip.description),
      el("div", "d", "Source: " + trip.source));
    add(main, head);
    if (trip.flags && trip.flags.length) {
      var f = tid(el("div", "panel flags"), "trip-flags");
      add(f, el("div", "label", "Data problems flagged, NOT silently fixed"));
      var ul = el("ul");
      trip.flags.forEach(function (x) { add(ul, el("li", "", x)); });
      add(f, ul);
      add(main, f);
    }
  }

  function renderFixtureLegs(main, trip) {
    // An empty legs table says nothing about why it is empty. The note comes
    // from the file itself and replaces the table rather than sitting over it.
    if (trip.no_legs_note) {
      var np = tid(el("div", "panel nolegs"), "fixture-no-legs");
      add(np, el("span", "flag", "NOT A PER-LEG TRIP"), p(trip.no_legs_note.text, "note"));
      add(main, np);
      return;
    }
    var wrap = tid(el("div", "tscroll"), "fixture-legs-scroll");
    var t = tid(el("table", "grid static"), "fixture-legs");
    var thead = el("thead"); var hr = el("tr");
    ["Leg", "What", "Date", "Travellers", "Cabin", "Cash in fixture", "Points prices in fixture"]
      .forEach(function (h) { add(hr, el("th", "", h)); });
    add(thead, hr); add(t, thead);
    var tb = el("tbody");
    trip.legs.forEach(function (leg) {
      var tr = el("tr");
      var what = el("td");
      if (leg.origin && leg.destination) { add(what, routeEl(leg.origin, leg.destination)); }
      add(what, el("span", "sub2", leg.description));
      var cash = el("td", "mono");
      var cheapest = null;
      leg.cash_options.forEach(function (c) {
        if (isNum(c.amount) && (cheapest === null || c.amount < cheapest.amount)) { cheapest = c; }
      });
      if (cheapest) {
        add(cash, cheapest.currency + " " + cheapest.amount.toLocaleString("en-US",
          { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
        add(cash, el("span", "sub2", cheapest.source === "unknown" ? "source unknown" : cheapest.source +
          (cheapest.captured_on ? " " + cheapest.captured_on : "")));
      } else { add(cash, unknownChip()); }
      var pts = el("td");
      if (leg.points_candidates.length) {
        var srcs = {};
        leg.points_candidates.forEach(function (c) { srcs[c.source] = true; });
        add(pts, leg.points_candidates.length + " · " + Object.keys(srcs).join(", "), " ");
        if (leg.points_candidates.some(function (c) { return c.unverified; })) { add(pts, unvTag()); }
      } else { add(pts, el("span", "dim", "none")); }
      var cab = el("td", "mono");
      if (leg.kind === "flight") { add(cab, leg.cabin ? leg.cabin : unknownChip()); } else { add(cab, el("span", "dim", "hotel")); }
      add(tr, el("td", "legid", leg.id), what, el("td", "mono", leg.date), el("td", "mono", leg.travelers),
        cab, cash, pts);
      add(tb, tr);
    });
    add(t, tb); add(wrap, t); add(main, wrap);
  }

  /* ------------------------------------------------------------ run strip */

  function tripListing() {
    var list = S.trips || [];
    for (var i = 0; i < list.length; i++) { if (list[i].id === S.tripId) { return list[i]; } }
    return null;
  }

  function renderRunStrip(main) {
    var st = S.state;
    var listing = tripListing();
    var strip = tid(el("div", "panel runstrip"), "run-strip");
    var field = el("div", "field");
    add(field, el("span", "label", "Mode of the next run"));
    var seg = el("div", "seg"); seg.setAttribute("role", "group"); seg.setAttribute("aria-label", "Mode");
    var manifests = (st && st.modes.replay_manifests) || [];
    function modeBtn(mode, testid, title, sub, disabled) {
      var b = btn(null, null, function () { S.mode = mode; renderTopbar(); renderTrips(); }, testid);
      b.setAttribute("aria-pressed", String(S.mode === mode));
      b.disabled = !!disabled;
      add(b, el("span", "t", title), el("span", "s", sub));
      return b;
    }
    var liveOk = st && st.key.found;
    var man = manifests[S.manifestId] || manifests[0];
    var noPts = listing && listing.fixture_has_points_prices === false;
    add(seg,
      modeBtn("live", "mode-live", "LIVE", liveOk ? "points from Seats.aero, cash from captures" :
        "LIVE needs a Seats.aero key.", !liveOk),
      modeBtn("replay", "mode-replay", "REPLAY", man ? "manifest " + man.label + " · " +
        (man.rows === null ? "UNREADABLE" : man.rows + " rows") + " · nothing is asked of Seats.aero" :
        (st ? st.modes.replay_reason : ""), !man),
      modeBtn("offline", "mode-offline", "OFFLINE", noPts ? "this trip has no points prices, so nothing will be scored on points" :
        "no transport: scores the fixture's own points prices", false));
    add(field, seg);
    // The three segments are one control and stay the same width, so the
    // reason REPLAY is unavailable - which carries a filesystem path of no
    // fixed length - goes on its own line under it. The path is shortened to
    // its last two parts; the whole of it is in the title, never dropped.
    var detail = !man && st && st.modes.replay_reason_detail;
    if (detail) {
      var note = tid(el("span", "mode-note"), "replay-unavailable");
      var parts = detail.text.split("{path}");
      add(note, parts[0]);
      var pathEl = el("span", "path", shortPath(detail.path));
      pathEl.title = detail.path;
      add(note, pathEl, parts.length > 1 ? parts[1] : "");
      add(field, note);
    }
    add(strip, field);
    if (!liveOk && st && st.key.error_text) {
      var ke = tid(el("details", "fold"), "key-error");
      add(ke, el("summary", "label", "Why LIVE is unavailable"), el("pre", "transcript", st.key.error_text));
      add(strip, ke);
    }
    if (S.mode === "replay" && manifests.length > 1) {
      var mf = el("label", "field");
      add(mf, el("span", "label", "Manifest"));
      var sel = tid(el("select"), "replay-manifest");
      manifests.forEach(function (m) {
        var o = el("option", "", m.label + (m.rows === null ? " (unreadable)" : " · " + m.rows + " rows"));
        o.value = String(m.id);
        if (m.id === S.manifestId) { o.selected = true; }
        add(sel, o);
      });
      sel.addEventListener("change", function () { S.manifestId = Number(sel.value); renderTrips(); });
      add(mf, sel); add(strip, mf);
    }
    var optBtn = btn("btn", S.optsOpen ? "Hide options" : "Options", function () {
      S.optsOpen = !S.optsOpen; renderTrips();
    }, "run-options");
    optBtn.setAttribute("aria-expanded", String(S.optsOpen));
    var runBtn = btn("btn btn-primary", S.busy ? "Running…" : "Run", doRun, "run-go");
    runBtn.disabled = S.busy || (S.mode === "live" && !liveOk) || (S.mode === "replay" && !man);
    add(strip, optBtn, runBtn);
    if (S.busy) {
      add(strip, tid(el("span", "note", S.busyText + " " +
        Math.round((Date.now() - S.busyStart) / 1000) + " s"), "run-busy"));
    }
    add(main, strip);
    if (S.optsOpen) { add(main, renderOptions()); }
  }

  function renderOptions() {
    var box = tid(el("div", "panel options"), "run-options-panel");
    var live = S.mode === "live";
    function f(label, input, hint) {
      var w = el("label", "field");
      add(w, el("span", "label", label), input);
      if (hint) { add(w, el("span", "hint", hint)); }
      return w;
    }
    var td = el("input", "date"); td.type = "text"; td.placeholder = "YYYY-MM-DD";
    td.value = S.opts.transfer_date || (S.state ? S.state.today : "");
    td.addEventListener("input", function () { S.opts.transfer_date = td.value; });
    tid(td, "opt-transfer-date");
    var fd = el("input"); fd.type = "number"; fd.min = "0"; fd.max = "7"; fd.value = S.opts.flex_days;
    fd.disabled = !live; tid(fd, "opt-flex-days");
    fd.addEventListener("input", function () { S.opts.flex_days = fd.value; });
    var tr = el("select"); tid(tr, "opt-trips"); tr.disabled = !live;
    ["auto", "all", "off"].forEach(function (m) { var o = el("option", "", m); o.value = m; if (S.opts.trips === m) { o.selected = true; } add(tr, o); });
    tr.addEventListener("change", function () { S.opts.trips = tr.value; });
    var cap = el("input"); cap.type = "number"; cap.min = "1"; cap.max = "50"; cap.value = S.opts.trips_cap;
    cap.disabled = !live; tid(cap, "opt-trips-cap");
    cap.addEventListener("input", function () { S.opts.trips_cap = cap.value; });
    var rf = el("input"); rf.type = "checkbox"; rf.checked = !!S.opts.refresh; rf.disabled = !live;
    tid(rf, "opt-refresh");
    rf.addEventListener("change", function () { S.opts.refresh = rf.checked; });
    add(box,
      f("Transfer date", td, "ratios and bonuses are looked up on THIS date, not on the travel date"),
      f("Flex days", fd, "awards on other dates are shown, never scored" + (live ? "" : " (LIVE only)")),
      f("Operating-airline lookup", tr, live ? "" : "LIVE only"),
      f("Lookup cap", cap, live ? "1-50 itinerary requests at most" : "LIVE only"),
      f("Refresh cache", rf, "re-fetches every search; spends calls" + (live ? "" : " (LIVE only)")));
    return box;
  }

  function runBody() {
    var o = { transfer_date: S.opts.transfer_date || (S.state ? S.state.today : "") };
    if (S.mode === "live") {
      o.flex_days = S.opts.flex_days; o.trips = S.opts.trips; o.trips_cap = S.opts.trips_cap;
      o.refresh = !!S.opts.refresh;
    }
    if (S.mode === "replay") { o.manifest_id = S.manifestId; }
    return { mode: S.mode, options: o };
  }

  function startBusy(text) {
    S.busy = true; S.busyStart = Date.now(); S.busyText = text;
    var tick = function () {
      if (!S.busy) { return; }
      var n = document.querySelector('[data-testid="run-busy"]') ||
        document.querySelector('[data-testid="search-state"]');
      if (n && S.busyText) {
        n.textContent = S.busyText + " " + Math.round((Date.now() - S.busyStart) / 1000) + " s";
      }
      window.setTimeout(tick, 1000);
    };
    window.setTimeout(tick, 1000);
  }
  function stopBusy() { S.busy = false; S.busyText = ""; }

  function doRun() {
    if (S.busy || !S.tripId) { return; }
    var id = S.tripId;
    var body = runBody();
    api("POST", "/api/trips/" + encodeURIComponent(id) + "/preflight", body).then(function (res) {
      if (res.status !== 200) { showBanner((res.body && res.body.message) || ("HTTP " + res.status)); return; }
      var pf = res.body;
      if (pf.mode === "live") {
        if (pf.blocked) { showBanner(pf.blocked.message); return; }
        openConfirm(tripConfirmText(pf), function () {
          body.confirm_id = pf.confirm_id; sendRun(id, body);
        });
      } else { sendRun(id, body); }
    });
  }

  function tripConfirmText(pf) {
    var b = pf.breakdown;
    var why = b.flight_legs + " flight-leg search" + (b.flight_legs === 1 ? "" : "es") +
      " × up to " + b.pages_per_search + " pages each" +
      (b.lookup_cap ? " + up to " + b.lookup_cap + " itinerary lookups" : "") +
      ". Cache hits are free and are not counted. " + callsSentence(pf.calls);
    var lines = [];
    if (isNum(pf.cache_answerable)) {
      lines.push("The disk cache can answer " + pf.cache_answerable + " of the " + b.flight_legs +
        " searches right now (fetched within 6h).");
    }
    if (pf.archive_dir) { lines.push("Snapshots are archived to " + pf.archive_dir + "."); }
    return { lead: "This run can spend at most", n: pf.max_calls, why: why, extra: lines,
      cmd: pf.argv_display, go: "Spend up to " + pf.max_calls + " calls", testid: "run-confirm",
      goid: "run-confirm-go" };
  }

  function sendRun(id, body) {
    startBusy(body.mode === "live" ? "Asking Seats.aero… nothing is shown until the whole answer is in." :
      "Scoring…");
    renderTrips();
    api("POST", "/api/trips/" + encodeURIComponent(id) + "/run", body).then(function (res) {
      stopBusy();
      if (res.status === 200) {
        var run = res.body;
        (S.runs[id] = S.runs[id] || []).push(run);
        S.legSel = null; S.cameFrom = null;
        refreshState();
        if (S.tripId === id) { go("#trips/" + id + "/run/" + run.run_id); }
      } else {
        var m = (res.body && res.body.message) || ("HTTP " + res.status);
        if (res.body && res.body.error === "confirm_stale") { m = res.body.message; }
        showBanner(m);
        renderTrips();
      }
    });
  }

  function renderRunChips(main) {
    var list = S.runs[S.tripId] || [];
    if (!list.length) { return; }
    var meta = el("div", "meta");
    add(meta, el("span", "label", "Runs"));
    var box = el("div", "runs");
    list.forEach(function (r, i) {
      var t = r.mode.toUpperCase() + " · " + (r.started_at || "").slice(11, 16) + " · exit " + r.exit_code;
      var b = btn("runchip", t, function () { go("#trips/" + S.tripId + "/run/" + r.run_id); }, "run-chip-" + i);
      b.setAttribute("aria-pressed", String(r.run_id === S.runId));
      add(box, b);
    });
    var fx = btn("runchip", "Fixture", function () { go("#trips/" + S.tripId); }, "run-chip-fixture");
    fx.setAttribute("aria-pressed", String(!S.runId));
    add(box, fx);
    add(meta, box);
    add(main, meta);
  }

  /* -------------------------------------------------------------- result */

  function exitChip(run) {
    var kind = "neutral";
    if (run.exit_code === 3 || run.exit_code === 4) { kind = "withheld"; }
    if (run.exit_code === 1 || run.exit_code === 2) { kind = "exitwarn"; }
    return tid(chip(kind, "exit " + run.exit_code + " · " + run.exit_label), "exit-chip");
  }

  function callsLine(run) {
    if (run.mode === "offline") { return "CALLS 0 this run (offline: no transport)"; }
    if (run.mode === "replay") { return "CALLS 0 this run (replay asks nothing)"; }
    var tr = run.calls && run.calls.this_run;
    if (!tr) { return "CALLS this run: not measured"; }
    var n = tr.search + tr.trips;
    return "CALLS " + n + " this run (" + tr.search + " search + " + tr.trips + " trips)";
  }

  function panelRefusal(title, message) {
    var box = el("div", "panel refusal");
    add(box, el("div", "label", title), el("pre", "", message));
    return box;
  }

  function renderResult(main, run) {
    var res = tid(el("div", "result"), "trip-result");
    var meta = el("div", "meta");
    add(meta, el("span", "pill pill-" + run.mode, run.mode.toUpperCase()), exitChip(run),
      tid(el("span", "mono", callsLine(run)), "run-calls"),
      el("span", "", run.duration_s + " s"));
    add(meta, cmdRow(run.argv_display, "run-command"));
    add(res, meta);

    if (run.refusal) {
      add(res, tid(panelRefusal("Nothing was scored", run.refusal.message), "refusal"));
      add(res, transcriptBlock(run.transcript));
      add(main, res);
      return;
    }

    if (run.funding_banner && run.funding_banner.length) {
      var fb = tid(el("div", "panel funding"), "funding-banner");
      run.funding_banner.forEach(function (sg) {
        if (/^=+$/.test(sg.text)) { return; }
        add(fb, styled(sg.text, sg.style, "div"));
      });
      add(res, fb);
    }

    add(res, renderHeadline(run));
    add(res, renderLegsTable(run));

    var lower = el("div", "lower");
    var notes = tid(el("div", "panel"), "trip-notes");
    add(notes, el("div", "label", "Trip notes"));
    var ul = el("ul", "notes");
    var skip = { withheld: 1, mixed: 1, range_caveat: 1, badge: 1 };
    run.trip_notes.forEach(function (n) { if (!skip[n.topic]) { add(ul, noteItem(n.segments)); } });
    run.footer_lines.forEach(function (n) { add(ul, noteItem(n.segments)); });
    if (!ul.firstChild) { add(ul, el("li", "dim", "No trip-level notes on this run.")); }
    add(notes, ul);

    var right = el("div", "panel");
    var resid = tid(el("div"), "residue");
    add(resid, el("div", "label", "Residue — what is left in each account"));
    var kv = el("div", "kv");
    run.residue_rows.forEach(function (r) {
      var d1 = el("div"); add(d1, el("span", "dim", r.currency + " · " + r.starting + " start"),
        el("span", "", r.remaining + (r.unconstrained ? "" : " left")));
      var d2 = el("div"); add(d2, el("span", "dim", "spent"), el("span", "", r.spent));
      add(kv, d1, d2);
      if (r.note) { add(kv, styled(r.note, r.note_style, "div")); }
    });
    if (!run.residue_rows.length) { add(kv, el("div", "dim", "No residue table on this run.")); }
    add(resid, kv);
    add(right, resid, runDetails(run), transcriptBlock(run.transcript));
    add(lower, notes, right);
    add(res, lower);
    add(main, res);
  }

  function noteItem(list) { var li = el("li"); add(li, segs(list)); return li; }

  function transcriptBlock(text) {
    var d = tid(el("details", "fold"), "transcript");
    d.style.marginTop = "14px";
    var sm = el("summary", "label", "CLI output");
    var pre = el("pre", "transcript", text);
    var copy = btn("btn btn-small", "Copy", function () { copyText(text, copy); }, "transcript-copy");
    add(d, sm, el("div", "", copy), pre);
    return d;
  }

  function runDetails(run) {
    var d = tid(el("details", "fold"), "run-details");
    d.style.marginTop = "14px";
    add(d, el("summary", "label", "Run details"));
    var box = el("div", "lines");
    var c = run.context;
    add(box, el("div", "", c.key_source ? "Seats.aero key: (masked key not sent to the browser)   (source: " +
      c.key_source + ")" : (run.mode === "replay" ? "Seats.aero key: not required (--from-snapshot replays committed bytes)" :
      "Seats.aero key: not used (offline: no transport)")));
    (S.state ? S.state.relocation_lines : []).forEach(function (l) { add(box, el("div", "seg-caution", l)); });
    c.wallet_block.forEach(function (l) { add(box, el("div", "", l)); });
    c.fx_lines.forEach(function (l) { add(box, el("div", "", l)); });
    add(box, el("div", "", c.valuation_line));
    if (run.replay) { run.replay.banner_lines.forEach(function (l) { add(box, el("div", "seg-replay", l.text)); }); }
    if (run.live_banner_lines) { run.live_banner_lines.forEach(function (l) { add(box, el("div", "", l.text)); }); }
    add(d, box);
    return d;
  }

  /* ------------------------------------------------------------ headline */

  function renderHeadline(run) {
    var h = run.headline;
    var box = tid(el("div", "panel headline"), "headline");
    var left = el("div");
    add(left, el("div", "label", "Optimizer beats paying cash by"));
    var v = tid(el("div", "hl-value"), "headline-value");
    if (h.state === "single" || h.state === "range") {
      if (!h.qualifier) {
        // The number never travels without what it is. No qualifier: no number.
        add(v, unknownChip(), el("span", "q", "headline has no qualifier; not shown"));
      } else {
        add(v, el("span", "", h.state === "range" ? h.text.replace(" - ", " – ") : h.text),
          el("span", "q", h.qualifier));
      }
    } else {
      add(v, chip("withheld", h.state === "not_fundable" ? "WITHHELD - PLAN NOT FUNDABLE" : "WITHHELD"));
    }
    add(left, v);
    if (h.state === "range") {
      var names = { surcharge: "carrier SURCHARGE", award_taxes: "award TAXES", apd: "UK APD" };
      h.range_parts.forEach(function (rp) {
        add(left, tid(el("div", "hl-part", names[rp.part] + " unknown" +
          (rp.legs.length ? " on " + rp.legs.join(", ") : "")), "headline-part"));
      });
      var rows = el("div", "hl-rows");
      [h.low_row, h.high_row].forEach(function (r) {
        if (!r) { return; }
        var d = el("div"); add(d, el("span", "dim", r.label), el("span", "", r.value)); add(rows, d);
      });
      add(left, rows);
    }
    if (h.withheld_reason || h.funding_note) {
      add(left, tid(p("withheld because  " + (h.withheld_reason || h.funding_note), "reason seg-caution"),
        "headline-withheld-reason"));
    }
    h.manifest_rows.forEach(function (r) {
      var d = el("div", "hl-rows"); var row = el("div");
      add(row, el("span", "dim", r.label.replace(/\n/g, " ").trim()), el("span", "", r.value));
      add(d, row); add(left, d);
    });
    if (run.replay && run.replay.reparsed) {
      add(left, p("REPARSED: captured under " + run.replay.parser_at_capture + ", read by " +
        run.replay.parser_now + " now.", "warnline"));
    }

    var right = tid(el("div", "kv"), "totals");
    run.totals_rows.forEach(function (r) {
      if (r.group !== "totals") { return; }
      var d = el("div", r.label.indexOf("  ") === 0 ? "sub" : "");
      add(d, el("span", styleClass(r.label_style), r.label.replace(/\n/g, " ").trim()),
        el("span", styleClass(r.value_style), r.value));
      add(right, d);
    });

    var prov = el("div", "prov");
    var row = el("div", "row");
    add(row, el("span", "label", "Margin provenance"),
      tid(chip(h.provenance === "live" || h.provenance === "snapshot" ? "neutral" : "cashq", h.provenance), "headline-provenance"),
      el("span", "mono dim", h.legs_counted_text));
    add(prov, row, p(h.provenance_note, "note"));
    run.trip_notes.forEach(function (n) {
      if (n.topic === "withheld" || n.topic === "mixed" || n.topic === "range_caveat") {
        var q = el("p", "note"); add(q, segs(n.segments)); add(prov, tid(q, "headline-caveat"));
      }
    });
    add(box, left, right, prov);
    return box;
  }

  /* ----------------------------------------------------------- legs table */

  var LEG_COLS = [
    ["leg", "Leg", ""], ["what", "What", ""], ["cash", "Cash", "num"], ["cash_pts", "Cash as pts", "num"],
    ["path", "Best points path", ""], ["points", "Points", "num"], ["surcharge", "Surcharge", "num"],
    ["surch_source", "Surch. source", ""], ["score_points", "Score points", "num"],
    ["score_cash", "Score cash", "num"], ["provenance", "Provenance points | cash", ""],
    ["verdict", "Verdict", ""]
  ];

  /* The leg id and the VERDICT - the answer - stay put while the middle of the
     table scrolls sideways. */
  function stickyClass(name) {
    if (name === "leg") { return " pin-left"; }
    if (name === "verdict") { return " pin-right"; }
    return "";
  }

  function verdictChip(cell) {
    var first = cell.segments[0] || { text: "" };
    var kind = cell.kind;
    var wrap = el("span", "chips");
    var c;
    if (kind === "points") { c = chip("points", first.text); }
    else if (kind === "withheld") { c = chip("withheld", first.text); }
    else if (kind === "cash_qualified") {
      var i = first.text.indexOf(" (");
      c = i >= 0 ? chip("cash chip-cashq", first.text.slice(0, i), first.text.slice(i)) : chip("cash chip-cashq", first.text);
    } else { c = chip("cash", first.text); }
    add(wrap, c);
    cell.segments.slice(1).forEach(function (sg) { if (sg.text.trim()) { add(wrap, flagTag(sg.text.trim())); } });
    return wrap;
  }

  function cellContent(name, cell, leg) {
    if (name === "verdict") { return verdictChip(cell); }
    if (cell.kind === "unknown" && (name === "cash" || name === "cash_pts" || name === "surcharge")) {
      return unknownChip();
    }
    if (name === "what") {
      // The CLI's own words: the description's first part (the terminal cuts it
      // at 28 characters; the page does not), then the rest of it underneath.
      var w = el("span");
      var parts = leg.description.split(",");
      var head = parts[0].trim();
      var m = /^([A-Z]{3})->([A-Z]{3})$/.exec(head);
      if (m) { add(w, routeEl(m[1], m[2])); } else { add(w, el("span", "", head)); }
      if (parts.length > 1) { add(w, el("span", "sub2", parts.slice(1).join(",").trim())); }
      return w;
    }
    if (name === "score_points" && cell.kind === "floor") {
      var t = cell.segments.map(function (s) { return s.text; }).join("");
      return el("span", "", t.replace(/^>= /, "≥ "));
    }
    return segs(cell.segments);
  }

  function renderLegsTable(run) {
    var wrap = tid(el("div", "tscroll"), "legs-table-scroll");
    var t = tid(el("table", "grid"), "legs-table");
    var thead = el("thead"); var hr = el("tr");
    LEG_COLS.forEach(function (c) { add(hr, el("th", c[2] + stickyClass(c[0]), c[1])); });
    add(thead, hr); add(t, thead);
    var tb = el("tbody");
    run.legs.forEach(function (leg) {
      var tr = tid(el("tr", "clickable"), "leg-row-" + leg.id);
      tr.tabIndex = 0;
      tr.setAttribute("aria-selected", String(S.legSel === leg.id));
      LEG_COLS.forEach(function (c) {
        var name = c[0];
        var cell = leg.cells[name];
        var td = el("td", c[2] + (["cash", "cash_pts", "points", "surcharge", "score_points", "score_cash", "provenance"].indexOf(name) >= 0 ? " mono" : "") + (name === "leg" ? " legid" : "") + stickyClass(name));
        tid(td, name === "verdict" ? "verdict-" + leg.id : "cell-" + leg.id + "-" + name);
        td.setAttribute("data-kind", cell.kind);
        add(td, cellContent(name, cell, leg));
        add(tr, td);
      });
      var open = function () {
        openedFrom("leg-row-" + leg.id);
        S.legSel = leg.id; renderTrips(); focusDrawer();
      };
      tr.addEventListener("click", open);
      tr.addEventListener("keydown", function (e) {
        if (e.key !== "Enter" && e.key !== " ") { return; }
        // The default action would fire on whatever has focus AFTER this
        // handler - which is the drawer's close button - and shut the drawer
        // with the same keypress that opened it.
        e.preventDefault();
        open();
      });
      add(tb, tr);
    });
    add(t, tb); add(wrap, t);
    return wrap;
  }

  function focusDrawer() {
    var x = document.querySelector(S.view === "search" ? "#drawer-search .x" : "#drawer-trips .x");
    if (x) { x.focus({ preventScroll: true }); }
  }

  // Closing re-renders the table, which DESTROYS the element focus came from,
  // so focus fell to <body> and the reader's place in the table was gone - 18
  // Tab presses back to the row they were reading. The opener records where it
  // came from and the closer puts focus back on the rebuilt element. Mouse
  // users see no ring: :focus-visible does not match a programmatic focus that
  // follows a click.
  function openedFrom(testid) { S.cameFrom = testid; }
  function closeDrawer(render) {
    var testid = S.cameFrom;
    S.cameFrom = null;
    render();
    if (!testid) { return; }
    var back = null;
    var all = document.querySelectorAll("[data-testid]");
    for (var i = 0; i < all.length; i++) {
      if (all[i].getAttribute("data-testid") === testid) { back = all[i]; break; }
    }
    if (back && back.focus) { back.focus({ preventScroll: true }); }
  }
  function closeLegDrawer() {
    S.legSel = null;
    closeDrawer(renderTrips);
  }
  function closeSearchDrawer() {
    S.search.sel = null;
    closeDrawer(renderSearch);
  }

  /* ----------------------------------------------------------- leg drawer */

  function linesByTopic(leg, topics) {
    return leg.cli_lines.filter(function (l) { return topics.indexOf(l.topic) >= 0; });
  }
  function lineP(l, extra) {
    var q = el("p", styleClass(l.style) + (extra ? " " + extra : ""), l.text.trim());
    return q;
  }

  function renderLegDrawer(run) {
    var d = $("drawer-trips");
    clear(d);
    var leg = null;
    if (run && S.legSel && run.legs) {
      run.legs.forEach(function (l) { if (l.id === S.legSel) { leg = l; } });
    }
    if (!leg) { d.hidden = true; return; }
    d.hidden = false;
    var head = el("div", "dhead");
    var top = el("div", "top");
    var ttl = el("div", "ttl");
    add(ttl, el("span", "legid", leg.id + "  "));
    if (leg.origin && leg.destination) { add(ttl, routeEl(leg.origin, leg.destination)); } else { add(ttl, leg.short); }
    add(ttl, "  · " + leg.date);
    var x = btn("x", "×", closeLegDrawer);
    x.setAttribute("aria-label", "Close detail");
    add(top, ttl, x);
    var chips = el("div", "chips");
    add(chips, tid(verdictChip(leg.cells.verdict), "drawer-verdict"), el("span", "dim", leg.description));
    add(head, top, chips, tid(p(leg.verdict.reason, "reason"), "drawer-reason"));
    add(d, head);

    // CASH
    var cash = section("Cash", "drawer-cash");
    if (leg.cash) {
      add(cash, p(leg.cash.label));
      linesByTopic(leg, ["cash"]).slice(1).forEach(function (l) { add(cash, lineP(l, "mono")); });
      var pv = el("p", "dim");
      add(pv, "provenance: ", leg.cash.provenance === "unknown" ? el("span", "seg-caution", "unknown") :
        leg.cash.provenance + (leg.cash.captured_on ? " " + leg.cash.captured_on : ""));
      add(cash, pv);
    } else {
      var up = el("p"); add(up, unknownChip(), "  no cash price could be priced for this leg."); add(cash, up);
    }
    linesByTopic(leg, ["fees"]).forEach(function (l) { add(cash, lineP(l)); });
    add(d, cash);

    // POINTS
    var pts = section("Points", "drawer-points");
    if (leg.points) {
      var pl = el("p");
      add(pl, leg.points.label + "  ");
      if (leg.points.unverified) { add(pl, tid(unvTag(), "drawer-points-unverified")); }
      if (leg.points.attribution_assumed) { add(pl, "  ", unvTag("program attribution ASSUMED")); }
      add(pts, pl);
      if (leg.points.path_summary) { add(pts, p(leg.points.path_summary, "mono dim")); }
      if (leg.points.spend_summary) { add(pts, p("spend: " + leg.points.spend_summary, "mono dim")); }
      if (leg.points.stranded) {
        add(pts, p("stranded: " + fmtInt(leg.points.stranded) + " (within the unavoidable transfer increment)", "mono dim"));
      }
    } else {
      add(pts, p(leg.cells.path.segments.map(function (s) { return s.text; }).join("")));
    }
    add(d, pts);

    // THE MATH
    var math = section("The math", "drawer-math");
    var g = el("div", "mathgrid");
    [["score points", "score_points"], ["score cash", "score_cash"]].forEach(function (pair) {
      var c = el("div", "cellm"); add(c, el("div", "label", pair[0]));
      var f = el("div", "figure");
      var cell = leg.cells[pair[1]];
      if (cell.kind === "range" && pair[1] === "score_points") {
        // Two numbers, drawn as two numbers. No midpoint anywhere.
        add(f, el("span", "", cell.segments.map(function (s) { return s.text; }).join("").replace("-", " – ")));
      } else if (cell.kind === "none" && pair[1] === "score_points") { add(f, el("span", "dim", "not scored")); }
      else { add(f, cellContent(pair[1], cell, leg)); }
      add(c, f); add(g, c);
    });
    add(math, g);
    linesByTopic(leg, ["margin", "break_even", "floor", "inert"]).forEach(function (l) { add(math, lineP(l)); });
    add(d, math);

    // TAXES
    var tx = section("Taxes", "drawer-taxes");
    var tl = linesByTopic(leg, ["taxes"]);
    if (leg.taxes.status === "unknown" || leg.taxes.status === "unconvertible") {
      var tu = el("p"); add(tu, unknownChip()); add(tx, tu);
    }
    if (tl.length) { tl.forEach(function (l) { add(tx, lineP(l)); }); }
    else { add(tx, p("No award taxes were reported for this leg's points price on this run.", "dim")); }
    add(d, tx);

    // SURCHARGE
    var su = section("Surcharge", "drawer-surcharge");
    if (!leg.surcharge) { add(su, p("No points path, so no surcharge applies to a points option here.", "dim")); }
    else {
      var sp = el("p");
      if (!leg.surcharge.known) { add(sp, unknownChip()); }
      else {
        // The terminal's own line LOSES this word: it prints
        // "[modeled]" as markup and rich reads it as a style tag and eats it.
        // The drawer takes it from the engine field instead, so "$0.00" is
        // never shown here without saying whether anyone observed it. The CLI
        // line itself is an older defect and is filed, not fixed, this round.
        var conf = String(leg.surcharge.confidence || "unknown").toUpperCase();
        add(sp, tid(chip(conf === "CAPTURED" ? "neutral" : "modeled", conf,
          conf === "CAPTURED" ? "observed in a capture" : "from the surcharge table, not observed"),
          "surcharge-confidence"));
      }
      add(su, sp);
      linesByTopic(leg, ["surcharge", "surcharge_unknown"]).forEach(function (l) { add(su, lineP(l)); });
    }
    add(d, su);

    // UK APD
    if (leg.apd.state !== "none") {
      var ap = section("UK APD", "drawer-apd");
      var names = { added: "ADDED", stated_not_added: "STATED, NOT ADDED", unknown: "UNKNOWN" };
      var cp = el("p");
      add(cp, leg.apd.state === "unknown" ? unknownChip() :
        chip(leg.apd.state === "added" ? "neutral" : "withheld", names[leg.apd.state]));
      add(ap, cp);
      if (leg.apd.line) { add(ap, p(leg.apd.line)); }
      add(d, ap);
    }

    // OPERATING AIRLINE
    var oa = section("Operating airline", "drawer-metal");
    if (leg.legacy_metal_line) { add(oa, p(leg.legacy_metal_line, "mono dim")); }
    if (leg.metal) {
      var ml = tid(el("p"), "drawer-metal-line");
      if (leg.metal.unverified) { add(ml, tid(unvTag(), "metal-unverified"), "  "); }
      add(ml, leg.metal.line);
      add(oa, ml);
      leg.metal.extra_lines.forEach(function (x2) { add(oa, p(x2, "mono dim")); });
    }
    leg.other_lookups.forEach(function (o) {
      var op = el("p"); if (o.unverified) { add(op, unvTag(), "  "); } add(op, o.title); add(oa, op);
      o.lines.forEach(function (x3) { add(oa, p(x3, "mono dim")); });
    });
    if (!leg.metal && !leg.legacy_metal_line) {
      add(oa, p(run.mode === "offline" ? "Offline run: no itinerary lookup was made." :
        "No operating-airline line for this leg.", "dim"));
    }
    add(d, oa);

    // PROVENANCE
    var pr = section("Provenance", "drawer-provenance");
    var pc = el("p");
    add(pc, chip(leg.cells.provenance.kind === "live" || leg.cells.provenance.kind === "snapshot" ? "neutral" : "cashq",
      leg.cells.provenance.segments[0].text));
    add(pr, pc);
    linesByTopic(leg, ["outcome", "coverage", "provenance"]).forEach(function (l) { add(pr, lineP(l)); });
    if (leg.points && leg.points.source !== "seats_aero_live") {
      add(pr, p("points price source: " + leg.points.source + (leg.points.source_note ? " - " + leg.points.source_note : ""), "dim"));
    }
    if (leg.live && S.state) {
      add(pr, p(leg.live.replayed_from_snapshot ? "search parser: captured under " +
        (leg.live.snapshot_parser_version || "an unrecorded version") + " / read by " + S.state.parser.search :
        "search parser " + S.state.parser.search, "dim mono"));
    }
    add(d, pr);

    var sd = linesByTopic(leg, ["superseded", "date_shift", "flexible"]);
    if (sd.length) {
      var fl = section("Superseded badges · date shifted · flexible-date findings (NOT scored)", "drawer-flexible");
      sd.forEach(function (l) { add(fl, lineP(l)); });
      add(d, fl);
    }
    if (leg.alternatives_lines.length) {
      var al = section("Same-metal alternatives", "drawer-alternatives");
      leg.alternatives_lines.forEach(function (l) { add(al, lineP(l)); });
      add(d, al);
    }
    var wl = linesByTopic(leg, ["note", "flag", "warning"]);
    if (wl.length) {
      var ws = section("Notes, flags and UNVERIFIED claims", "drawer-warnings");
      wl.forEach(function (l) { add(ws, lineP(l)); });
      add(d, ws);
    }
    var rc = section("Reason codes", "drawer-reasons");
    if (leg.reasons.length) {
      var ul = el("ul");
      leg.reasons.forEach(function (r) { var li = el("li"); add(li, el("span", "mono", r.code), " — " + r.detail); add(ul, li); });
      add(rc, ul);
    } else { add(rc, p("none", "dim")); }
    add(d, rc);

    var cl = section("Every line the CLI prints for this leg");
    var pre = tid(el("pre", "cli"), "cli-lines-" + leg.id);
    pre.textContent = leg.cli_lines.map(function (l) { return l.text; }).join("\n");
    add(cl, pre);
    add(d, cl);
  }

  /* -------------------------------------------------------------- confirm */

  function openConfirm(c, onGo) {
    var sc = clear($("scrim"));
    var m = tid(el("div", "modal"), c.testid);
    m.setAttribute("role", "dialog"); m.setAttribute("aria-modal", "true");
    var h = el("h3");
    add(h, c.lead + " ", el("span", "big mono", String(c.n)), " Seats.aero calls");
    add(m, el("div", "label", "Before anything is spent"), h, p(c.why, "note"));
    (c.extra || []).forEach(function (x) { add(m, p(x, "note")); });
    add(m, el("div", "cmd", "Equivalent command: " + c.cmd));
    var acts = el("div", "acts");
    var cancel = btn("btn", "Cancel", closeConfirm, "confirm-cancel");
    var goBtn = btn("btn btn-primary", c.go, function () { closeConfirm(); onGo(); }, c.goid);
    add(acts, cancel, goBtn); add(m, acts); add(sc, m);
    sc.hidden = false;
    cancel.focus();
  }
  function closeConfirm() { $("scrim").hidden = true; clear($("scrim")); }

  /* --------------------------------------------------------------- search */

  function renderSearch() {
    var main = clear($("search-main"));
    var st = S.state;
    var q = S.search;
    var strip = tid(el("div", "panel strip"), "search-strip");
    function inp(label, key, testid, cls, ph) {
      var w = el("label", "field");
      var i = tid(el("input", cls || ""), testid);
      i.value = q[key]; if (ph) { i.placeholder = ph; }
      i.addEventListener("input", function () { q[key] = i.value; updateWindow(); });
      i.addEventListener("keydown", function (e) { if (e.key === "Enter") { doSearch(); } });
      add(w, el("span", "label", label), i);
      return w;
    }
    add(strip, inp("From", "from", "search-from", "", "SFO"), inp("To", "to", "search-to", "", "MAD"),
      inp("Date", "date", "search-date", "date", "YYYY-MM-DD"),
      inp("To date", "dateTo", "search-date-to", "date", "+30 days"));
    var cw = el("label", "field");
    var cs = tid(el("select"), "search-cabin");
    ["All", "Y", "W", "J", "F"].forEach(function (c) { var o = el("option", "", c); o.value = c; if (q.cabin === c) { o.selected = true; } add(cs, o); });
    cs.addEventListener("change", function () { q.cabin = cs.value; renderSearch(); });
    add(cw, el("span", "label", "Cabin"), cs);
    var run = btn("btn btn-primary", q.busy ? "Searching…" : "Run search", doSearch, "search-run");
    run.disabled = q.busy || !(st && st.key.found);
    add(strip, cw, run);
    add(main, strip);
    var win = tid(p("", "note"), "search-window");
    add(main, win);
    function updateWindow() {
      var from = q.date || "?";
      var to = q.dateTo || (q.date ? plus30(q.date) : "?");
      win.textContent = "Searches " + from + " to " + to + " · 1 traveller (award prices are per seat) · " +
        "single-route search is always LIVE and does not use the disk cache.";
    }
    updateWindow();
    Object.keys(q.errors).forEach(function (k) {
      add(main, tid(p(q.errors[k], "field-error"), "search-error-" + k));
    });

    var box = el("div", "result");
    if (st && !st.key.found) {
      add(box, tid(panelRefusal("LIVE unavailable", st.key.error_text), "search-state"));
    } else if (q.busy) {
      add(box, tid(p("Asking Seats.aero… nothing is shown until the whole answer is in.", "note"), "search-state"));
    } else if (!q.run) {
      add(box, tid(p("Search a route. Results show award space, whether your wallet can fund it, and " +
        "whether the taxes are trusted. A single-route search has no cash price, so it cannot say " +
        "POINTS or PAY CASH: add an award to a trip to score it.", "note"), "search-state"));
    } else {
      renderSearchResult(box, q.run);
    }
    add(main, box);
    renderSearchDrawer();
    setDrawerClass();
  }

  function plus30(d) {
    var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(d);
    if (!m) { return "?"; }
    var dt = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]) + 30));
    return dt.toISOString().slice(0, 10);
  }

  function searchBody() {
    var q = S.search;
    return { origin: q.from, destination: q.to, date: q.date, date_to: q.dateTo };
  }

  function doSearch() {
    var q = S.search;
    if (q.busy) { return; }
    q.errors = {};
    api("POST", "/api/search/preflight", searchBody()).then(function (res) {
      if (res.status === 400 && res.body && res.body.errors) {
        res.body.errors.forEach(function (e) { q.errors[e.field] = e.message; });
        renderSearch(); return;
      }
      if (res.status !== 200) { showBanner((res.body && res.body.message) || ("HTTP " + res.status)); return; }
      var pf = res.body;
      renderSearch();
      if (pf.blocked) { showBanner(pf.blocked.message); return; }
      openConfirm({
        lead: "This search can spend up to", n: pf.max_calls,
        why: "One call per results page (one page is the only shape seen so far). " +
          callsSentence(pf.calls),
        extra: ["Searches " + pf.window.from + " to " + pf.window.to + ". Single-route search does not use the disk cache."],
        cmd: pf.argv_display, go: "Spend up to " + pf.max_calls + " calls",
        testid: "search-confirm", goid: "search-confirm-go"
      }, function () {
        var body = searchBody(); body.confirm_id = pf.confirm_id;
        q.busy = true; startBusy("Asking Seats.aero… nothing is shown until the whole answer is in.");
        renderSearch();
        api("POST", "/api/search/run", body).then(function (r2) {
          q.busy = false; stopBusy();
          if (r2.status === 200) { q.run = r2.body; q.sel = null; refreshState(); }
          else { showBanner((r2.body && r2.body.message) || ("HTTP " + r2.status)); }
          renderSearch();
        });
      });
    });
  }

  function renderSearchResult(box, run) {
    var meta = el("div", "meta");
    add(meta, exitChip(run), el("span", "mono", run.calls && run.calls.this_run ?
      "CALLS " + run.calls.this_run.search + " this run" : "CALLS this run: not measured"),
      el("span", "", run.duration_s + " s"));
    add(box, meta);
    if (run.refusal) {
      add(box, tid(panelRefusal("Nothing was searched", run.refusal.message), "search-state"));
      add(box, cmdRow(run.argv_display, "search-command"));
      add(box, transcriptBlock(run.transcript));
      return;
    }
    var stateBox = tid(el("div", "panel state-box"), "search-state");
    stateBox.setAttribute("data-state", run.state);
    if (run.state === "api_error") {
      add(stateBox, p("Seats.aero could NOT be reached: " + run.api_error, "seg-alert"),
        p("This is an API failure, NOT a finding of no award availability. No conclusion about award space can be drawn from this run.", "seg-alert"));
    } else if (run.state === "no_awards") {
      add(stateBox, p("Seats.aero returned no award availability for this route and date range.", "seg-caution"),
        p("Seats.aero result coverage: " + run.coverage_note, run.coverage_incomplete ? "seg-alert" : "dim"));
    } else if (run.state === "none_fundable") {
      add(stateBox, p(run.header_line, "seg-caution"));
    } else {
      add(stateBox, p(run.rows.length + " row(s) · award space, fundability from your wallet, and taxes trust. No POINTS / PAY CASH verdict: there is no cash price here.", "dim"));
    }
    add(box, stateBox);
    if (run.rows.length) { add(box, renderSearchTable(run)); }
    var foot = tid(el("div", "footer-lines"), "search-footer");
    add(foot, el("div", run.coverage_incomplete ? "seg-alert" : "", "Seats.aero result coverage: " + (run.coverage_note || "(no coverage note recorded)")));
    add(foot, el("div", "seg-caution", run.trips_footer));
    if (run.unknown_cash_line) { add(foot, el("div", "seg-alert", run.unknown_cash_line)); }
    run.context.wallet_lines.forEach(function (l) { add(foot, el("div", "", l)); });
    run.context.wallet_warnings.forEach(function (l) { add(foot, el("div", "seg-caution", "! " + l)); });
    var fx = el("details", "fold");
    add(fx, el("summary", "", run.context.fx_summary));
    var fl = el("div", "lines"); run.context.fx_lines.forEach(function (l) { add(fl, el("div", "", l)); });
    add(fx, fl); add(foot, fx);
    add(foot, cmdRow(run.argv_display, "search-command"));
    add(box, foot);
    add(box, transcriptBlock(run.transcript));
  }

  function renderSearchTable(run) {
    var q = S.search;
    var cabins = q.cabin === "All" ? ["Y", "W", "J", "F"] : [q.cabin];
    var wrap = tid(el("div", "tscroll"), "search-table-scroll");
    var t = tid(el("table", "grid static"), "search-table");
    var thead = el("thead"); var hr = el("tr");
    add(hr, el("th", "", "Date"), el("th", "", "Program"));
    cabins.forEach(function (c) { add(hr, el("th", "", c)); });
    add(thead, hr); add(t, thead);
    var tb = el("tbody");
    run.rows.forEach(function (row, ri) {
      var tr = el("tr");
      var pg = el("td"); add(pg, el("span", "", row.program || "(program not named)"), el("span", "sub2", row.source_code || ""));
      add(tr, el("td", "mono", row.date), pg);
      cabins.forEach(function (c) {
        var cell = row.cabins[c];
        var td = el("td", "cab");
        if (!cell) { add(td, el("div", "cell none", "no space")); add(tr, td); return; }
        td.className += " pick";
        td.tabIndex = 0;
        tid(td, "cell-" + row.date + "-" + (row.source_code || "none") + "-" + c);
        if (q.sel && q.sel.row === ri && q.sel.cabin === c) { td.className += " sel"; }
        var bx = el("div", "cell");
        var l1 = el("div", "l1", fmtInt(cell.cost)); add(l1, el("span", "seats", cell.seats + " seats"));
        var l2 = el("div", "l2");
        if (cell.taxes.known) { add(l2, cell.taxes.text); } else { add(l2, unknownChip()); }
        if (cell.total_text) { add(l2, el("span", "dim", "  total " + cell.total_text.replace(/^>= /, "≥ "))); }
        var l3 = el("div");
        if (cell.fundable) { add(l3, chip("neutral", "FUNDABLE #" + cell.rank)); }
        else if (cell.indirect_path) { add(l3, chip("indirect", "INDIRECT")); }
        else { add(l3, chip("notfund", "NOT FUNDABLE")); }
        add(bx, l1, l2, l3); add(td, bx);
        var open = function () {
          openedFrom("cell-" + row.date + "-" + (row.source_code || "none") + "-" + c);
          q.sel = { row: ri, cabin: c }; renderSearch(); focusDrawer();
        };
        td.addEventListener("click", open);
        td.addEventListener("keydown", function (e) {
          if (e.key !== "Enter" && e.key !== " ") { return; }
          e.preventDefault();
          open();
        });
        add(tr, td);
      });
      add(tb, tr);
    });
    add(t, tb); add(wrap, t);
    return wrap;
  }

  function renderSearchDrawer() {
    var d = clear($("drawer-search"));
    var q = S.search;
    var run = q.run;
    if (!run || !q.sel || !run.rows[q.sel.row]) { d.hidden = true; return; }
    var row = run.rows[q.sel.row];
    var cell = row.cabins[q.sel.cabin];
    if (!cell) { d.hidden = true; return; }
    d.hidden = false;
    var head = el("div", "dhead"); var top = el("div", "top");
    var ttl = el("div", "ttl");
    add(ttl, routeEl(run.route.origin, run.route.destination), "  · " + row.date + " · " +
      (row.program || "(program not named)") + " · " + q.sel.cabin);
    var x = btn("x", "×", closeSearchDrawer);
    x.setAttribute("aria-label", "Close detail");
    add(top, ttl, x); add(head, top); add(d, head);

    var a = section("Award");
    var kv = el("div", "kv");
    [["Miles", fmtInt(cell.cost)], ["Seats", String(cell.seats)],
     ["Possible carriers", cell.carriers.length ? cell.carriers.join(", ") : "not listed"]].forEach(function (pr) {
      var dv = el("div"); add(dv, el("span", "dim", pr[0]), el("span", "", pr[1])); add(kv, dv);
    });
    add(a, kv); add(d, a);

    var t = section("Taxes", "search-drawer-taxes");
    if (cell.taxes.known) {
      var tp = el("p");
      add(tp, el("span", "figure", cell.taxes.text));
      if (cell.taxes.source_text) { add(tp, "  (" + cell.taxes.source_text + ")"); }
      if (cell.taxes.confirm) { add(tp, "  ", unvTag("confirm before trusting")); }
      add(t, tp);
    } else {
      var tu = el("p"); add(tu, unknownChip(), "  IT IS NOT $0."); add(t, tu);
      add(t, p(cell.taxes.note));
    }
    add(d, t);

    var f = section("Funding", "search-drawer-funding");
    if (cell.fundable) {
      add(f, p(cell.path_summary, "mono"));
      if (cell.stranded) { add(f, p("stranded: " + fmtInt(cell.stranded), "mono dim")); }
      add(f, p("rank #" + cell.rank + " of " + run.fundable_count, "dim"));
    } else { add(f, p(cell.why_not)); }
    add(d, f);

    var tot = section("Total at 1¢/pt");
    if (cell.total_text) {
      add(tot, p(cell.total_text.replace(/^>= /, "≥ "), "figure"));
      if (cell.total_is_floor) { add(tot, p("a floor: the unknown cash is not in it", "dim")); }
    } else { add(tot, p("Not computed: this award cannot be funded from your wallet.", "dim")); }
    add(d, tot);

    var oa = section("Operating airline");
    add(oa, p("NOT LOOKED UP - single-route search does not call the trips endpoint" +
      (cell.carriers.length ? ". The possible carriers are " + cell.carriers.join(", ") + "." : ".")));
    add(d, oa);

    var pv = section("Provenance");
    add(pv, p("seats_aero_live · " + (cell.source_note || "") + " · parser " + cell.parser_version, "dim"));
    add(d, pv);

    var nv = section("No verdict");
    add(nv, p("A single-route search has no cash price to compare against, so it cannot say POINTS or PAY CASH. Add it to a trip with the fare you found."));
    var goBtn = btn("btn btn-primary", "Score against a fare →", function () {
      S.nt = newTripState();
      S.nt.cabin = q.sel.cabin;
      S.nt.legs = [{ origin: run.route.origin, destination: run.route.destination, date: row.date, cabin: q.sel.cabin, cash: "" }];
      go("#new-trip");
    }, "drawer-to-trip");
    var wrap = el("div"); add(wrap, goBtn); add(nv, wrap);
    add(d, nv);
  }

  /* ------------------------------------------------------------- new trip */

  function newTripState() {
    return { name: "", cabin: "Y", legs: [{ origin: "", destination: "", date: "", cabin: "", cash: "" }],
      errors: [], echo: null, draft_hash: null, wrote: null, busy: false };
  }

  function renderNewTrip(main) {
    var nt = S.nt || (S.nt = newTripState());
    var form = tid(el("div", "panel form"), "new-trip-form");
    add(form, el("div", "label", "New trip"));
    function errFor(leg, field) {
      return nt.errors.filter(function (e) { return e.leg === leg && e.field === field; })
        .map(function (e) { return tid(p(e.message, "field-error"), "nt-error-" + (leg === null ? "trip" : leg) + "-" + field); });
    }
    function invalidate() { nt.echo = null; nt.draft_hash = null; nt.wrote = null; }
    var r1 = el("div", "form-row");
    var nf = el("label", "field");
    var ni = tid(el("input", "name"), "nt-name"); ni.value = nt.name;
    ni.addEventListener("input", function () { nt.name = ni.value; invalidate(); renderNtEcho(); });
    add(nf, el("span", "label", "Name"), ni, el("span", "hint", "letters, digits, _ - ."));
    var cf = el("label", "field");
    var ci = tid(el("select"), "nt-cabin");
    ["Y", "W", "J", "F"].forEach(function (c) { var o = el("option", "", c); o.value = c; if (nt.cabin === c) { o.selected = true; } add(ci, o); });
    ci.addEventListener("change", function () { nt.cabin = ci.value; invalidate(); renderTrips(); });
    add(cf, el("span", "label", "Cabin for all legs"), ci);
    add(r1, nf, cf); add(form, r1, errFor(null, "name"), errFor(null, "cabin"));

    nt.legs.forEach(function (leg, i) {
      var row = tid(el("div", "form-row"), "nt-leg-" + (i + 1));
      function li(label, key, cls, ph) {
        var w = el("label", "field");
        var inp = tid(el("input", cls || ""), "nt-leg-" + (i + 1) + "-" + key);
        inp.value = leg[key]; if (ph) { inp.placeholder = ph; }
        inp.addEventListener("input", function () { leg[key] = inp.value; invalidate(); renderNtEcho(); });
        add(w, el("span", "label", label), inp);
        return w;
      }
      var cw = el("label", "field");
      var cs = tid(el("select"), "nt-leg-" + (i + 1) + "-cabin");
      var o0 = el("option", "", "trip cabin (" + nt.cabin + ")"); o0.value = ""; add(cs, o0);
      ["Y", "W", "J", "F"].forEach(function (c) { var o = el("option", "", c); o.value = c; if (leg.cabin === c) { o.selected = true; } add(cs, o); });
      cs.addEventListener("change", function () { leg.cabin = cs.value; invalidate(); renderNtEcho(); });
      add(cw, el("span", "label", "Cabin"), cs);
      var rm = btn("btn btn-small", "Remove", function () { nt.legs.splice(i, 1); invalidate(); renderTrips(); });
      rm.disabled = nt.legs.length === 1;
      add(row, el("span", "legid", "L" + (i + 1)), li("From", "origin", "", "SFO"), li("To", "destination", "", "LHR"),
        li("Date", "date", "date", "YYYY-MM-DD"), cw, li("Cash per person (USD)", "cash", "", "2400"), rm);
      add(form, row);
      ["origin", "destination", "date", "cash", "leg"].forEach(function (fld) { add(form, errFor(i + 1, fld)); });
    });
    add(form, btn("btn", "+ Add leg", function () {
      nt.legs.push({ origin: "", destination: "", date: "", cabin: "", cash: "" }); invalidate(); renderTrips();
    }, "nt-add-leg"));
    add(form, el("div", "mono", "TRAVELLERS: 1"));
    add(form, tid(p("Couple trip? Enter it as 1 traveller with per-person cash. Flight legs for 2+ " +
      "travellers are not scored on points (award prices are per seat; party pricing is not modelled) " +
      "and the trip headline would be WITHHELD.", "note"), "nt-couple-hint"));
    var acts = el("div", "form-row");
    add(acts, btn("btn", "Preview", ntPreview, "nt-preview"),
      btn("btn", "Cancel", function () { S.nt = null; go(S.tripId ? "#trips/" + S.tripId : "#trips"); }));
    add(form, acts);
    add(form, tid(el("div"), "nt-echo-box"));
    add(main, form);
    renderNtEcho();
  }

  function renderNtEcho() {
    var nt = S.nt;
    var box = document.querySelector('[data-testid="nt-echo-box"]');
    if (!box || !nt) { return; }
    clear(box);
    if (nt.wrote) {
      nt.wrote.lines.forEach(function (l) { add(box, p(l, "seg-caution")); });
      return;
    }
    if (!nt.echo) { return; }
    add(box, tid(el("pre", "echo", nt.echo.join("\n")), "nt-echo"));
    var w = btn("btn btn-primary", "Write " + nt.name.trim() + ".json", ntWrite, "nt-write");
    add(box, w);
  }

  function ntBody() {
    var nt = S.nt;
    return { name: nt.name, cabin: nt.cabin, legs: nt.legs.map(function (l) {
      return { origin: l.origin, destination: l.destination, date: l.date, cabin: l.cabin || nt.cabin, cash: l.cash };
    }) };
  }

  function ntPreview() {
    var nt = S.nt;
    api("POST", "/api/trips/draft", ntBody()).then(function (res) {
      if (res.status !== 200) { showBanner((res.body && res.body.message) || ("HTTP " + res.status)); return; }
      if (res.body.ok) { nt.errors = []; nt.echo = res.body.echo_lines; nt.draft_hash = res.body.draft_hash; }
      else { nt.errors = res.body.errors; nt.echo = null; nt.draft_hash = null; }
      renderTrips();
    });
  }

  function ntWrite() {
    var nt = S.nt;
    var body = ntBody(); body.draft_hash = nt.draft_hash;
    api("POST", "/api/trips/create", body).then(function (res) {
      if (res.status === 200 && res.body.id) {
        var id = res.body.id;
        S.lastWrite = { id: id, lines: res.body.lines };
        loadTrips().then(function () { S.nt = null; S.mode = "offline"; go("#trips/" + id); });
      } else {
        var m = (res.body && res.body.message) || ("HTTP " + res.status);
        nt.errors = [{ leg: null, field: "name", message: m }];
        if (res.body && res.body.error === "refused") { nt.errors.push({ leg: null, field: "name", message: "Choose another name." }); }
        renderTrips();
      }
    });
  }

  /* --------------------------------------------------------------- wallet */

  function openWallet() {
    var st = S.state;
    if (!st) { return; }
    var w = st.wallet;
    var rows = Object.keys(w.balances || {}).sort().map(function (c) {
      return { cur: c, bal: w.balances[c] === null ? "" : String(w.balances[c]) };
    });
    if (!rows.length) { rows.push({ cur: "UR", bal: "" }); }
    var cards = (w.cards || []).slice();
    var vals = w.valuations || {};
    var err = null;
    var sc = clear($("wallet-scrim"));
    var m = tid(el("div", "modal"), "wallet-panel");
    m.setAttribute("role", "dialog"); m.setAttribute("aria-modal", "true");
    function draw() {
      clear(m);
      add(m, el("div", "label", "Wallet"), el("h3", "", "Balances and cards for this session"));
      add(m, p("A number: held, that many points, the ceiling applies. Blank: held, balance not supplied, NO ceiling. " +
        "Removed: NOT HELD. Absent is not zero and zero is not unconstrained.", "note"));
      var list = el("div", "wallet-rows");
      rows.forEach(function (r, i) {
        var wr = el("div", "wr");
        var c = tid(el("input", "cur"), "wallet-cur-" + i); c.value = r.cur;
        c.addEventListener("input", function () { r.cur = c.value.toUpperCase(); });
        var b = tid(el("input"), "wallet-bal-" + i); b.value = r.bal; b.placeholder = "blank = not supplied";
        b.addEventListener("input", function () { r.bal = b.value; });
        add(wr, c, b, btn("btn btn-small", "Remove", function () { rows.splice(i, 1); draw(); }));
        add(list, wr);
      });
      add(list, btn("btn btn-small", "+ Currency", function () { rows.push({ cur: "", bal: "" }); draw(); }));
      add(m, list);
      add(m, el("div", "label", "Cards"));
      var cl = el("div", "wallet-rows");
      cards.forEach(function (cd, i) {
        var wr = el("div", "wr");
        var ci = tid(el("input", "card"), "wallet-card-" + i); ci.value = cd;
        ci.addEventListener("input", function () { cards[i] = ci.value; });
        add(wr, ci, btn("btn btn-small", "Remove", function () { cards.splice(i, 1); draw(); }));
        add(cl, wr);
      });
      add(cl, btn("btn btn-small", "+ Card", function () { cards.push(""); draw(); }));
      add(m, cl);
      add(m, p("Source: " + (w.source ? (w.from_file ? "from " + w.source : w.source) : "none"), "note"));
      var lines = el("div", "lines");
      (w.describe_lines || []).forEach(function (l) { add(lines, el("div", "", l)); });
      (w.warnings || []).forEach(function (l) { add(lines, el("div", "seg-caution", "! " + l)); });
      add(m, lines);
      if (err || w.error) { add(m, tid(p("Wallet error: " + (err || w.error), "field-error"), "wallet-error")); }
      var acts = el("div", "acts");
      add(acts, btn("btn", "Cancel", closeWallet), btn("btn btn-primary", "Apply for this session", apply, "wallet-apply"));
      add(m, acts, p("Not written to disk.", "note"));
    }
    function apply() {
      var balances = {};
      rows.forEach(function (r) { if (r.cur.trim()) { balances[r.cur.trim()] = r.bal.trim(); } });
      api("POST", "/api/wallet", { balances: balances, cards: cards.filter(function (c) { return c.trim(); }),
        valuations: vals }).then(function (res) {
        if (res.status === 200 && res.body && !res.body.error) {
          S.state.wallet = res.body; closeWallet(); refreshState(); render();
        } else {
          err = (res.body && res.body.message) || ("HTTP " + res.status); draw();
        }
      });
    }
    draw();
    add(sc, m); sc.hidden = false;
  }
  function closeWallet() { $("wallet-scrim").hidden = true; clear($("wallet-scrim")); }

  /* ----------------------------------------------------------------- boot */

  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") { return; }
    if (!$("scrim").hidden) { closeConfirm(); return; }
    if (!$("wallet-scrim").hidden) { closeWallet(); return; }
    if (S.view === "search" && S.search.sel) { closeSearchDrawer(); return; }
    if (S.legSel) { closeLegDrawer(); }
  });
  $("scrim").addEventListener("click", function (e) { if (e.target === $("scrim")) { closeConfirm(); } });
  $("wallet-scrim").addEventListener("click", function (e) { if (e.target === $("wallet-scrim")) { closeWallet(); } });
  $("tab-trips").addEventListener("click", function () { go(S.tripId ? "#trips/" + S.tripId : "#trips"); });
  $("tab-search").addEventListener("click", function () { go("#search"); });
  window.addEventListener("hashchange", onHash);

  refreshState().then(function (res) {
    if (res.status !== 200) { return; }
    if (S.state && S.state.today) { S.opts.transfer_date = ""; }
    loadTrips().then(function () {
      if (!location.hash && S.trips && S.trips.length) {
        var b = S.trips.filter(function (t) { return t.id === "trip_b_europe"; })[0] || S.trips[0];
        go("#trips/" + b.id);
      } else { onHash(); }
    });
  });
})();
