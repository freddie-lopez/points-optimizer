/*
 * Drive the real page in headless Chromium and dump what the DOM actually says.
 *
 *   node drive.js '<json spec>'
 *
 * spec = {
 *   port, kind: "trip"|"search", trip, mode, options?, search?,
 *   width, height, shots: "dir"|null, name, openAllLegs: bool, evil: "url"|null
 * }
 *
 * Everything is READ FROM THE DOM the user sees: cell text, chips, drawer text,
 * the transcript. No JSON from the API is trusted here. Prints one JSON object
 * on stdout.
 */
const { chromium } = require("/home/claude/.npm-global/lib/node_modules/playwright");

const spec = JSON.parse(process.argv[2]);
const BASE = "http://127.0.0.1:" + spec.port + "/";

function q(t) { return '[data-testid="' + t + '"]'; }

async function main() {
  const browser = await chromium.launch({
    executablePath: "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
    args: [
      "--no-sandbox", "--disable-dev-shm-usage",
      // NOTHING may leave this machine. Every name but loopback fails to
      // resolve, the browser's own background traffic is off, and any request
      // that is not to the probe server is aborted below and recorded.
      "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1",
      "--disable-background-networking", "--disable-component-update",
      "--disable-sync", "--no-first-run", "--no-default-browser-check",
      "--disable-default-apps", "--metrics-recording-only", "--no-pings",
      "--disable-features=OptimizationHints,MediaRouter,Translate",
      // The sandbox sets HTTPS_PROXY; Chromium would honour it and dial the
      // proxy for every name, which is a real connection to a real host.
      "--no-proxy-server",
    ],
    env: Object.assign({}, process.env, {
      HTTPS_PROXY: "", HTTP_PROXY: "", https_proxy: "", http_proxy: "",
      NO_PROXY: "*", no_proxy: "*",
    }),
  });
  const ctx = await browser.newContext({
    viewport: { width: spec.width || 1440, height: spec.height || 1000 },
  });
  const offsite = [];
  await ctx.route("**/*", (r) => {
    const u = r.request().url();
    if (u.startsWith("http://127.0.0.1:") || u.startsWith("data:") || u === "about:blank") {
      return r.continue();
    }
    offsite.push(u);
    return r.abort();
  });
  const page = await ctx.newPage();
  const errors = [];
  const console_ = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => { if (m.type() === "error") console_.push(m.text()); });
  const out = { errors, console: console_, scenario: spec.name, offsite };

  await page.goto(BASE, { waitUntil: "domcontentloaded" });
  await page.waitForSelector(q("wordmark"), { timeout: 20000 });
  await page.waitForTimeout(300);

  out.topbar = await text(page, ".topbar");

  if (spec.kind === "search") {
    await page.click(q("tab-search"));
    await page.waitForTimeout(150);
    const s = spec.search || {};
    await fill(page, "search-from", s.origin || "SFO");
    await fill(page, "search-to", s.destination || "MAD");
    await fill(page, "search-date", s.date || "2027-01-15");
    if (s.date_to) await fill(page, "search-date-to", s.date_to);
    await page.click(q("search-run"));
    await page.waitForTimeout(400);
    if (await page.$(q("search-confirm-go"))) {
      out.confirm = await text(page, q("search-confirm"));
      await page.click(q("search-confirm-go"));
    }
    await page.waitForTimeout(spec.wait || 3000);
    await page.waitForSelector(q("search-table") + "," + q("search-state"),
                               { timeout: 60000 }).catch(() => {});
    await page.waitForTimeout(400);
    out.state = await text(page, q("search-state"));
    out.tableText = await text(page, q("search-table"));
    out.cells = await page.evaluate(() => {
      const rows = [];
      document.querySelectorAll('[data-testid="search-table"] tbody tr').forEach((tr) => {
        const cells = [];
        tr.querySelectorAll("td").forEach((td) => cells.push({
          testid: td.getAttribute("data-testid"), text: td.innerText,
          kind: td.getAttribute("data-kind"),
        }));
        rows.push(cells);
      });
      return rows;
    });
    out.footer = await text(page, q("search-footer"));
    // open the first cell that holds an award (not a "no space" cell)
    const cells = await page.$$('[data-testid^="cell-"]');
    for (const c of cells) {
      const t = (await c.innerText()).trim();
      if (!t || /^no space$/i.test(t)) continue;
      await c.click().catch(() => {});
      await page.waitForTimeout(600);
      out.drawer = await text(page, "#drawer-search");
      if (!out.drawer) {          // one retry: the table may have re-rendered
        await page.waitForTimeout(600);
        await c.click().catch(() => {});
        await page.waitForTimeout(700);
        out.drawer = await text(page, "#drawer-search");
      }
      if (out.drawer) break;
    }
  } else if (spec.kind === "trip") {
    await page.click(q("tab-trips"));
    await page.waitForTimeout(200);
    out.tripList = await text(page, q("trip-list"));
    await page.click(q("trip-row-" + spec.trip));
    await page.waitForSelector(q("trip-detail"), { timeout: 15000 });
    out.tripDetail = await text(page, q("trip-detail"));
    if (spec.options) {
      const ob = await page.$(q("run-options"));
      if (ob) await ob.click().catch(() => {});
    }
    await page.click(q("mode-" + (spec.mode || "offline")));
    await page.waitForTimeout(200);
    out.runStrip = await text(page, q("run-strip"));
    out.runStripBox = await box(page, q("run-strip"));
    await page.click(q("run-go"));
    await page.waitForTimeout(500);
    if (await page.$(q("run-confirm-go"))) {
      out.confirm = await text(page, q("run-confirm"));
      await page.click(q("run-confirm-go"));
    }
    await page.waitForFunction(() => {
      const r = document.querySelector('[data-testid="trip-result"]');
      const b = document.querySelector('[data-testid="banner-error"]');
      return !!r || (b && !b.hidden);
    }, null, { timeout: 120000 });
    await page.waitForTimeout(600);
    out.result = await text(page, q("trip-result"));
    out.banner = await text(page, q("banner-error"));
    out.headline = await text(page, q("headline"));
    out.headlineValue = await text(page, q("headline-value"));
    out.exitChip = await text(page, q("exit-chip"));
    out.calls = await text(page, q("run-calls"));
    out.refusal = await text(page, q("refusal"));
    out.transcript = await page.evaluate(() => {
      const t = document.querySelector('[data-testid="transcript"] pre');
      return t ? t.textContent : null;
    });
    out.rows = await page.evaluate(() => {
      const rows = [];
      document.querySelectorAll('[data-testid^="leg-row-"]').forEach((tr) => {
        const id = tr.getAttribute("data-testid").replace("leg-row-", "");
        const cells = {};
        tr.querySelectorAll("td").forEach((td) => {
          const tid = td.getAttribute("data-testid") || "";
          const name = tid.startsWith("verdict-") ? "verdict"
            : tid.replace("cell-" + id + "-", "");
          cells[name] = { text: td.innerText, kind: td.getAttribute("data-kind") };
        });
        rows.push({ id: id, cells: cells });
      });
      return rows;
    });
    out.legsTableScroll = await page.evaluate(() => {
      const n = document.querySelector('[data-testid="legs-table-scroll"]');
      if (!n) return null;
      const r = n.getBoundingClientRect();
      const v = document.querySelector('[data-testid^="verdict-"]');
      const vr = v ? v.getBoundingClientRect() : null;
      return { sw: n.scrollWidth, cw: n.clientWidth, left: r.left, right: r.right,
        scrollLeft: n.scrollLeft,
        verdict: vr ? { left: vr.left, right: vr.right, visible: vr.right <= r.right + 1 } : null };
    });
    out.drawers = {};
    if (spec.openAllLegs) {
      for (const r of out.rows) {
        // close the sheet first: at phone width it covers the table
        await page.keyboard.press("Escape");
        await page.waitForTimeout(120);
        await page.click(q("leg-row-" + r.id));
        await page.waitForTimeout(250);
        out.drawers[r.id] = await text(page, "#drawer-trips");
      }
    }
  } else if (spec.kind === "newtrip") {
    await page.click(q("tab-trips"));
    await page.waitForTimeout(150);
    await page.click(q("new-trip"));
    await page.waitForSelector(q("new-trip-form"), { timeout: 10000 });
    const f = spec.form || {};
    await fill(page, "nt-name", f.name || "probe_trip");
    await fill(page, "nt-leg-1-origin", f.origin || "SFO");
    await fill(page, "nt-leg-1-destination", f.destination || "LHR");
    await fill(page, "nt-leg-1-date", f.date || "2027-01-15");
    await fill(page, "nt-leg-1-cash", f.cash === undefined ? "2400" : f.cash);
    await page.click(q("nt-preview"));
    await page.waitForTimeout(500);
    out.form = await text(page, q("new-trip-form"));
    out.echo = await text(page, q("nt-echo"));
    if (spec.write && await page.$(q("nt-write"))) {
      await page.click(q("nt-write"));
      await page.waitForTimeout(800);
      out.afterWrite = await text(page, "main");
    }
  }

  // page-level horizontal scroll (the phone-width rule)
  out.doc = await page.evaluate(() => ({
    sw: document.documentElement.scrollWidth,
    cw: document.documentElement.clientWidth,
    bodySW: document.body.scrollWidth,
  }));
  out.drawerBox = await box(page, "#drawer-trips");
  out.pwned = await page.evaluate(() => !!window.__pwned);
  out.title = await page.title();

  if (spec.shots) {
    await page.screenshot({ path: spec.shots + "/" + spec.name + ".png", fullPage: false });
  }
  if (spec.keyboard) {
    out.keyboard = await keyboardProbe(page);
  }
  await browser.close();
  process.stdout.write(JSON.stringify(out));
}

async function keyboardProbe(page) {
  const res = {};
  const rows = await page.$$('[data-testid^="leg-row-"]');
  if (rows.length) {
    await rows[1].focus();
    res.focusOutline = await page.evaluate(() => {
      const a = document.activeElement;
      const cs = getComputedStyle(a);
      return { tag: a.tagName, testid: a.getAttribute("data-testid"),
        outline: cs.outlineWidth + " " + cs.outlineStyle + " " + cs.outlineColor };
    });
    await page.keyboard.press("Enter");
    await page.waitForTimeout(400);
    res.afterEnterDrawerHidden = await page.evaluate(() =>
      document.querySelector("#drawer-trips").hidden);
    res.afterEnterFocus = await page.evaluate(() => {
      const a = document.activeElement;
      return a ? (a.getAttribute("aria-label") || a.className || a.tagName) : null;
    });
    await page.keyboard.press("Escape");
    await page.waitForTimeout(400);
    res.afterEscapeDrawerHidden = await page.evaluate(() =>
      document.querySelector("#drawer-trips").hidden);
  }
  return res;
}

async function text(page, sel) {
  return await page.evaluate((s) => {
    const n = document.querySelector(s);
    if (!n || n.hidden) return null;
    return n.innerText;
  }, sel);
}

async function box(page, sel) {
  return await page.evaluate((s) => {
    const n = document.querySelector(s);
    if (!n || n.hidden) return null;
    const r = n.getBoundingClientRect();
    const cs = getComputedStyle(n);
    return { x: r.x, y: r.y, w: r.width, h: r.height, position: cs.position,
      display: cs.display };
  }, sel);
}

async function fill(page, testid, value) {
  const n = await page.$(q(testid));
  if (!n) throw new Error("no field " + testid);
  await n.fill(String(value));
}

main().catch((e) => { process.stdout.write(JSON.stringify({ fatal: String(e) })); process.exit(3); });
