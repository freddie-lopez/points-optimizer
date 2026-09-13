/*
 * A HOSTILE PAGE on another origin, in the same browser, doing everything a
 * real one could do to a server on 127.0.0.1: fetch (with and without CORS),
 * a form POST that needs no preflight, an image GET, a script include, and a
 * window.open of the app's own page to try to read its token.
 *
 *   node csrf.js '{"port": <ui port>, "evil": <attacker port>}'
 *
 * Prints what the attacker learned. The probe then asks the server how many
 * Seats.aero calls this cost.
 */
const { chromium } = require("/home/claude/.npm-global/lib/node_modules/playwright");
const spec = JSON.parse(process.argv[2]);
const UI = "http://127.0.0.1:" + spec.port;
const EVIL = "http://127.0.0.1:" + spec.evil + "/evil.html";

(async () => {
  const browser = await chromium.launch({
    executablePath: "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
    args: ["--no-sandbox", "--disable-dev-shm-usage", "--no-proxy-server",
      "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1",
      "--disable-background-networking", "--disable-component-update", "--no-first-run"],
    env: Object.assign({}, process.env, {HTTPS_PROXY: "", HTTP_PROXY: "", https_proxy: "",
      http_proxy: "", NO_PROXY: "*"}),
  });
  const ctx = await browser.newContext({ viewport: { width: 900, height: 700 } });
  const page = await ctx.newPage();
  await page.goto(EVIL, { waitUntil: "domcontentloaded" });
  const out = await page.evaluate(async (ui) => {
    const res = { attempts: [] };
    async function try_(label, fn) {
      try { res.attempts.push([label, "ok", await fn()]); }
      catch (e) { res.attempts.push([label, "blocked", String(e).slice(0, 120)]); }
    }
    // 1. plain cross-origin GET of the app page (to steal the token)
    await try_("read the app page", async () => {
      const r = await fetch(ui + "/", { credentials: "include" });
      return (await r.text()).slice(0, 80);
    });
    // 2. GET /api/state with a guessed token
    await try_("GET /api/state with a guessed token", async () => {
      const r = await fetch(ui + "/api/state", { headers: { "X-PO-Token": "guess" } });
      return r.status + " " + (await r.text()).slice(0, 60);
    });
    // 3. a SIMPLE POST (no preflight): text/plain body that is valid JSON
    await try_("simple POST /api/search/run (text/plain)", async () => {
      const r = await fetch(ui + "/api/search/run", {
        method: "POST", mode: "no-cors", headers: { "Content-Type": "text/plain" },
        body: JSON.stringify({ origin: "SFO", destination: "MAD", date: "2027-01-15" }),
      });
      return "type=" + r.type + " status=" + r.status;
    });
    // 4. an image GET (no token, no CORS)
    await try_("img GET /api/trips", () => new Promise((resolve, reject) => {
      const i = new Image();
      i.onload = () => resolve("loaded");
      i.onerror = () => reject(new Error("img error (refused or not an image)"));
      i.src = ui + "/api/trips";
      setTimeout(() => reject(new Error("img timeout")), 3000);
    }));
    // 5. a script include (would run in the attacker's page if it were JS)
    await try_("script include of /static/app.js", () => new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.onload = () => resolve("executed in the attacker page");
      s.onerror = () => reject(new Error("script error"));
      s.src = ui + "/static/app.js";
      document.head.appendChild(s);
      setTimeout(() => reject(new Error("script timeout")), 3000);
    }));
    return res;
  }, UI);

  // 6. a real form POST (top-level navigation, no preflight at all)
  await page.evaluate((ui) => {
    const f = document.createElement("form");
    f.method = "POST";
    f.action = ui + "/api/trips/trip_b_europe/run";
    f.enctype = "text/plain";
    const i = document.createElement("input");
    i.name = '{"mode": "live", "options": {"transfer_date": "2026-09-15"}, "x": "';
    i.value = '"}';
    f.appendChild(i);
    document.body.appendChild(f);
    f.submit();
  }, UI);
  await page.waitForTimeout(1500);
  out.afterFormPost = (await page.content()).slice(0, 300);

  // 7. open the app in a second tab and try to read its DOM (same browser)
  const tab = await ctx.newPage();
  await tab.goto(UI + "/", { waitUntil: "domcontentloaded" });
  await tab.waitForTimeout(500);
  out.tokenInPage = await tab.evaluate(() => {
    const m = document.querySelector('meta[name="po-token"]');
    return m ? m.getAttribute("content").slice(0, 8) + "…" : null;
  });
  out.evilPageText = await page.evaluate(() => document.documentElement.outerHTML);
  out.crossTabRead = await page.evaluate(() => {
    try {
      const w = window.open("", "");
      return w ? "opened" : "blocked";
    } catch (e) { return "threw"; }
  });
  await browser.close();
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { process.stdout.write(JSON.stringify({ fatal: String(e) })); process.exit(3); });
