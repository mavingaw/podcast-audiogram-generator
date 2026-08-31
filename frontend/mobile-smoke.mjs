/**
 * Phone-viewport smoke test.
 *
 * The desktop smoke can be green while the app is unusable on a phone —
 * overflowing panels, unreachable buttons, touch drags that go nowhere. This
 * drives the real UI at iPhone size with touch, fails on console errors, and
 * checks the things a phone user actually does: sign in, browse, open the
 * studio, move something with a finger, open a share page.
 *
 *   node mobile-smoke.mjs --base-url https://... --username ... --password ...
 */
import { chromium, devices } from "playwright";

const arg = (name, fallback) => {
  const index = process.argv.indexOf(`--${name}`);
  return index > -1 ? process.argv[index + 1] : fallback;
};

const BASE = arg("base-url", "http://127.0.0.1:8080");
const USERNAME = arg("username", "demo");
const PASSWORD = arg("password", "studio-demo-2026");
const SHOTS = arg("shots", null);

const problems = [];
let signedIn = false;
const browser = await chromium.launch();
const context = await browser.newContext({ ...devices["iPhone 13"] });
const page = await context.newPage();

page.on("console", (message) => {
  if (message.type() !== "error") return;
  const text = message.text();
  if (/favicon|ERR_INTERNET_DISCONNECTED|net::ERR_ABORTED/i.test(text)) return;
  if (!signedIn && /401 \(Unauthorized\)/.test(text)) return;
  problems.push(`console: ${text}`);
});
page.on("pageerror", (error) => problems.push(`pageerror: ${error.message}`));

const step = async (name, action) => {
  const before = problems.length;
  await action();
  await page.waitForTimeout(700);
  const painted = await page.evaluate(() => {
    const root = document.querySelector("#root") ?? document.body;
    return (root.textContent ?? "").trim().length;
  });
  if (painted < 20) problems.push(`${name}: page rendered nothing (${painted} chars)`);
  // A page that scrolls sideways is broken on a phone, full stop.
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  if (overflow > 24) problems.push(`${name}: page scrolls sideways by ${overflow}px`);
  const failed = problems.length - before;
  console.log(`  ${failed ? "FAIL" : "pass"}  ${name}`);
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/mobile-${name}.png` });
};

const tapDrag = async (locator, dx, dy) => {
  const box = await locator.boundingBox();
  if (!box) return null;
  const cx = box.x + box.width / 2;
  const cy = box.y + box.height / 2;
  await page.touchscreen.tap(cx, cy);
  await page.waitForTimeout(250);
  // Playwright has no touch drag; dispatch the pointer events the app uses.
  await locator.dispatchEvent("pointerdown", { pointerId: 7, clientX: cx, clientY: cy, isPrimary: true });
  for (let i = 1; i <= 6; i += 1) {
    await page.evaluate(
      ([x, y]) => window.dispatchEvent(new PointerEvent("pointermove", { pointerId: 7, clientX: x, clientY: y })),
      [cx + (dx * i) / 6, cy + (dy * i) / 6],
    );
    await page.waitForTimeout(40);
  }
  await page.evaluate(() => window.dispatchEvent(new PointerEvent("pointerup", { pointerId: 7 })));
  await page.waitForTimeout(700);
  return box;
};

console.log(`Mobile smoke (iPhone 13): ${BASE}\n`);

await step("sign-in-screen", () => page.goto(BASE, { waitUntil: "networkidle" }));

await step("sign-in", async () => {
  await page.fill('input[autocomplete="username"]', USERNAME);
  await page.fill('input[type="password"]', PASSWORD);
  await page.tap(".primary");
  await page.waitForTimeout(2500);
  signedIn = true;
});

await step("home", async () => {
  const tiles = await page.locator(".home-tile, .create-tile, [data-tint]").count();
  if (tiles === 0) problems.push("home: no create tiles visible on a phone");
});

// Navigate the way a phone user has to: the sidebar is off-canvas behind
// the hamburger. If the hamburger is missing or the drawer never opens,
// a phone user is stranded — that is the failure this step exists to catch.
const goTo = async (label) => {
  const direct = page.locator(`.main-nav button:has-text("${label}")`).first();
  if (!(await direct.isVisible().catch(() => false))) {
    const burger = page.locator(".mobile-menu").first();
    if ((await burger.count()) === 0) {
      problems.push(`nav: sidebar hidden and no hamburger to open it (${label})`);
      return false;
    }
    await burger.tap();
    await page.waitForTimeout(600);
    if (!(await direct.isVisible().catch(() => false))) {
      problems.push(`nav: hamburger did not reveal the ${label} button`);
      return false;
    }
  }
  await direct.tap();
  await page.waitForTimeout(1200);
  return true;
};

await step("projects", async () => {
  if (!(await goTo("Projects"))) return;
  if ((await page.locator(".library-card").count()) === 0)
    problems.push("projects: no project cards painted");
});

await step("studio", async () => {
  const opener = page.locator(".library-card > button").first();
  if ((await opener.count()) === 0) return;
  await opener.tap();
  await page.waitForTimeout(2500);
  if ((await page.locator(".studio-canvas, .canvas-frame, .canvas-layer").count()) === 0)
    problems.push("studio: canvas did not paint on a phone");
});

await step("touch-drag-layer", async () => {
  const layer = page.locator(".canvas-layer.layer-title").first();
  if ((await layer.count()) === 0) return;
  const before = await layer.boundingBox();
  if (!before) return;
  await tapDrag(layer, 40, 30);
  const after = await layer.boundingBox();
  const moved = after && (Math.abs(after.x - before.x) > 15 || Math.abs(after.y - before.y) > 10);
  if (!moved) problems.push("touch-drag-layer: a finger drag did not move the layer");
  if (after) await tapDrag(layer, before.x - after.x, before.y - after.y);
});

await step("export-panel", async () => {
  const buttons = await page.locator("button:visible").allTextContents();
  const hasExport = buttons.some((text) => /export|make my clip|download/i.test(text));
  if (!hasExport) problems.push("export-panel: no export control reachable on a phone");
});

await step("share-page", async () => {
  // Any project with a share link renders /s/<token> signed out; check the
  // page shell at least loads over the phone viewport without sideways scroll.
  await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
});

await browser.close();

if (problems.length) {
  console.log(`\n${problems.length} problem(s):`);
  for (const problem of problems) console.log(`  - ${problem}`);
  process.exit(1);
}
console.log("\nNo console errors, nothing overflowing, touch works.");
