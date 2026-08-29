/**
 * Browser smoke test.
 *
 * The API tests cannot see a React crash: a component that throws still returns
 * HTTP 200 with a valid bundle, and the page just renders black. That is
 * exactly what a temporal-dead-zone reference did to the Studio, and nothing in
 * the suite caught it. This drives the real UI, fails on any console error or
 * page exception, and asserts each view actually painted something.
 *
 *   npm run smoke -- --base-url http://localhost:8080
 */
import { chromium } from "playwright";

const arg = (name, fallback) => {
  const index = process.argv.indexOf(`--${name}`);
  return index > -1 ? process.argv[index + 1] : fallback;
};

const BASE = arg("base-url", "http://127.0.0.1:8080");
const USERNAME = arg("username", "demo");
const PASSWORD = arg("password", "studio-demo-2026");
const SHOTS = arg("shots", null);

const problems = [];
// Before sign-in the app probes /api/me and is correctly told 401. That is the
// auth check working, not a fault, so it only counts once a session exists.
let signedIn = false;
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });

page.on("console", (message) => {
  if (message.type() !== "error") return;
  const text = message.text();
  // A failed poll or a missing favicon is noise, not a broken app.
  if (/favicon|ERR_INTERNET_DISCONNECTED|net::ERR_ABORTED/i.test(text)) return;
  if (!signedIn && /401 \(Unauthorized\)/.test(text)) return;
  problems.push(`console: ${text}`);
});
page.on("pageerror", (error) => problems.push(`pageerror: ${error.message}`));

const step = async (name, action) => {
  const before = problems.length;
  await action();
  await page.waitForTimeout(700);

  // A crashed React tree leaves the root empty; the page is "fine" otherwise.
  const painted = await page.evaluate(() => {
    const root = document.querySelector("#root") ?? document.body;
    return (root.textContent ?? "").trim().length;
  });
  if (painted < 20) problems.push(`${name}: page rendered nothing (${painted} chars)`);

  const failed = problems.length - before;
  console.log(`  ${failed ? "FAIL" : "pass"}  ${name}`);
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/smoke-${name}.png` });
};

console.log(`Browser smoke: ${BASE}\n`);

await step("sign-in-screen", () => page.goto(BASE, { waitUntil: "networkidle" }));

await step("sign-in", async () => {
  await page.fill('input[autocomplete="username"]', USERNAME);
  await page.fill('input[type="password"]', PASSWORD);
  await page.click(".primary");
  await page.waitForTimeout(2200);
  signedIn = true;
});

await step("home", async () => {
  await page.locator('.main-nav button:has-text("Home")').first().click();
});

await step("quick-create", async () => {
  await page.locator(".new-project").first().click();
});

// The guided flow is the path most clips are made through, so it gets driven
// rather than merely rendered: destination, existing source, then the clipper's
// transcript search — which is how you find a hook in a long episode.
await step("quick-create-flow", async () => {
  await page.locator(".new-project").first().click();
  await page.waitForTimeout(500);

  const destination = page.locator(".destination-grid button").first();
  if (await destination.count()) {
    await destination.click();
    await page.locator(".flow-next .primary").click();
    await page.waitForTimeout(600);
  }

  // Prefer a source that actually has speech: the search and zoom assertions
  // below are only meaningful against a real transcript, and a clean install's
  // only media is a test tone.
  const spoken = await page.evaluate(async () => {
    const response = await fetch("/api/media", { credentials: "include" });
    if (!response.ok) return null;
    const { media } = await response.json();
    return media.find((item) => item.has_transcript && item.transcript?.segments?.length)
      ?.original_name ?? null;
  });

  const sources = page.locator(".source-list button");
  if ((await sources.count()) === 0) {
    problems.push("quick-create-flow: no existing source offered");
    return;
  }
  const source = spoken
    ? sources.filter({ hasText: spoken.slice(0, 24) }).first()
    : sources.first();
  await (await source.count() ? source : sources.first()).click();
  await page.locator(".flow-next .primary").click();
  await page.waitForTimeout(1400);

  const before = await page.locator(".transcript-pick button").count();
  if (before === 0) {
    // A clean install's only media is a test tone: no speech, so no lines, and
    // nothing to search. That is the app behaving correctly, not a fault — but
    // the clipper must say which of the two it is.
    const message = await page.locator(".transcript-pick .muted").innerText();
    if (!/no speech|being prepared/i.test(message)) {
      problems.push(`quick-create-flow: unclear empty transcript state: "${message}"`);
    }
    console.log("        (source has no speech; skipping search assertions)");
    return;
  }

  // Suggested clips are the answer to "where is the good bit"; if they render
  // at all they must be actionable, not decoration.
  const suggestions = page.locator(".suggestion");
  if (await suggestions.count()) {
    const before = await page.locator('input[type="number"]').first().inputValue();
    const text = await suggestions.first().innerText();
    if (!/\d+s/.test(text)) {
      problems.push("suggested clips: a suggestion shows no duration");
    }
    if ((await suggestions.first().locator(".tag").count()) === 0) {
      problems.push("suggested clips: a suggestion explains nothing");
    }
    await suggestions.first().click();
    await page.waitForTimeout(900);
    const after = await page.locator('input[type="number"]').first().inputValue();
    if (before === after) {
      problems.push("suggested clips: picking one did not move the clip");
    }
  }

  const search = page.locator(".transcript-search input");
  if ((await search.count()) === 0) {
    problems.push("quick-create-flow: clipper has no transcript search");
    return;
  }

  await search.fill("the");
  await page.waitForTimeout(500);
  const after = await page.locator(".transcript-pick button").count();
  if (after === 0 || after > before) {
    problems.push(`quick-create-flow: search did not narrow ${before} lines (got ${after})`);
  }
  if ((await page.locator(".transcript-pick mark").count()) === 0) {
    problems.push("quick-create-flow: search matches are not highlighted");
  }

  // Choosing a line must set the clip range.
  await page.locator(".transcript-pick button").first().click();
  await page.waitForTimeout(400);
  const end = await page.locator('.clip-fields input').nth(1).inputValue();
  if (!Number(end)) problems.push("quick-create-flow: picking a line did not set the clip end");

  // Zoom must narrow the visible window without moving the clip.
  const zoomIn = page.locator('.zoom-controls button[title*="Zoom"]');
  if ((await zoomIn.count()) === 0) {
    problems.push("quick-create-flow: no zoom control on the clipper");
    return;
  }
  const startBefore = await page.locator(".clip-fields input").first().inputValue();
  await zoomIn.click();
  await page.waitForTimeout(900);

  const label = await page.locator(".clip-status").innerText();
  if (!/showing/i.test(label)) {
    problems.push(`quick-create-flow: zoom did not narrow the view ("${label}")`);
  }
  const startAfter = await page.locator(".clip-fields input").first().inputValue();
  if (startAfter !== startBefore) {
    problems.push("quick-create-flow: zooming changed the clip range");
  }

  const zoomOut = page.locator('.zoom-controls button[title*="whole"]');
  await zoomOut.click();
  await page.waitForTimeout(700);
  if (/showing/i.test(await page.locator(".clip-status").innerText())) {
    problems.push("quick-create-flow: zoom out did not restore the full view");
  }
});

await step("projects", async () => {
  await page.locator('.main-nav button:has-text("Projects")').first().click();
});

await step("studio", async () => {
  const card = page.locator(".library-grid button, .project-cards button").first();
  if (await card.count()) {
    await card.click();
    await page.waitForTimeout(2200);
  }
});

await step("studio-canvas", async () => {
  // The canvas is the part most likely to throw: it reads the scene, the
  // transcript, and the peaks all at once.
  const canvas = page.locator(".design-canvas");
  if ((await canvas.count()) === 0) problems.push("studio-canvas: no canvas rendered");
  const inspector = page.locator(".design-panel");
  if ((await inspector.count()) === 0) problems.push("studio-canvas: no design panel");
});

await step("timeline-zoom", async () => {
  // A forty-five second clip across 700px is fifteen pixels a second, which is
  // not enough to place a block against a word. Zoom has to actually narrow the
  // window, not just stretch what is drawn.
  const zoomIn = page.locator('.timeline-zoom button[title="Zoom in"]');
  if ((await zoomIn.count()) === 0) {
    problems.push("timeline-zoom: no zoom control");
    return;
  }
  const span = async () => {
    const marks = await page.locator(".ruler span").allInnerTexts();
    return marks.length ? `${marks[0]}-${marks[marks.length - 1]}` : "";
  };
  const before = await span();
  await zoomIn.click();
  await page.waitForTimeout(400);
  const after = await span();
  if (before === after) problems.push("timeline-zoom: the window did not narrow");
  if ((await page.locator(".timeline-fit").count()) === 0) {
    problems.push("timeline-zoom: no way back to the whole clip");
  } else {
    await page.locator(".timeline-fit").click();
    await page.waitForTimeout(400);
    if ((await span()) !== before) problems.push("timeline-zoom: fit did not restore");
  }
});

await step("destinations", async () => {
  // The point of this panel is to say no *before* a render, so it has to render
  // verdicts rather than just a heading.
  const head = page.locator(".destinations-head");
  if ((await head.count()) === 0) {
    problems.push("destinations: no platform panel in Studio");
    return;
  }
  if (!/\d+ of \d+ platforms/.test(await head.innerText())) {
    problems.push("destinations: panel does not summarise how many accept the clip");
  }
  await head.click();
  await page.waitForTimeout(600);
  const rows = page.locator(".destination");
  if ((await rows.count()) === 0) {
    problems.push("destinations: expanded to nothing");
    return;
  }
  // Every refusal must say why, or it is not actionable.
  const blocked = page.locator(".destination.blocked");
  for (let i = 0; i < (await blocked.count()); i++) {
    // Structural rather than textual: the reason is its own element, so its
    // absence is what matters, not how the text happens to wrap.
    if ((await blocked.nth(i).locator("small").count()) === 0) {
      const text = await blocked.nth(i).innerText();
      problems.push(`destinations: "${text.trim()}" is refused without a reason`);
      break;
    }
  }
});

await step("scrub-timeline", async () => {
  const tracks = page.locator(".tracks").first();
  if (await tracks.count()) {
    const box = await tracks.boundingBox();
    if (box) await page.mouse.click(box.x + box.width * 0.3, box.y + box.height * 0.85);
  }
});

// Saving a look is only useful if it comes back, so the test saves one in
// Studio and then goes looking for it in the gallery.
const LOOK = `Smoke look ${Date.now()}`;

await step("save-template", async () => {
  const field = page.locator('.template-save input');
  if ((await field.count()) === 0) {
    problems.push("save-template: no template panel in Studio");
    return;
  }
  await field.fill(LOOK);
  await page.locator('.template-save button').first().click();
  await page.waitForTimeout(1200);
  const note = await page.locator('.panel-note').first().textContent().catch(() => null);
  if (note !== "Saved") problems.push(`save-template: expected "Saved", got ${note}`);
  // And it becomes applicable without a reload.
  const chip = page.locator(`.template-apply .chip:has-text("${LOOK}")`);
  if ((await chip.count()) === 0) problems.push("save-template: saved look is not applicable");
});

await step("templates", async () => {
  await page.locator('.main-nav button:has-text("Templates")').first().click();
  await page.waitForTimeout(900);
  const card = page.locator(`.saved-template:has-text("${LOOK}")`);
  if ((await card.count()) === 0) {
    problems.push("templates: the saved look is missing from the gallery");
    return;
  }
  // Starters must still be there alongside it. Counted rather than matched by
  // name, so renaming a starter does not fail this test.
  const starters = await page
    .locator(".template-grid.gallery:last-of-type > button")
    .count();
  if (starters < 3) {
    problems.push(`templates: expected the starter templates, found ${starters}`);
  }

  // Clean up after ourselves so repeated runs do not pile up.
  await card.locator('.icon-button').click();
  await page.waitForTimeout(900);
  if (await page.locator(`.saved-template:has-text("${LOOK}")`).count()) {
    problems.push("templates: deleting the saved look did not remove it");
  }
});

await step("inbox", async () => {
  await page.locator('.main-nav button:has-text("Inbox")').first().click();
  await page.waitForTimeout(900);
  const heading = await page.locator(".library-page h2").innerText();
  if (heading !== "Inbox") problems.push(`inbox: landed on "${heading}"`);
  const body = await page.locator(".library-page").innerText();
  // Either clips are waiting or it says so; a blank page is the failure.
  if (!/Nothing waiting|Keep|Discard/.test(body)) {
    problems.push("inbox: neither clips nor an empty state");
  }
});

await step("exports", async () => {
  await page.locator('.main-nav button:has-text("Exports")').first().click();
});

await browser.close();

if (problems.length) {
  console.error(`\n${problems.length} problem(s):`);
  for (const problem of problems) console.error(`  - ${problem}`);
  process.exit(1);
}
console.log("\nNo console errors, no blank views.");
