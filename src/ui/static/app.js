/* Points Optimizer - local UI. Vanilla JS, no build step.
   DOM is built with createElement + textContent ONLY (docs/plans/ui.md 4.4).
   Every number shown comes from the API; an unknown arrives as null with a
   status beside it and is drawn as the UNKNOWN chip - never blank, never a
   dash, never $0. */
(function () {
  "use strict";

  var TOKEN = (document.querySelector('meta[name="po-token"]') || {}).content || "";
  var S = { state: null };

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
      parent.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    }
    return parent;
  }
  function clear(n) { while (n.firstChild) { n.removeChild(n.firstChild); } return n; }
  function $(id) { return document.getElementById(id); }

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
      throw new Error("offline");
    });
  }

  function showBanner(text) { var b = $("banner-error"); b.textContent = text; b.hidden = false; }

  function renderTopbar() {
    var st = S.state;
    $("key-source").textContent = "key: " + (st.key.found ? st.key.source : "not found");
  }

  function boot() {
    api("GET", "/api/state").then(function (res) {
      if (res.status !== 200) { showBanner((res.body && res.body.message) || ("HTTP " + res.status)); return; }
      S.state = res.body;
      renderTopbar();
    });
  }

  boot();
})();
