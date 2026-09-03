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
import { chromium, webkit } from "playwright";

const arg = (name, fallback) => {
  const index = process.argv.indexOf(`--${name}`);
  return index > -1 ? process.argv[index + 1] : fallback;
};

const BASE = arg("base-url", "http://127.0.0.1:8080");
const USERNAME = arg("username", "demo");
const PASSWORD = arg("password", "studio-demo-2026");
const SHOTS = arg("shots", null);
// --engine webkit drives the same steps in Safari's engine. Safari delivers
// pointer moves faster than React renders, which is how a drag that looked
// fine in Chromium saved only its first pixel there.
const ENGINE = arg("engine", "chromium") === "webkit" ? webkit : chromium;

const problems = [];
// Before sign-in the app probes /api/me and is correctly told 401. That is the
// auth check working, not a fault, so it only counts once a session exists.
let signedIn = false;
// While a step deliberately makes a request fail, the browser's own line
// about that failure is expected, not a finding.
let expectingFailure = false;
const browser = await ENGINE.launch();
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });

page.on("console", (message) => {
  if (message.type() !== "error") return;
  const text = message.text();
  // A failed poll or a missing favicon is noise, not a broken app.
  if (/favicon|ERR_INTERNET_DISCONNECTED|net::ERR_ABORTED/i.test(text)) return;
  if (!signedIn && /401 \(Unauthorized\)/.test(text)) return;
  if (expectingFailure && /500 \(Internal Server Error\)/.test(text)) return;
  problems.push(`console: ${text}`);
});
page.on("pageerror", (error) => problems.push(`pageerror: ${error.message}`));

// Poll for a condition for up to `timeout` ms; false when it never held.
// Fixed pauses fail on the slower engines (WebKit saves and reloads take
// longer than Chromium), and a pause that is long enough for them makes
// every run slow.
const settles = async (check, timeout = 8000) => {
  const until = Date.now() + timeout;
  while (Date.now() < until) {
    if (await check()) return true;
    await page.waitForTimeout(250);
  }
  return false;
};

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

console.log(`Browser smoke: ${BASE} (${ENGINE === webkit ? "webkit" : "chromium"})\n`);

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
  // The one-click magic clip must be offered on transcribed sources.
  // Presence only: clicking would render a real project every night.
  if ((await page.locator(".source-row").count()) > 0 &&
      (await page.locator(".magic-clip").count()) === 0) {
    problems.push("quick-create-flow: no magic-clip button on any source");
  }
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
    const before = await page.locator('.clip-fields input').first().inputValue();
    const text = await suggestions.first().innerText();
    if (!/\d+s/.test(text)) {
      problems.push("suggested clips: a suggestion shows no duration");
    }
    if ((await suggestions.first().locator(".tag").count()) === 0) {
      problems.push("suggested clips: a suggestion explains nothing");
    }
    await suggestions.first().click();
    await page.waitForTimeout(900);
    const after = await page.locator('.clip-fields input').first().inputValue();
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
  // Times read "m:ss.s" now; a bare number is still accepted.
  const endSeconds = end.split(":").reduce((acc, part) => acc * 60 + Number(part), 0);
  if (!endSeconds) problems.push("quick-create-flow: picking a line did not set the clip end");

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

// The "?" must explain the screen you are on, in words, and go away again.
await step("help", async () => {
  const button = page.locator(".help-button").first();
  if ((await button.count()) === 0) {
    problems.push("help: no help button in the header");
    return;
  }
  await button.click();
  await page.waitForTimeout(300);
  const card = page.locator(".help-card");
  if ((await card.count()) === 0) {
    problems.push("help: the button opened nothing");
    return;
  }
  const text = await card.innerText();
  if (text.length < 120) problems.push("help: the guide is too short to help anyone");
  if ((await card.locator(".help-switch input").count()) === 0) problems.push("help: no large-text switch");
  await page.keyboard.press("Escape");
  await page.waitForTimeout(200);
  if (await card.count()) problems.push("help: Escape did not close it");
});

// "Show me" must point at a real button and follow the person through the
// first two screens on its own.
await step("coach", async () => {
  await page.locator(".help-button").first().click();
  await page.waitForTimeout(300);
  const start = page.locator(".help-card button:has-text('Show me')");
  if ((await start.count()) === 0) {
    problems.push("coach: the help card has no Show me button");
    await page.keyboard.press("Escape");
    return;
  }
  await start.click();
  await page.waitForTimeout(600);
  const spot = page.locator(".coach-spot");
  if ((await spot.count()) === 0) {
    problems.push("coach: nothing was spotlighted");
    return;
  }
  const first = await page.locator(".coach-bubble p").innerText();
  await page.locator(".new-project").first().click();
  await page.waitForTimeout(900);
  const second = await page.locator(".coach-bubble p").innerText().catch(() => "");
  if (!second || second === first) problems.push("coach: did not move on after the button was pressed");
  await page.keyboard.press("Escape");
  await page.waitForTimeout(200);
  if (await page.locator(".coach").count()) problems.push("coach: Escape did not stop it");
  // The tour moved us to Quick Create; the steps below expect Projects.
  await page.locator('.main-nav button:has-text("Projects")').first().click();
  await page.waitForTimeout(600);
});

// Right-click is how most people expect to find "delete"; the menu has to
// appear, offer it, and go away again without doing anything by itself.
await step("context-menu", async () => {
  const card = page.locator(".library-card").first();
  if ((await card.count()) === 0) return;
  await card.click({ button: "right" });
  await page.waitForTimeout(300);
  const menu = page.locator(".context-menu");
  if ((await menu.count()) === 0) {
    problems.push("context-menu: right-clicking a project opened nothing");
    return;
  }
  const text = await menu.innerText();
  for (const expected of ["Open in Studio", "Rename", "Delete"]) {
    if (!text.includes(expected)) problems.push(`context-menu: no "${expected}" item`);
  }
  await page.keyboard.press("Escape");
  await page.waitForTimeout(200);
  if (await menu.count()) problems.push("context-menu: Escape did not close it");
  // The "⋯" button is the same menu for people who do not right-click.
  const more = card.locator(".menu-button");
  if (await more.count()) {
    await more.click();
    await page.waitForTimeout(300);
    if ((await menu.count()) === 0) problems.push("context-menu: the ⋯ button opened nothing");
    await page.keyboard.press("Escape");
  }
});

await step("studio", async () => {
  // The card's first button is now the wee menu overlay; the opener is the
  // direct child button.
  const card = page.locator(".library-card > button, .project-cards button").first();
  if (await card.count()) {
    await card.click();
    // Through the tunnel the Studio takes a few seconds to arrive; wait for
    // it rather than for a fixed time, up to a limit that would itself be a
    // finding.
    await page.locator(".design-canvas").first().waitFor({ timeout: 20000 }).catch(() => {});
    await page.waitForTimeout(600);
  }
});

// Simple mode hides the advanced panels; "Everything" must bring them
// back. The rest of these steps use those panels, so switch it on here.
await step("studio-mode", async () => {
  const toggle = page.locator(".studio-mode");
  if ((await toggle.count()) === 0) {
    problems.push("studio-mode: no Simple / Everything switch");
    return;
  }
  await toggle.locator("button", { hasText: "Simple" }).click();
  await page.waitForTimeout(300);
  if (await page.locator(".music-panel").count()) problems.push("studio-mode: Simple still shows the music panel");
  await toggle.locator("button", { hasText: "Everything" }).click();
  await page.waitForTimeout(300);
  if ((await page.locator(".music-panel").count()) === 0) problems.push("studio-mode: Everything did not bring the music panel back");
});

await step("studio-canvas", async () => {
  // The canvas is the part most likely to throw: it reads the scene, the
  // transcript, and the peaks all at once.
  const canvas = page.locator(".design-canvas");
  if ((await canvas.count()) === 0) problems.push("studio-canvas: no canvas rendered");
  const inspector = page.locator(".design-panel");
  if ((await inspector.count()) === 0) problems.push("studio-canvas: no design panel");
});

// Making a thing bigger or smaller is the most basic edit there is; a
// selected layer must show handles, and dragging one must change its size.
await step("resize-layer", async () => {
  const title = page.locator(".canvas-layer.layer-title").first();
  if ((await title.count()) === 0) return;
  await title.click({ force: true });
  await page.waitForTimeout(300);
  const handle = page.locator(".layer-handles .resize-handle.se");
  if ((await handle.count()) === 0) {
    problems.push("resize-layer: a selected layer shows no resize handles");
    return;
  }
  const before = (await title.boundingBox())?.width ?? 0;
  const box = await handle.boundingBox();
  if (!box) return;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  // Inward, so it works however wide the layer already is: growing is
  // capped at the canvas edge and a previous run may have left it there.
  await page.mouse.move(box.x - 40, box.y - 20, { steps: 6 });
  await page.mouse.up();
  await page.waitForTimeout(600);
  const after = (await title.boundingBox())?.width ?? 0;
  if (after >= before - 5) problems.push(`resize-layer: dragging the corner did not resize the layer (${before} -> ${after})`);
  // And back, so the project is left as it was found.
  const again = await page.locator(".layer-handles .resize-handle.se").boundingBox();
  if (again) {
    await page.mouse.move(again.x + 6, again.y + 6);
    await page.mouse.down();
    await page.mouse.move(again.x + 46, again.y + 26, { steps: 6 });
    await page.mouse.up();
    await page.waitForTimeout(400);
  }
});

// The exact gesture a friend reported broken: grab a layer's body with the
// mouse and move it. A native image drag used to hijack this on artwork.
await step("drag-layer", async () => {
  const layer = page.locator(".canvas-layer.layer-title").first();
  if ((await layer.count()) === 0) return;
  const before = await layer.boundingBox();
  if (!before) return;
  await page.mouse.move(before.x + before.width / 2, before.y + before.height / 2);
  await page.mouse.down();
  await page.mouse.move(before.x + before.width / 2 + 60, before.y + before.height / 2 + 40, { steps: 8 });
  await page.mouse.up();
  await page.waitForTimeout(600);
  const after = await layer.boundingBox();
  const moved = after && (Math.abs(after.x - before.x) > 30 || Math.abs(after.y - before.y) > 20);
  if (!moved) problems.push("drag-layer: dragging a layer's body did not move it");
  // Put it back.
  if (after) {
    await page.mouse.move(after.x + after.width / 2, after.y + after.height / 2);
    await page.mouse.down();
    await page.mouse.move(before.x + before.width / 2, before.y + before.height / 2, { steps: 8 });
    await page.mouse.up();
    await page.waitForTimeout(400);
  }
});

// The Design panel's dropdowns are Material components now; picking an
// option must actually change the scene, not just look pretty.
await step("material-select", async () => {
  const fold = page.locator('.design-fold summary:has-text("Sound bars")');
  if ((await fold.count()) === 0) {
    problems.push("material-select: no Sound bars fold");
    return;
  }
  await fold.click();
  await page.waitForTimeout(400);
  const select = page.locator('.design-fold[open] md-outlined-select').first();
  if ((await select.count()) === 0) {
    problems.push("material-select: no Material dropdown in the Sound bars fold");
    return;
  }
  const before = await select.evaluate((el) => el.value);
  await select.click();
  await page.waitForTimeout(500);
  const target = page
    .locator('.design-fold[open] md-select-option')
    .filter({ hasText: before === "solid" ? "Still bars" : "Solid shape" })
    .first();
  await target.click();
  await page.waitForTimeout(900);
  const after = await select.evaluate((el) => el.value);
  if (after === before) problems.push(`material-select: picking an option changed nothing (${before})`);
  // Put it back.
  await select.click();
  await page.waitForTimeout(500);
  const options = page.locator('.design-fold[open] md-select-option');
  const n = await options.count();
  for (let i = 0; i < n; i += 1) {
    const value = await options.nth(i).evaluate((el) => el.value);
    if (value === before) { await options.nth(i).click(); break; }
  }
  await page.waitForTimeout(700);
  await fold.click();
});

// Changing a clip's length in the Studio must not require starting over:
// the full trimmer lives above the timeline now.
await step("clip-length", async () => {
  const summary = page.locator(".clip-length-block summary");
  if ((await summary.count()) === 0) {
    problems.push("clip-length: no trimmer block in the Studio");
    return;
  }
  await summary.click();
  await page.waitForTimeout(600);
  const endField = page.locator(".clip-length-block .clip-fields input").nth(1);
  const before = await endField.inputValue();
  const handle = page.locator(".clip-length-block .range-handle.end");
  const box = await handle.boundingBox();
  if (!box) {
    problems.push("clip-length: no draggable end handle");
    return;
  }
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x - 60, box.y, { steps: 6 });
  await page.mouse.up();
  await page.waitForTimeout(800);
  const after = await endField.inputValue();
  if (after === before) problems.push("clip-length: dragging the end handle changed nothing");
  // Put the clip back exactly as it was.
  await endField.click();
  await endField.fill(before);
  await endField.press("Enter");
  await page.waitForTimeout(600);
  await summary.click();
});

// Captions used to snap back when dragged; now they must stay put.
await step("drag-captions", async () => {
  const captions = page.locator(".canvas-layer.layer-captions").first();
  if ((await captions.count()) === 0) return;
  // Select them in the list first, the way a person does: an unselected
  // caption band hands presses on its empty part to the layer underneath.
  await page.locator('.layer-row:has-text("Captions")').first().click().catch(() => {});
  // The clip-length step above may have scrolled the canvas off the top;
  // positions are measured against the canvas, not the viewport, because
  // selecting a layer changes the panel's height and can shift the page.
  // Centred, not merely "in view": the sticky app header covers the top of
  // a canvas that is scrolled just into view, and a caption band parked at
  // the top of the picture then gets its press swallowed by the header.
  const canvasBox = page.locator(".design-canvas").first();
  await canvasBox.evaluate((el) => el.scrollIntoView({ block: "center" }));
  await page.waitForTimeout(300);
  const relativeTop = async () => {
    const canvas = await canvasBox.boundingBox();
    const box = await captions.boundingBox();
    return canvas && box ? box.y - canvas.y : null;
  };
  const before = await captions.boundingBox();
  const beforeTop = await relativeTop();
  if (!before || beforeTop === null) return;
  // Up, unless the band is already parked at the top of the canvas (a
  // previous run can leave it there), in which case down.
  const delta = beforeTop < 60 ? 80 : -80;
  await page.mouse.move(before.x + before.width / 2, before.y + before.height / 2);
  await page.mouse.down();
  await page.mouse.move(before.x + before.width / 2, before.y + before.height / 2 + delta, { steps: 8 });
  await page.mouse.up();
  await page.waitForTimeout(800);
  const afterTop = await relativeTop();
  if (afterTop === null || (afterTop - beforeTop) * Math.sign(delta) < 40)
    problems.push(`drag-captions: captions did not stay where they were dropped (${beforeTop} -> ${afterTop})`);
  // Back to roughly where they were.
  const after = await captions.boundingBox();
  if (after) {
    await page.mouse.move(after.x + after.width / 2, after.y + after.height / 2);
    await page.mouse.down();
    await page.mouse.move(after.x + after.width / 2, after.y + after.height / 2 - delta, { steps: 8 });
    await page.mouse.up();
    await page.waitForTimeout(500);
  }
});

// Arrow keys must move the selected layer; typing in a field must not.
await step("keyboard", async () => {
  const title = page.locator(".canvas-layer.layer-title").first();
  if ((await title.count()) === 0) return;
  await title.click({ force: true });
  await page.waitForTimeout(200);
  const before = (await title.boundingBox())?.x ?? 0;
  await page.keyboard.press("ArrowRight");
  await page.waitForTimeout(500);
  const after = (await title.boundingBox())?.x ?? 0;
  if (after <= before) problems.push(`keyboard: ArrowRight did not nudge the layer (${before} -> ${after})`);
  await page.keyboard.press("ArrowLeft");
  await page.waitForTimeout(300);
  // Typing a name must not move anything.
  const name = page.locator('.inspector input[type="text"]').first();
  if (await name.count()) {
    await name.click();
    const b2 = (await title.boundingBox())?.x ?? 0;
    await page.keyboard.press("ArrowRight");
    await page.waitForTimeout(300);
    const a2 = (await title.boundingBox())?.x ?? 0;
    if (a2 !== b2) problems.push("keyboard: arrows moved the layer while typing in a field");
  }
});

// Cutting words out of the clip is the one feature where the browser and the
// renderer have to agree about time, and a wrong answer is silent: the export
// is simply a few seconds off with captions that drift. The click is driven
// here so at least the panel's own arithmetic is exercised on every deploy.
await step("transcript-cuts", async () => {
  const words = page.locator(".cuts-line .word");
  const available = await words.count();
  if (available === 0) {
    // No word timings in this clip's transcript. The panel must say which of
    // the two situations it is in rather than rendering an empty box.
    const message = await page.locator(".transcript-cuts .muted, .transcript-editor .muted")
      .first().innerText().catch(() => "");
    if (!message.trim()) problems.push("transcript-cuts: empty with no explanation");
    return;
  }

  // A previous run that died mid-step can leave a cut behind, and clicking
  // a cut word restores it instead of cutting — the whole step inverts.
  // Start from a clean slate.
  const leftover = page.locator('.cuts-summary button:has-text("Restore all")');
  if (await leftover.count()) {
    await leftover.click();
    await page.waitForTimeout(600);
  }

  const summary = page.locator(".cuts-summary span").first();
  const before = await summary.innerText();
  if (!/Nothing cut|cut/i.test(before)) {
    problems.push(`transcript-cuts: unexpected summary "${before}"`);
  }

  await words.nth(Math.min(3, available - 1)).click();
  await page.waitForTimeout(900);

  const after = await summary.innerText();
  if (after === before) {
    problems.push("transcript-cuts: cutting a word changed nothing");
  }
  const struck = await page.locator(".cuts-line .word.cut").count();
  if (struck === 0) problems.push("transcript-cuts: no word was struck out");

  // Put it back, so the smoke run does not leave an edit on a real project.
  const restore = page.locator('.cuts-summary button:has-text("Restore all")');
  if (await restore.count()) {
    await restore.click();
    await page.waitForTimeout(900);
    if ((await page.locator(".cuts-line .word.cut").count()) !== 0) {
      problems.push("transcript-cuts: Restore all left cuts behind");
    }
  } else {
    problems.push("transcript-cuts: no way to restore what was cut");
  }
});

// A misheard name must be fixable right where the words are, and the fix
// must stick (the captions are built from the transcript's words).
await step("fix-word", async () => {
  const words = page.locator(".cuts-line .word");
  if ((await words.count()) < 2) return;
  const fix = page.locator('.cuts-summary button:has-text("Fix a word")');
  if ((await fix.count()) === 0) {
    problems.push("fix-word: no Fix a word button");
    return;
  }
  await fix.click();
  await page.waitForTimeout(300);
  const original = (await words.nth(1).innerText()).trim();
  await words.nth(1).click();
  const input = page.locator(".cuts-line .word.editing input");
  if ((await input.count()) === 0) {
    problems.push("fix-word: clicking a word in fix mode opened no box");
    await page.locator('.cuts-summary button:has-text("Done fixing")').click().catch(() => {});
    return;
  }
  await input.fill(`${original}x`);
  await input.press("Enter");
  const wordIs = (text) => async () =>
    (await page.locator(".cuts-line .word").nth(1).innerText()).trim() === text;
  if (!(await settles(wordIs(`${original}x`)))) {
    const changed = (await page.locator(".cuts-line .word").nth(1).innerText()).trim();
    problems.push(`fix-word: the word did not change (${original} -> ${changed})`);
  }
  // Put it back, so the smoke run does not leave a typo on a real project.
  await page.locator(".cuts-line .word").nth(1).click();
  const again = page.locator(".cuts-line .word.editing input");
  await again.fill(original);
  await again.press("Enter");
  if (!(await settles(wordIs(original)))) {
    const restored = (await page.locator(".cuts-line .word").nth(1).innerText()).trim();
    problems.push(`fix-word: could not restore the word (${restored})`);
  }
  await page.locator('.cuts-summary button:has-text("Done fixing")').click();
});

// A save that fails must say so and put the screen back to what the server
// has. Before this the edit stayed on screen, unsaved, until the next reload
// quietly took it away.
await step("save-failure", async () => {
  const title = page.locator(".canvas-layer.layer-title").first();
  if ((await title.count()) === 0) return;
  await page.locator(".design-canvas").first().evaluate((el) => el.scrollIntoView({ block: "center" }));
  await title.click({ force: true });
  await page.waitForTimeout(300);
  const before = (await title.boundingBox())?.x ?? 0;
  let failed = 0;
  expectingFailure = true;
  await page.route("**/api/projects/*", async (route) => {
    if (route.request().method() === "PATCH" && failed === 0) {
      failed += 1;
      return route.fulfill({ status: 500, contentType: "application/json", body: '{"detail":"simulated"}' });
    }
    return route.continue();
  });
  await page.keyboard.press("ArrowRight");
  const notice = page.locator(".save-notice");
  const shown = await settles(async () => (await notice.count()) > 0);
  if (!shown) problems.push("save-failure: no notice when a save failed");
  await page.unroute("**/api/projects/*");
  expectingFailure = false;
  const reverted = await settles(async () => Math.abs(((await title.boundingBox())?.x ?? 0) - before) < 0.5);
  if (!reverted) problems.push("save-failure: the layer kept an edit the server never saved");
  await notice.locator("button").click().catch(() => {});
  if (await notice.count()) problems.push("save-failure: OK did not dismiss the notice");
});

// The arrows in "What is on the picture" reorder the stack — the list, the
// canvas, and (since the export follows the list) the video.
await step("layer-order", async () => {
  const rows = page.locator(".layer-row");
  if ((await rows.count()) < 3) return;
  const names = async () => (await rows.allInnerTexts()).map((text) => text.trim());
  const before = await names();
  const forward = rows.nth(1).locator('button[aria-label="Bring forward"]');
  if (await forward.isDisabled()) {
    problems.push("layer-order: Bring forward is disabled on a movable layer");
    return;
  }
  await forward.click();
  await page.waitForTimeout(700);
  const after = await names();
  if (after[0] !== before[1] || after[1] !== before[0]) {
    problems.push(`layer-order: Bring forward did not reorder (${before.slice(0, 2)} -> ${after.slice(0, 2)})`);
  }
  await rows.nth(0).locator('button[aria-label="Send back"]').click();
  await page.waitForTimeout(700);
  const restored = await names();
  if (restored[0] !== before[0] || restored[1] !== before[1]) {
    problems.push("layer-order: Send back did not restore the order");
  }
});

// History is the safety net under the destructive-feeling actions — applying a
// template, cutting words, running a batch. A panel that silently lists nothing
// would leave those feeling like one-way doors.
await step("history", async () => {
  const panel = page.locator(".history-panel");
  if ((await panel.count()) === 0) {
    problems.push("history: no panel in the inspector");
    return;
  }
  // Either entries or an explanation; an empty box says neither.
  const entries = await page.locator(".history-list li").count();
  if (entries === 0) {
    const message = await panel.locator(".muted").first().innerText().catch(() => "");
    if (!message.trim()) problems.push("history: empty with no explanation");
  } else if ((await page.locator('.history-list button:has-text("Restore")').count()) === 0) {
    problems.push("history: entries listed with no way to restore one");
  }
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
  const gone = await settles(
    async () => (await page.locator(`.saved-template:has-text("${LOOK}")`).count()) === 0,
  );
  if (!gone) problems.push("templates: deleting the saved look did not remove it");
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

await step("settings", async () => {
  await page.locator('.main-nav button:has-text("Settings")').first().click();
  await page.waitForTimeout(800);
  const text = await page.locator(".settings-page").innerText().catch(() => "");
  if (!text.includes("Your show")) problems.push("settings: no Your show section");
  if (!text.includes("Posting accounts")) problems.push("settings: no posting accounts");
  // The platform rows arrive from the API after the page paints.
  if (!(await settles(async () => (await page.locator(".connection-row").count()) >= 3)))
    problems.push("settings: platform list missing");
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
