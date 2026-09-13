/*
 * R2-4 re-test: where focus is after the drawer closes, in the four ways it can
 * close and the three ways the page can change underneath it.
 *
 *   node focus.js '{"port": N, "width": 1440}'
 */
const { chromium } = require("/home/claude/.npm-global/lib/node_modules/playwright");
const spec = JSON.parse(process.argv[2]);
function q(t) { return '[data-testid="' + t + '"]'; }

async function active(page) {
  return await page.evaluate(() => {
    const a = document.activeElement;
    if (!a) return null;
    const hiddenAncestor = (() => {
      let n = a;
      while (n && n !== document.body) {
        if (n.hasAttribute && n.hasAttribute("hidden")) return true;
        n = n.parentElement;
      }
      return false;
    })();
    return { tag: a.tagName, testid: a.getAttribute("data-testid"),
      label: a.getAttribute("aria-label"), inDocument: document.contains(a),
      insideHidden: hiddenAncestor,
      drawerHidden: document.querySelector("#drawer-trips").hidden };
  });
}

(async () => {
  const browser = await chromium.launch({
    executablePath: "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
    args: ["--no-sandbox", "--disable-dev-shm-usage", "--no-proxy-server",
      "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1",
      "--disable-background-networking", "--disable-component-update", "--no-first-run"],
    env: Object.assign({}, process.env, {HTTPS_PROXY: "", HTTP_PROXY: "", https_proxy: "",
      http_proxy: "", NO_PROXY: "*"}),
  });
  const ctx = await browser.newContext({ viewport: { width: spec.width || 1440, height: 900 } });
  await ctx.route("**/*", (r) => r.request().url().startsWith("http://127.0.0.1:")
    ? r.continue() : r.abort());
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  const out = { errors, width: spec.width || 1440 };

  await page.goto("http://127.0.0.1:" + spec.port + "/", { waitUntil: "domcontentloaded" });
  await page.waitForSelector(q("wordmark"));
  await page.click(q("trip-row-trip_b_europe"));
  await page.waitForSelector(q("trip-detail"));
  await page.click(q("mode-offline"));
  await page.click(q("run-go"));
  await page.waitForSelector(q("trip-result"), { timeout: 120000 });
  await page.waitForTimeout(500);

  // 1. keyboard: Tab to a row, Enter, Esc
  let cur = null;
  for (let i = 0; i < 60; i++) {
    await page.keyboard.press("Tab");
    cur = await page.evaluate(() => {
      const a = document.activeElement;
      return a ? a.getAttribute("data-testid") : null;
    });
    if (cur && cur.startsWith("leg-row-")) break;
  }
  out.openedFrom = cur;
  await page.keyboard.press("Enter");
  await page.waitForTimeout(300);
  out.afterEnter = await active(page);
  await page.keyboard.press("Escape");
  await page.waitForTimeout(350);
  out.afterEscape = await active(page);

  // 2. the close button
  await page.click(q("leg-row-B4"));
  await page.waitForTimeout(300);
  await page.click("#drawer-trips .x");
  await page.waitForTimeout(350);
  out.afterCloseButton = await active(page);

  // 3. the content changes underneath: run again while the drawer is open.
  // Below 1180px the drawer is a full-screen SHEET and covers Run, so a user
  // cannot do this at all there; the sheet is closed first and the step then
  // only checks that nothing is left focused on a destroyed element.
  await page.click(q("leg-row-B2"));
  await page.waitForTimeout(300);
  if ((spec.width || 1440) < 1180) {
    await page.keyboard.press("Escape");
    await page.waitForTimeout(300);
    out.sheetCoveredRun = true;
  }
  await page.click(q("run-go"));
  await page.waitForSelector(q("trip-result"), { timeout: 120000 });
  await page.waitForTimeout(700);
  out.afterRerunWithDrawerOpen = await active(page);
  out.drawerAfterRerun = await page.evaluate(() =>
    document.querySelector("#drawer-trips").hidden);

  // 4. switch tabs with the drawer open, then press Escape
  await page.click(q("leg-row-B3"));
  await page.waitForTimeout(300);
  // Does the open sheet cover the top bar? (It does below 1180px: the sheet is
  // the whole viewport.) Measured, then closed so the walk can continue.
  out.topBarReachableWithDrawerOpen = await page.evaluate(() => {
    const t = document.querySelector('[data-testid="tab-search"]');
    if (!t) return null;
    const r = t.getBoundingClientRect();
    const el = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
    return !!(el && (el === t || t.contains(el)));
  });
  if (!out.topBarReachableWithDrawerOpen) {
    await page.keyboard.press("Escape");
    await page.waitForTimeout(250);
    await page.click(q("leg-row-B3"));
    await page.waitForTimeout(200);
    await page.keyboard.press("Escape");
    await page.waitForTimeout(250);
  }
  await page.click(q("tab-search"));
  await page.waitForTimeout(300);
  await page.keyboard.press("Escape");
  await page.waitForTimeout(350);
  out.afterTabSwitchEscape = await active(page);
  out.searchViewVisible = await page.evaluate(() =>
    !document.querySelector("#view-search").hidden);

  // 5. back to trips: is the drawer still there, and does Esc still work?
  await page.click(q("tab-trips"));
  await page.waitForTimeout(400);
  out.drawerBackOnTrips = await page.evaluate(() =>
    document.querySelector("#drawer-trips").hidden);
  await page.keyboard.press("Escape");
  await page.waitForTimeout(300);
  out.afterSecondEscape = await active(page);

  await browser.close();
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { process.stdout.write(JSON.stringify({ fatal: String(e) })); process.exit(3); });
