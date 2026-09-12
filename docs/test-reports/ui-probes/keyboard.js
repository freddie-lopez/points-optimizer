/*
 * Keyboard-only walk of a scored trip: TAB to a leg row, ENTER to open it,
 * ESC to close it, with the focus ring measured as the browser paints it
 * (`:focus-visible`, which a programmatic .focus() does not satisfy).
 *
 *   node keyboard.js '<json spec>'   // {port, trip, mode, width}
 */
const { chromium } = require("/home/claude/.npm-global/lib/node_modules/playwright");
const spec = JSON.parse(process.argv[2]);
function q(t) { return '[data-testid="' + t + '"]'; }

async function main() {
  const browser = await chromium.launch({
    executablePath: "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
    args: ["--no-sandbox", "--disable-dev-shm-usage",
      "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1",
      "--disable-background-networking", "--disable-component-update", "--no-first-run",
      "--no-proxy-server"],
    env: Object.assign({}, process.env, {HTTPS_PROXY:"", HTTP_PROXY:"", https_proxy:"", http_proxy:"", NO_PROXY:"*"}),
  });
  const ctx = await browser.newContext({ viewport: { width: spec.width || 1440, height: 1000 } });
  const offsite = [];
  await ctx.route("**/*", (r) => {
    const u = r.request().url();
    if (u.startsWith("http://127.0.0.1:")) return r.continue();
    offsite.push(u); return r.abort();
  });
  const page = await ctx.newPage();
  await page.goto("http://127.0.0.1:" + spec.port + "/", { waitUntil: "domcontentloaded" });
  await page.waitForSelector(q("wordmark"));
  await page.click(q("trip-row-" + spec.trip));
  await page.waitForSelector(q("trip-detail"));
  await page.click(q("mode-" + (spec.mode || "offline")));
  await page.click(q("run-go"));
  await page.waitForSelector(q("trip-result"), { timeout: 120000 });
  await page.waitForTimeout(500);

  const out = { offsite };
  // TAB until a leg row has focus (or give up after 200 stops)
  let steps = 0;
  let cur = null;
  for (; steps < 200; steps++) {
    await page.keyboard.press("Tab");
    cur = await page.evaluate(() => {
      const a = document.activeElement;
      return a ? { testid: a.getAttribute("data-testid"), tag: a.tagName } : null;
    });
    if (cur && cur.testid && cur.testid.startsWith("leg-row-")) break;
  }
  out.tabsToFirstLegRow = steps + 1;
  out.focused = cur;
  if (cur && cur.testid && cur.testid.startsWith("leg-row-")) {
    out.ring = await page.evaluate(() => {
      const a = document.activeElement;
      const cs = getComputedStyle(a);
      return { outlineWidth: cs.outlineWidth, outlineStyle: cs.outlineStyle,
        outlineColor: cs.outlineColor, matchesFocusVisible: a.matches(":focus-visible") };
    });
    await page.keyboard.press("Enter");
    await page.waitForTimeout(500);
    out.afterEnter = await page.evaluate(() => {
      const d = document.querySelector("#drawer-trips");
      const a = document.activeElement;
      return { drawerHidden: d.hidden, drawerText: d.hidden ? null : d.innerText.slice(0, 60),
        focus: a ? (a.getAttribute("aria-label") || a.className || a.tagName) : null };
    });
    // Esc straight after Enter: where does focus go?
    await page.keyboard.press("Escape");
    await page.waitForTimeout(350);
    out.escFocus = await page.evaluate(() => {
      const a = document.activeElement;
      return { drawerHidden: document.querySelector("#drawer-trips").hidden,
        testid: a ? a.getAttribute("data-testid") : null,
        tag: a ? a.tagName : null,
        backOnTheRow: !!(a && (a.getAttribute("data-testid") || "").startsWith("leg-row-")) };
    });
    const again = await page.$('[data-testid^="leg-row-"]');
    if (again) { await again.focus(); await page.keyboard.press("Enter");
      await page.waitForTimeout(300); }
    // where does focus land inside the drawer, and can it be tabbed through?
    out.drawerTabStops = [];
    for (let i = 0; i < 4; i++) {
      await page.keyboard.press("Tab");
      out.drawerTabStops.push(await page.evaluate(() => {
        const a = document.activeElement;
        if (!a) return null;
        return { tag: a.tagName, label: a.getAttribute("aria-label"),
          testid: a.getAttribute("data-testid"),
          inDrawer: !!a.closest && !!a.closest("#drawer-trips") };
      }));
    }
    await page.keyboard.press("Escape");
    await page.waitForTimeout(400);
    out.afterEscape = await page.evaluate(() => {
      const a = document.activeElement;
      return {
        drawerHidden: document.querySelector("#drawer-trips").hidden,
        focus: a ? a.tagName : null,
        testid: a ? a.getAttribute("data-testid") : null,
        backOnTheRow: !!(a && (a.getAttribute("data-testid") || "").startsWith("leg-row-")),
      };
    });
    // and again with SPACE, which is the other key a row is expected to take
    const rows2 = await page.$$('[data-testid^="leg-row-"]');
    if (rows2.length > 2) {
      await rows2[2].focus();
      const before = await page.evaluate(() => window.scrollY);
      await page.keyboard.press(" ");
      await page.waitForTimeout(400);
      out.afterSpace = await page.evaluate((y) => ({
        drawerHidden: document.querySelector("#drawer-trips").hidden,
        pageScrolled: window.scrollY !== y,
      }), before);
    }
  }
  await browser.close();
  process.stdout.write(JSON.stringify(out));
}

main().catch((e) => { process.stdout.write(JSON.stringify({ fatal: String(e) })); process.exit(3); });
