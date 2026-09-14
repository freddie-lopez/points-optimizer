/* Points Optimizer - the world map on the Search tab.

   One global, `window.POMap`: load() fetches the land path and the hub file,
   mount(host, {onPick}) builds the SVG into the host it is given, hub(code) and
   suggest(text, max) read the loaded set. This file never touches app.js's
   state or DOM outside that host.

   THE RULES THIS FILE KEEPS (the same as app.js):
   * DOM and SVG are built with createElement/createElementNS + textContent.
     Names, cities and codes come from a JSON file a tool wrote from Seats.aero
     answers and are hostile input by assumption.
   * A dot means "Seats.aero has routes touching this airport in several
     programmes at capture time" - never availability. The footer says so.
   * No data is a state with words: an empty hub file, an unreadable one and a
     row that failed validation are each counted and named, never drawn as an
     empty ocean that looks like an answer. */
(function () {
  "use strict";

  var NS = "http://www.w3.org/2000/svg";
  var IATA = /^[A-Z0-9]{3}$/;
  var ZMIN = 1, ZMAX = 8;
  var CLUSTER_PX = 18;
  var CLICK_PX = 4;
  var LABEL_ZOOM = 3;
  var ROUTE_POINTS = 64;

  var S_NO_DATA = "NO AIRPORT DATA - run \"python -m src.map_tools capture-hubs\" on your Mac. The map plots nothing until then.";
  var S_UNREADABLE_A = "AIRPORT DATA UNREADABLE - /static/hubs.json could not be read (";
  var S_UNREADABLE_B = "). The map plots nothing.";
  var S_IDLE = "Click an airport to set From.";
  var S_FROM = "Click an airport to set To.";
  var S_BOTH = "The line is your route, not availability. Award space appears only after the search runs.";
  var S_NOT_ON_MAP_A = " · ";
  var S_NOT_ON_MAP_B = " is not on this map. It can still be searched.";
  var S_LEGEND_DOT = "airport Seats.aero tracks";
  var S_LEGEND_CLUSTER = "several airports - click to list";
  var S_LEGEND_HOW = "Drag to pan · scroll to zoom";
  var S_POP_SUB_A = " airports Seats.aero tracks · pick one";
  var S_POP_NEAR = " airports near ";
  var S_ARIA_MAP = "World map of airports Seats.aero tracks";

  /* ------------------------------------------------------------- helpers */

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) { n.className = cls; }
    if (text !== undefined && text !== null) { n.textContent = String(text); }
    return n;
  }
  function svg(tag, attrs) {
    var n = document.createElementNS(NS, tag);
    if (attrs) { Object.keys(attrs).forEach(function (k) { n.setAttribute(k, String(attrs[k])); }); }
    return n;
  }
  function tid(n, id) { n.setAttribute("data-testid", id); return n; }
  function clear(n) { while (n.firstChild) { n.removeChild(n.firstChild); } return n; }
  function isNum(x) { return typeof x === "number" && isFinite(x); }
  function isInt(x) { return isNum(x) && Math.floor(x) === x; }
  function str(x) { return typeof x === "string" ? x : (x === null || x === undefined ? "" : String(x)); }
  function clamp(v, lo, hi) { return v < lo ? lo : (v > hi ? hi : v); }

  /* ---------------------------------------------------------- projection */
  /* The same four numbers tools/build_land_path.py used for the land. */

  function miller(latDeg) { return 1.25 * Math.log(Math.tan(Math.PI / 4 + 0.4 * latDeg * Math.PI / 180)); }
  function projector(land) {
    var top = miller(land.lat_max), bot = miller(land.lat_min);
    return function (lon, lat) {
      lat = clamp(lat, land.lat_min, land.lat_max);
      lon = clamp(lon, -180, 180);
      return { X: (lon + 180) / 360 * land.w, Y: (top - miller(lat)) / (top - bot) * land.h };
    };
  }

  /* ---------------------------------------------------------------- data */

  var data = null;        // resolved by load()
  var loading = null;

  function fetchJSON(path) {
    return fetch(path, { credentials: "same-origin" }).then(function (r) {
      if (!r.ok) { throw new Error("HTTP " + r.status); }
      return r.json().catch(function (e) { throw new Error(e && e.name ? e.name : "SyntaxError"); });
    }, function () { throw new Error("fetch failed"); });
  }

  function validLand(d) {
    return d && typeof d === "object" && isNum(d.w) && isNum(d.h) && isNum(d.lat_min) &&
      isNum(d.lat_max) && d.lat_max > d.lat_min && typeof d.d === "string" && d.d.charAt(0) === "M";
  }

  /* Every hub that fails a check is counted and named in the footer, never
     silently dropped and never plotted as if it had passed. */
  function readHubs(doc, proj) {
    var out = { plotted: [], byIata: {}, total: 0, notSearchable: 0, noCoords: 0, dropped: 0 };
    var hubs = doc.hubs;
    for (var i = 0; i < hubs.length; i++) {
      var h = hubs[i];
      out.total += 1;
      if (!h || typeof h !== "object" || Array.isArray(h)) { out.dropped += 1; continue; }
      var code = h.iata;
      if (typeof code !== "string" || !IATA.test(code) || out.byIata[code]) { out.dropped += 1; continue; }
      if (!isInt(h.routes) || h.routes < 1) { out.dropped += 1; continue; }
      if (!Array.isArray(h.sources) || h.sources.length < 1) { out.dropped += 1; continue; }
      if (h.searchable !== true) { out.notSearchable += 1; continue; }
      if (!isNum(h.lat) || !isNum(h.lon) || h.lat < -90 || h.lat > 90 || h.lon < -180 || h.lon > 180) {
        out.noCoords += 1; continue;
      }
      var pt = proj(h.lon, h.lat);
      var hub = {
        iata: code, name: str(h.name), city: str(h.city), country: str(h.country),
        lat: h.lat, lon: h.lon, routes: h.routes, X: pt.X, Y: pt.Y
      };
      hub.lname = hub.name.toLowerCase();
      hub.lcity = hub.city.toLowerCase();
      out.plotted.push(hub);
      out.byIata[code] = hub;
    }
    out.plotted.sort(function (a, b) { return b.routes - a.routes || (a.iata < b.iata ? -1 : 1); });
    return out;
  }

  function load() {
    if (loading) { return loading; }
    loading = fetchJSON("/static/land.json").then(function (land) {
      if (!validLand(land)) { throw new Error("land.json: not a land file"); }
      return fetchJSON("/static/hubs.json").then(function (doc) {
        if (!doc || typeof doc !== "object" || !Array.isArray(doc.hubs)) {
          return { land: land, error: "hubs is not a list", meta: null, set: null };
        }
        var meta = doc._meta && typeof doc._meta === "object" ? doc._meta : {};
        var set = readHubs(doc, projector(land));
        return { land: land, error: null, meta: meta, set: set };
      }, function (e) {
        return { land: land, error: e.message, meta: null, set: null };
      });
    }, function (e) {
      return { land: null, error: "land.json: " + e.message, meta: null, set: null };
    }).then(function (d) {
      data = d;
      d.hubs = d.set ? d.set.plotted : [];
      return d;
    });
    return loading;
  }

  function hub(code) {
    if (!data || !data.set) { return null; }
    var key = str(code).trim().toUpperCase();
    return data.set.byIata[key] || null;
  }

  /* exact code, code prefix, city prefix, name/city substring; ties by route
     count. The plotted set only: what the map offers, the keyboard offers. */
  function suggest(text, max) {
    if (!data || !data.set) { return []; }
    var q = str(text).trim().toLowerCase();
    if (!q) { return []; }
    var scored = [];
    data.set.plotted.forEach(function (h) {
      var code = h.iata.toLowerCase(), rank;
      if (code === q) { rank = 0; }
      else if (code.indexOf(q) === 0) { rank = 1; }
      else if (h.lcity.indexOf(q) === 0) { rank = 2; }
      else if (h.lname.indexOf(q) >= 0 || h.lcity.indexOf(q) >= 0) { rank = 3; }
      else { return; }
      scored.push({ rank: rank, hub: h });
    });
    scored.sort(function (a, b) {
      return a.rank - b.rank || b.hub.routes - a.hub.routes || (a.hub.iata < b.hub.iata ? -1 : 1);
    });
    return scored.slice(0, max === undefined ? 8 : max).map(function (s) { return s.hub; });
  }

  /* ---------------------------------------------------------- great circle */

  function greatCircle(a, b, proj) {
    var toR = Math.PI / 180;
    var la1 = a.lat * toR, lo1 = a.lon * toR, la2 = b.lat * toR, lo2 = b.lon * toR;
    var x1 = Math.cos(la1) * Math.cos(lo1), y1 = Math.cos(la1) * Math.sin(lo1), z1 = Math.sin(la1);
    var x2 = Math.cos(la2) * Math.cos(lo2), y2 = Math.cos(la2) * Math.sin(lo2), z2 = Math.sin(la2);
    var d = Math.acos(clamp(x1 * x2 + y1 * y2 + z1 * z2, -1, 1));
    var segs = [[]], prevLon = null;
    for (var i = 0; i <= ROUTE_POINTS; i++) {
      var t = i / ROUTE_POINTS, x, y, z;
      if (d < 1e-9) { x = x1; y = y1; z = z1; }
      else {
        var A = Math.sin((1 - t) * d) / Math.sin(d), B = Math.sin(t * d) / Math.sin(d);
        x = A * x1 + B * x2; y = A * y1 + B * y2; z = A * z1 + B * z2;
      }
      var lat = Math.atan2(z, Math.sqrt(x * x + y * y)) / toR;
      var lon = Math.atan2(y, x) / toR;
      if (prevLon !== null && Math.abs(lon - prevLon) > 180) { segs.push([]); }
      segs[segs.length - 1].push(proj(lon, lat));
      prevLon = lon;
    }
    return segs;
  }

  /* ---------------------------------------------------------------- mount */

  function mount(host, opts) {
    var onPick = opts && opts.onPick ? opts.onPick : function () {};
    var land = data && data.land;
    clear(host);
    host.classList.add("map-pane");

    // Without a land file there is no map to draw; the sentence is the pane.
    if (!land) {
      var only = tid(el("div", "map-status", S_UNREADABLE_A + data.error + S_UNREADABLE_B), "map-status");
      host.appendChild(only);
      return { setPicks: function () {}, closePopover: function () { return false; },
        popoverOpen: function () { return false; }, refresh: function () {}, view: function () { return null; } };
    }

    var W = land.w, H = land.h;
    var proj = projector(land);
    var view = { x: 0, y: 0, z: 1 };
    var picks = { from: null, to: null };
    var scaleK = 1;          // map units per screen px, at the current zoom
    var lastZoom = null;
    var raf = 0;
    var pop = null;          // the open popover's state
    var drag = null;

    var root = tid(svg("svg", { "class": "map-svg", role: "img", "aria-label": S_ARIA_MAP,
      preserveAspectRatio: "xMidYMid meet" }), "map-svg");
    var landPath = tid(svg("path", { id: "map-land", "class": "map-land", d: land.d }), "map-land");
    var routeLayer = svg("g", { "class": "map-routes" });
    var markerLayer = svg("g", { "class": "map-markers" });
    root.appendChild(landPath); root.appendChild(routeLayer); root.appendChild(markerLayer);
    host.appendChild(root);

    var ctl = el("div", "map-ctl");
    function ctlBtn(label, testid, glyph, onClick) {
      var b = el("button", "mapbtn", glyph);
      b.type = "button"; b.setAttribute("aria-label", label); b.title = label;
      tid(b, testid); b.addEventListener("click", onClick);
      return b;
    }
    ctl.appendChild(ctlBtn("Zoom in", "map-zoom-in", "+", function () { zoomBy(1.5, centre()); }));
    ctl.appendChild(ctlBtn("Zoom out", "map-zoom-out", "−", function () { zoomBy(1 / 1.5, centre()); }));
    ctl.appendChild(ctlBtn("Reset view", "map-reset", "◎", function () { setView(0, 0, 1); }));
    host.appendChild(ctl);

    var popEl = tid(el("div", "map-pop"), "map-cluster-list");
    popEl.setAttribute("role", "listbox"); popEl.hidden = true;
    host.appendChild(popEl);

    var foot = el("div", "map-foot");
    var status = tid(el("div", "map-status"), "map-status");
    var legend = el("div", "map-legend");
    var l1 = el("span"); l1.appendChild(el("span", "lg-dot")); l1.appendChild(document.createTextNode(S_LEGEND_DOT));
    var l2 = el("span"); l2.appendChild(el("span", "lg-cl", "7")); l2.appendChild(document.createTextNode(S_LEGEND_CLUSTER));
    legend.appendChild(l1); legend.appendChild(l2); legend.appendChild(el("span", "", S_LEGEND_HOW));
    var prov = tid(el("div", "map-provenance"), "map-provenance");
    foot.appendChild(status); foot.appendChild(legend); foot.appendChild(prov);
    host.appendChild(foot);

    /* ---- view ---- */

    function applyView() {
      root.setAttribute("viewBox", view.x + " " + view.y + " " + (W / view.z) + " " + (H / view.z));
    }
    function setView(x, y, z) {
      var zoomed = z !== view.z;
      view.z = clamp(z, ZMIN, ZMAX);
      view.x = clamp(x, 0, W - W / view.z);
      view.y = clamp(y, 0, H - H / view.z);
      applyView();
      if (zoomed) { scheduleRecluster(); }
      if (pop) { closePopover(false); }
    }
    function centre() { return { x: view.x + W / view.z / 2, y: view.y + H / view.z / 2 }; }
    function zoomBy(f, about) {
      var z2 = clamp(view.z * f, ZMIN, ZMAX);
      var fe = z2 / view.z;
      setView(about.x - (about.x - view.x) / fe, about.y - (about.y - view.y) / fe, z2);
    }
    // Screen -> map units, from the SVG element's own matrix (never a <g>'s).
    function ctm() {
      var m = root.getScreenCTM();
      return m && m.a > 0 ? m : null;
    }
    function toMap(clientX, clientY) {
      var m = ctm();
      if (!m) { return null; }
      var pt = root.createSVGPoint(); pt.x = clientX; pt.y = clientY;
      var p = pt.matrixTransform(m.inverse());
      return { x: p.x, y: p.y };
    }
    function toScreen(X, Y) {
      var m = ctm();
      if (!m) { return null; }
      var pt = root.createSVGPoint(); pt.x = X; pt.y = Y;
      var p = pt.matrixTransform(m);
      return { x: p.x, y: p.y };
    }

    root.addEventListener("wheel", function (e) {
      e.preventDefault();
      var about = toMap(e.clientX, e.clientY);
      if (!about) { return; }
      zoomBy(Math.pow(1.2, -e.deltaY / 100), about);
    }, { passive: false });
    root.addEventListener("dblclick", function (e) {
      e.preventDefault();
      var about = toMap(e.clientX, e.clientY);
      if (about) { zoomBy(2, about); }
    });
    root.addEventListener("pointerdown", function (e) {
      if (e.button !== 0) { return; }
      var m = ctm();
      if (!m) { return; }
      // The marker under the pointer is read HERE: once the pointer is
      // captured, later events are retargeted to the SVG itself.
      var hit = e.target && e.target.closest ? e.target.closest("g.mk") : null;
      drag = { sx: e.clientX, sy: e.clientY, vx: view.x, vy: view.y, k: 1 / m.a, moved: false,
        id: e.pointerId, hit: hit };
      try { root.setPointerCapture(e.pointerId); } catch (err) { /* not capturable */ }
    });
    root.addEventListener("pointermove", function (e) {
      if (!drag || e.pointerId !== drag.id) { return; }
      var dx = e.clientX - drag.sx, dy = e.clientY - drag.sy;
      if (!drag.moved && Math.abs(dx) < CLICK_PX && Math.abs(dy) < CLICK_PX) { return; }
      if (!drag.moved) { drag.moved = true; root.classList.add("dragging"); if (pop) { closePopover(false); } }
      view.x = clamp(drag.vx - dx * drag.k, 0, W - W / view.z);
      view.y = clamp(drag.vy - dy * drag.k, 0, H - H / view.z);
      applyView();
    });
    function endDrag(e) {
      if (!drag || e.pointerId !== drag.id) { return; }
      var wasClick = !drag.moved, hit = drag.hit;
      drag = null;
      root.classList.remove("dragging");
      try { root.releasePointerCapture(e.pointerId); } catch (err) { /* already released */ }
      if (wasClick && e.type === "pointerup" && hit && hit.isConnected) { markerActivated(hit); }
    }
    root.addEventListener("pointerup", endDrag);
    root.addEventListener("pointercancel", endDrag);
    root.addEventListener("keydown", function (e) {
      if (e.key !== "Enter" && e.key !== " ") { return; }
      var g = e.target && e.target.closest ? e.target.closest("g.mk") : null;
      if (!g) { return; }
      e.preventDefault();
      markerActivated(g);
    });
    window.addEventListener("resize", function () { lastZoom = null; scheduleRecluster(); });

    /* ---- markers and clusters ---- */

    var clusters = [];   // [{X, Y, members: [hub], lead: hub, el}]

    function scheduleRecluster() {
      if (raf) { return; }
      raf = window.requestAnimationFrame(function () { raf = 0; recluster(); });
    }

    function isPicked(h) { return h === picks.from || h === picks.to; }

    function recluster() {
      var m = ctm();
      if (!m) { lastZoom = null; return; }      // hidden pane: nothing to measure
      var s = m.a;                                // screen px per map unit
      scaleK = 1 / s;
      lastZoom = view.z;
      clusters = [];
      var hubs = data.set ? data.set.plotted : [];
      var pickedHubs = [];
      for (var i = 0; i < hubs.length; i++) {
        var h = hubs[i];
        if (isPicked(h)) { pickedHubs.push(h); continue; }
        var joined = false;
        for (var j = 0; j < clusters.length; j++) {
          var c = clusters[j];
          if (Math.abs((c.X - h.X) * s) <= CLUSTER_PX && Math.abs((c.Y - h.Y) * s) <= CLUSTER_PX) {
            c.members.push(h); joined = true; break;
          }
        }
        if (!joined) { clusters.push({ X: h.X, Y: h.Y, members: [h], lead: h }); }
      }
      clear(markerLayer);
      clusters.forEach(function (c) {
        c.el = c.members.length === 1 ? singleMarker(c.members[0]) : clusterMarker(c);
        markerLayer.appendChild(c.el);
      });
      pickedHubs.forEach(function (h) { markerLayer.appendChild(singleMarker(h)); });
      drawRoute();
    }

    function place(g, X, Y) { g.setAttribute("transform", "translate(" + X + " " + Y + ") scale(" + scaleK + ")"); }

    function singleMarker(h) {
      var picked = isPicked(h);
      var g = tid(svg("g", { "class": "mk" + (picked ? " pick" : ""), "data-iata": h.iata, tabindex: 0 }),
        "map-hub-" + h.iata);
      g.setAttribute("role", "button");
      g.setAttribute("aria-label", h.iata + " " + h.name);
      place(g, h.X, h.Y);
      if (picked) { g.appendChild(svg("circle", { "class": "halo", r: 8.3 })); }
      g.appendChild(svg("circle", { "class": "dot", r: picked ? 4.2 : 3.2 }));
      if (picked || view.z >= LABEL_ZOOM) {
        var t = svg("text", { "class": "lbl", x: 7, y: 4 });
        t.textContent = h.iata;
        g.appendChild(t);
      }
      return g;
    }

    function clusterMarker(c) {
      var g = tid(svg("g", { "class": "mk cl", "data-iata": c.lead.iata, tabindex: 0 }),
        "map-cluster-" + c.lead.iata);
      g.setAttribute("role", "button");
      g.setAttribute("aria-label", c.members.length + S_POP_SUB_A);
      place(g, c.X, c.Y);
      g.appendChild(svg("circle", { "class": "cdot", r: 9 }));
      var t = svg("text", { "class": "cnt", x: 0, y: 3.2, "text-anchor": "middle" });
      t.textContent = String(c.members.length);
      g.appendChild(t);
      return g;
    }

    function markerActivated(g) {
      var code = g.getAttribute("data-iata");
      if (g.classList.contains("cl")) {
        for (var i = 0; i < clusters.length; i++) {
          if (clusters[i].el === g) { openPopover(clusters[i]); return; }
        }
        return;
      }
      onPick(code);
    }

    /* ---- route line ---- */

    function drawRoute() {
      clear(routeLayer);
      if (!picks.from || !picks.to || picks.from === picks.to) { return; }
      var segs = greatCircle(picks.from, picks.to, proj);
      var d = segs.map(function (seg) {
        return seg.map(function (p, i) {
          return (i === 0 ? "M" : "L") + p.X.toFixed(1) + "," + p.Y.toFixed(1);
        }).join("");
      }).join("");
      routeLayer.appendChild(tid(svg("path", { "class": "route", d: d }), "map-route"));
    }

    /* ---- popover ---- */

    function openPopover(c) {
      closePopover(false);
      var members = c.members.slice().sort(function (a, b) { return b.routes - a.routes; });
      var sameCity = members.every(function (h) { return h.city && h.city === members[0].city; });
      var title = sameCity ? members[0].city : (members.length + S_POP_NEAR + (c.lead.city || c.lead.iata));
      clear(popEl);
      var head = el("div", "ph");
      var tt = el("div");
      tt.appendChild(el("div", "pt", title));
      tt.appendChild(el("div", "ps", members.length + S_POP_SUB_A));
      var x = el("button", "x", "×");
      x.type = "button"; x.setAttribute("aria-label", "Close list");
      x.addEventListener("click", function () { closePopover(true); });
      head.appendChild(tt); head.appendChild(x);
      popEl.appendChild(head);
      var list = el("div", "pl");
      members.forEach(function (h) {
        var b = tid(el("button", "prow"), "map-pick-" + h.iata);
        b.type = "button"; b.setAttribute("role", "option"); b.title = h.name;
        var main = el("span", "pm");
        main.appendChild(el("span", "mono", h.iata));
        main.appendChild(document.createTextNode(" " + h.name));
        b.appendChild(main);
        b.appendChild(el("span", "dim", h.city + (h.city && h.country ? ", " : "") + h.country));
        b.addEventListener("click", function () {
          var code = h.iata;
          closePopover(true);
          onPick(code);
        });
        list.appendChild(b);
      });
      popEl.appendChild(list);
      popEl.setAttribute("aria-label", title);
      popEl.hidden = false;
      pop = { cluster: c, marker: c.el };
      // Position at the cluster's screen point, clamped 8px inside the pane.
      var sp = toScreen(c.X, c.Y), pr = host.getBoundingClientRect();
      var left = sp ? sp.x - pr.left + 12 : 16, top = sp ? sp.y - pr.top + 12 : 16;
      var pw = popEl.offsetWidth, ph = popEl.offsetHeight;
      left = clamp(left, 8, Math.max(8, pr.width - pw - 8));
      top = clamp(top, 8, Math.max(8, pr.height - ph - 8));
      popEl.style.left = left + "px"; popEl.style.top = top + "px";
      var first = list.firstChild;
      if (first) { first.focus(); }
    }

    function closePopover(refocus) {
      if (!pop) { return false; }
      var marker = pop.marker;
      pop = null;
      popEl.hidden = true; clear(popEl);
      if (refocus && marker && marker.isConnected) { marker.focus(); }
      return true;
    }

    popEl.addEventListener("keydown", function (e) {
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closePopover(true); return; }
      if (e.key !== "ArrowDown" && e.key !== "ArrowUp") { return; }
      var rows = Array.prototype.slice.call(popEl.querySelectorAll(".prow"));
      var i = rows.indexOf(document.activeElement);
      if (i < 0) { return; }
      e.preventDefault();
      var j = e.key === "ArrowDown" ? Math.min(rows.length - 1, i + 1) : Math.max(0, i - 1);
      rows[j].focus();
    });

    /* ---- status and provenance ---- */

    // Only a code-shaped value gets the sentence: "SF" mid-typing is not a
    // code the map does not know, and "San Fran" is not one the engine takes.
    function notOnMap(typed, h) {
      var code = str(typed).trim();
      return !h && /^[A-Za-z0-9]{3}$/.test(code) ? S_NOT_ON_MAP_A + code.toUpperCase() + S_NOT_ON_MAP_B : "";
    }

    function setPicks(fromHub, toHub, fromTyped, toTyped) {
      picks.from = fromHub || null;
      picks.to = toHub || null;
      var text;
      if (data.error) {
        text = S_UNREADABLE_A + data.error + S_UNREADABLE_B;
      } else if (data.set.total === 0) {
        text = S_NO_DATA;
      } else {
        if (picks.from && picks.to) { text = S_BOTH; }
        else if (!str(fromTyped).trim()) { text = S_IDLE; }
        else { text = S_FROM; }
        text += notOnMap(fromTyped, picks.from) + notOnMap(toTyped, picks.to);
      }
      status.textContent = text;
      renderProvenance();
      recluster();
    }

    function renderProvenance() {
      var set = data.set;
      if (!set || set.total === 0) { prov.textContent = ""; prov.hidden = true; return; }
      var when = typeof data.meta.captured_at === "string" ? data.meta.captured_at.slice(0, 10) : "an unknown date";
      var k = set.total - set.plotted.length;
      var text = set.total + " airports Seats.aero tracked on " + when + " · " + k + " not plotted";
      if (k > 0) {
        text += " (" + set.notSearchable + " not in data/airports.csv, " + set.noCoords + " without coordinates)";
      }
      prov.textContent = text; prov.hidden = false;
    }

    applyView();
    setPicks(null, null, "", "");

    return {
      setPicks: setPicks,
      closePopover: function () { return closePopover(true); },
      popoverOpen: function () { return !!pop; },
      refresh: function () { if (lastZoom !== view.z) { recluster(); } else { scheduleRecluster(); } },
      zoomBy: zoomBy,
      setView: setView,
      view: function () { return { x: view.x, y: view.y, z: view.z }; },
      clusters: function () { return clusters.map(function (c) { return c.members.map(function (h) { return h.iata; }); }); }
    };
  }

  window.POMap = { load: load, mount: mount, hub: hub, suggest: suggest };
})();
