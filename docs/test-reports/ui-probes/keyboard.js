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
    await page.keyboard.press("Escape");
    await page.waitForTimeout(400);
    out.afterEscape = await page.evaluate(() => ({
      drawerHidden: document.querySelector("#drawer-trips").hidden,
      focus: document.activeElement ? document.activeElement.tagName : null,
    }));
  }
  await browser.close();
  process.stdout.write(JSON.stringify(out));
}

main().catch((e) => { process.stdout.write(JSON.stringify({ fatal: String(e) })); process.exit(3); });
