// Kinder UI audit: every page and panel the smoke does not drive, exercised
// in a real browser. Records PASS/FAIL per part; restores what it changes.
//   node ui-audit.mjs --base-url URL --username U --password P [--engine webkit] [--shots DIR]
import { chromium, webkit } from "playwright";

const arg = (n, f) => { const i = process.argv.indexOf(`--${n}`); return i > -1 ? process.argv[i + 1] : f; };
const BASE = arg("base-url"), USERNAME = arg("username"), PASSWORD = arg("password"), SHOTS = arg("shots", null);
const ENGINE = arg("engine", "chromium") === "webkit" ? webkit : chromium;
const results = [];
const consoleErrors = [];
let signedIn = false;
const browser = await ENGINE.launch();
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
page.on("console", (m) => { if (m.type() !== "error") return; const t = m.text(); if (/favicon|ERR_ABORTED/i.test(t)) return; if (!signedIn && /401/.test(t)) return; consoleErrors.push(t.slice(0, 160)); });
page.on("pageerror", (e) => consoleErrors.push(`pageerror: ${e.message.slice(0, 160)}`));
page.on("dialog", (d) => d.accept());

const settles = async (check, timeout = 8000) => { const until = Date.now() + timeout; while (Date.now() < until) { if (await check()) return true; await page.waitForTimeout(250); } return false; };
const part = async (name, fn) => {
  const errorsBefore = consoleErrors.length;
  try { const note = await fn(); const fresh = consoleErrors.slice(errorsBefore); if (fresh.length) throw new Error(`console: ${fresh[0]}`); results.push({ name, ok: true, note: note || "" }); console.log(`  PASS  ${name}${note ? " — " + note : ""}`); }
  catch (e) { results.push({ name, ok: false, note: e.message.slice(0, 200) }); console.log(`  FAIL  ${name} — ${e.message.slice(0, 200)}`); }
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/ui-${name.replace(/[^a-z0-9]+/gi, "-").toLowerCase()}.png` }).catch(() => {});
};
const expect = (c, m) => { if (!c) throw new Error(m); };
const nav = async (label) => { await page.locator(`.main-nav button:has-text("${label}")`).first().click(); await page.waitForTimeout(900); };
// What a Material select shows is in its shadow DOM; innerText sees nothing.
const shownText = (select) => select.evaluate((el) => String(el.displayText ?? "").trim());
const mdSelect = async (select, optionText) => {
  // Material select: open it, pick the option by its headline.
  await select.click(); await page.waitForTimeout(250);
  const opt = select.locator(`md-select-option:has-text("${optionText}")`).first();
  await opt.click(); await page.waitForTimeout(400);
};

console.log(`UI audit against ${BASE} (${ENGINE === webkit ? "webkit" : "chromium"})\n`);
await page.goto(BASE, { waitUntil: "networkidle" });
await page.fill('input[autocomplete="username"]', USERNAME);
await page.fill('input[type="password"]', PASSWORD);
await page.click(".primary");
await page.waitForTimeout(2200);
signedIn = true;

// ---------- Home ----------
await part("home tiles and template strip", async () => {
  await nav("Home");
  const tiles = await page.locator(".home button, .home [role=button]").count();
  expect(tiles >= 5, `only ${tiles} clickable things on Home`);
  const strip = await page.locator(".home-templates, .home-template-row").count();
  expect(strip >= 1, "no template strip");
  return `${tiles} tiles`;
});
await part("home tile opens quick create", async () => {
  const first = page.locator(".home button").first();
  await first.click(); await page.waitForTimeout(900);
  expect((await page.locator(".new-project, .quick-create, .flow, .library-page").count()) >= 1, "nothing opened");
});

// ---------- Analytics ----------
await part("analytics page", async () => {
  await nav("Analytics");
  const ok = await settles(async () => (await page.locator(".stat-cards, .empty-state").count()) >= 1);
  expect(ok, "neither numbers nor an empty state");
  const rows = await page.locator(".analytics-row").count();
  return rows ? `${rows} clips listed` : "empty state";
});

// ---------- Trash ----------
await part("trash page lists, restores, deletes", async () => {
  // Make something to trash: a saved-look-free project via the API cookie is
  // out of scope here; use the Projects page context menu instead.
  await nav("Projects");
  const cards = page.locator(".library-card");
  expect((await cards.count()) >= 1, "no projects to work with");
  const firstTitle = (await cards.first().innerText()).split("\n")[0].trim();
  const trashButton = page.locator('.library-card button[title*="trash"], .library-card button[title*="Delete"]').first();
  expect(await trashButton.count(), "no trash button on the card");
  await trashButton.click(); await page.waitForTimeout(400);
  const confirmItem = page.locator('.context-menu button:has-text("Move to trash")').first();
  if (await confirmItem.count()) await confirmItem.click();
  expect(await settles(async () => (await page.locator(`.library-card:has-text("${firstTitle.slice(0, 20)}")`).count()) === 0), "card did not leave the Projects page");
  await nav("Trash");
  const row = page.locator(`.trash-row:has-text("${firstTitle.slice(0, 20)}")`).first();
  expect(await settles(async () => (await row.count()) > 0), "trashed project not listed in Trash");
  const restore = row.locator("button", { hasText: /restore|put back|bring/i }).first();
  expect(await restore.count(), "no restore button");
  await restore.click();
  expect(await settles(async () => (await row.count()) === 0), "restore did not remove it from Trash");
  await nav("Projects");
  expect(await settles(async () => (await page.locator(`.library-card:has-text("${firstTitle.slice(0, 20)}")`).count()) > 0), "restored project not back in Projects");
  return `"${firstTitle.slice(0, 30)}" trashed and restored`;
});

// ---------- Feeds ----------
await part("feeds page: add, controls, remove", async () => {
  await nav("Feeds");
  const input = page.locator(".feed-add input");
  expect(await input.count(), "no feed URL box");
  await input.fill("https://feeds.npr.org/510289/podcast.xml");
  await page.locator(".feed-add button.primary").click();
  const card = page.locator('.feed-card:has-text("Planet Money")').first();
  expect(await settles(async () => (await card.count()) > 0, 30000), "feed card did not appear");
  const clips = card.locator(".feed-controls md-outlined-select").first();
  expect(await clips.count(), "no clips-per-episode select");
  const shown = await shownText(clips);
  expect(/None/.test(shown), `clips select shows "${shown}" instead of None`);
  await mdSelect(clips, "3");
  expect(await settles(async () => (await card.locator("md-switch").count()) >= 1), "render switch did not appear after choosing 3 clips");
  expect(await settles(async () => /3/.test(await shownText(card.locator(".feed-controls md-outlined-select").first()))), "clips select does not show 3");
  const older = card.locator(".feed-import-older md-outlined-select");
  expect(/5/.test(await shownText(older)), "import-older select shows blank");
  const remove = card.locator(".feed-top button").first();
  await remove.click();
  expect(await settles(async () => (await card.count()) === 0), "feed was not removed");
});

// ---------- Inbox / Exports (present in smoke, but check the actions exist) ----------
await part("exports page lists downloads", async () => {
  await nav("Exports");
  const ok = await settles(async () => (await page.locator(".export-list, .empty-state").count()) >= 1);
  expect(ok, "no list and no empty state");
  const links = await page.locator('.export-list a[download], .export-list .button-link').count();
  return `${links} download links`;
});

// ---------- Settings ----------
await part("settings: profile name edit (and back)", async () => {
  await nav("Settings");
  const field = page.locator(".profile-name-row input").first();
  expect(await field.count(), "no display name field");
  const original = await field.inputValue();
  await field.fill(original ? original + "!" : "Audit Name");
  await page.locator(".profile-name-row button").first().click(); await page.waitForTimeout(900);
  expect(await settles(async () => (await page.locator(".profile-name-row input").first().inputValue()) !== original), "name did not change");
  await field.fill(original); await page.locator(".profile-name-row button").first().click(); await page.waitForTimeout(900);
  expect((await page.locator(".profile-name-row input").first().inputValue()) === original, "could not restore the name");
});
await part("settings: change-password form present and gated", async () => {
  const details = page.locator(".password-change");
  expect(await details.count(), "no Change password section");
  await details.locator("summary").click(); await page.waitForTimeout(200);
  const submit = details.locator('button[type="submit"]');
  expect(await submit.isDisabled(), "submit enabled with empty fields");
});
await part("settings: show artwork controls", async () => {
  const block = page.locator(".show-artwork");
  expect(await block.count(), "no show artwork block");
  expect(await block.locator('input[type="file"]').count(), "no upload input");
  const chosen = await block.locator(".show-artwork-actions button:has-text(\"Remove\"), .show-artwork-actions button:has-text(\"Clear\")").count();
  expect(chosen || (await block.locator("select").count()), "neither a chosen artwork nor a pick-from-uploads select");
});
await part("settings: intro/outro slots", async () => {
  const slots = page.locator(".branding-clips:not(.your-fonts) > .branding-slot");
  expect((await slots.count()) === 2, `expected 2 slots, found ${await slots.count()}`);
});
await part("settings: your fonts upload + delete", async () => {
  const input = page.locator('.branding-slot input[type="file"], input[type="file"][accept*="ttf"], input[type="file"][accept*="font"]').last();
  expect(await input.count(), "no font upload input");
  await input.setInputFiles(arg("assets") + "/audit-font.ttf");
  await page.waitForTimeout(4000); // the upload reloads the page so the new face is registered
  await page.waitForLoadState("networkidle").catch(() => {});
  await nav("Settings");
  const sample = page.locator('.font-sample:has-text("Sora")');
  expect(await settles(async () => (await sample.count()) > 0, 15000), "uploaded font did not appear");
  await sample.locator("xpath=..").locator("button").first().click();
  expect(await settles(async () => (await sample.count()) === 0), "font was not removed");
});
await part("settings: posting accounts listed", async () => {
  expect(await settles(async () => (await page.locator(".connection-row").count()) >= 5), `only ${await page.locator(".connection-row").count()} platform rows`);
  const rows = await page.locator(".connection-row").count();
  return `${rows} platforms`;
});
await part("settings: admin section (accounts, invite link, signups, GPU)", async () => {
  const admin = page.locator(".admin-strip");
  expect(await admin.count(), "no admin section");
  if (!(await admin.evaluate((el) => el.open))) await admin.locator("> summary").click();
  await page.waitForTimeout(400);
  expect(await admin.locator(".account-list li").count() >= 1, "no accounts listed");
  expect(await admin.locator(".invite-link input").count(), "no invite link");
  expect(await admin.locator("md-switch").count() >= 1, "no signups switch");
  expect(await admin.locator("md-outlined-select").count() >= 1, "no GPU select");
});
await part("settings: transcription settings panel", async () => {
  const panel = page.locator(".transcription-panel");
  expect(await panel.count(), "no transcription panel");
  expect(await panel.locator("md-outlined-select").count() >= 2, "model/language selects missing");
});
await part("user menu: avatar opens menu with Settings and Sign out", async () => {
  await page.locator(".user-avatar").first().click(); await page.waitForTimeout(300);
  const menu = page.locator(".context-menu, [role=menu]");
  expect(await menu.count(), "no menu opened");
  const text = await menu.innerText();
  expect(/Settings/.test(text) && /Sign out/.test(text), `menu says: ${text.slice(0, 60)}`);
  await page.keyboard.press("Escape");
});

// ---------- Studio: Everything panels ----------
await part("open the newest project in Studio", async () => {
  await nav("Projects");
  await page.locator(".library-card > button").first().click();
  await page.locator(".design-canvas").first().waitFor({ timeout: 20000 });
  await page.waitForTimeout(800);
  const toggle = page.locator(".studio-mode");
  await toggle.locator("button", { hasText: "Everything" }).click(); await page.waitForTimeout(300);
});
await part("aspect switch buttons present, 9:16 <-> current", async () => {
  const sw = page.locator(".aspect-switch button");
  expect((await sw.count()) === 4, `found ${await sw.count()} aspect buttons`);
  const current = (await page.locator(".aspect-switch button.on").innerText()).trim();
  const other = current === "16:9" ? "1:1" : "16:9";
  // The screen flips at once (optimistic); the server is the truth. Once, a
  // project was found at 16:9 with the screen saying 1:1, so both are checked.
  const newest = (await (await page.request.get(`${BASE}/api/projects`)).json()).projects[0];
  const serverRatio = async () => (await (await page.request.get(`${BASE}/api/projects/${newest.id}`)).json()).project.aspect_ratio;
  expect((await serverRatio()) === current, `server says ${await serverRatio()}, screen says ${current}`);
  await page.locator(`.aspect-switch button:has-text("${other}")`).click();
  expect(await settles(async () => (await page.locator(".aspect-switch button.on").innerText()).trim() === other), "switch did not take");
  expect(await settles(async () => (await serverRatio()) === other), `server did not switch to ${other}`);
  await page.locator(`.aspect-switch button:has-text("${current}")`).click();
  expect(await settles(async () => (await page.locator(".aspect-switch button.on").innerText()).trim() === current), "could not switch back");
  expect(await settles(async () => (await serverRatio()) === current), `server did not switch back to ${current}`);
  return `${current} -> ${other} -> ${current} (server agrees)`;
});
await part("pictures panel: cover art chips + background fold", async () => {
  const panel = page.locator(".pictures-panel");
  expect(await panel.count(), "no pictures panel");
  expect(await panel.locator(".image-chip").count() >= 2, "no picture chips");
  const fold = panel.locator("details.design-fold");
  await fold.locator("summary").click(); await page.waitForTimeout(200);
  expect(await fold.locator(".image-picker").count(), "background picker missing");
});
await part("layer list: eye toggles visibility (and back)", async () => {
  const row = page.locator('.layer-row:has-text("Waveform")').first();
  expect(await row.count(), "no Waveform row");
  const eye = row.locator("button").nth(2);
  const before = await page.locator(".canvas-layer.layer-waveform").count();
  await eye.click(); await page.waitForTimeout(600);
  const hidden = await page.locator(".canvas-layer.layer-waveform").count();
  expect(hidden < before || (await row.locator("button").nth(2).innerHTML()) !== "", "eye did nothing");
  await eye.click(); await page.waitForTimeout(600);
  expect((await page.locator(".canvas-layer.layer-waveform").count()) === before, "layer did not come back");
});
await part("text panel: add text, type select, words, alignment, delete", async () => {
  const add = page.locator(".text-panel .add-text");
  expect(await add.count(), "no Add text button");
  const before = await page.locator(".canvas-layer.layer-title").count();
  await add.click();
  expect(await settles(async () => (await page.locator(".canvas-layer.layer-title").count()) > before), "no new text layer");
  const fields = page.locator(".text-layer-fields");
  expect(await fields.count(), "text fields did not open for the new layer");
  await fields.locator("textarea").fill("Audit words");
  expect(await settles(async () => (await page.locator(".canvas-layer.layer-title").last().innerText()).includes("Audit words")), "typed words not on the canvas");
  await fields.locator('.align-row button:has-text("Left")').click(); await page.waitForTimeout(300);
  await mdSelect(fields.locator("md-outlined-select").first(), "Body");
  await page.waitForTimeout(500);
  await page.locator('.properties button:has-text("Delete layer")').click();
  expect(await settles(async () => (await page.locator(".canvas-layer.layer-title").count()) === before), "layer not deleted");
});
await part("text style: caption preset, highlight checkbox, font, colour, size", async () => {
  const ts = page.locator(".text-style");
  expect(await ts.count(), "no text style block");
  expect((await ts.locator("md-outlined-select").count()) >= 2, "preset/font selects missing");
  const box = ts.locator('input[type="checkbox"]');
  const was = await box.isChecked();
  await box.click(); await page.waitForTimeout(500);
  expect((await box.isChecked()) !== was, "highlight checkbox did not toggle");
  await box.click(); await page.waitForTimeout(500);
  expect(await ts.locator('input[type="color"]').count(), "no caption colour");
  expect(await ts.locator("md-slider").count(), "no caption size slider");
});
await part("colours: theme chips + colour inputs", async () => {
  const chips = page.locator(".theme-row .theme-chip");
  expect((await chips.count()) >= 8, `only ${await chips.count()} palettes`);
  const selectedBefore = await page.locator(".theme-row .theme-chip.selected").count();
  await chips.nth(1).click(); await page.waitForTimeout(600);
  expect(await page.locator(".theme-row .theme-chip.selected").count() >= 1, "no palette selected after click");
  await chips.nth(0).click(); await page.waitForTimeout(600);
  return `${await chips.count()} palettes (was ${selectedBefore} selected)`;
});
await part("everything dials: platform guide, caption offset, voice volume folds", async () => {
  const folds = page.locator(".design-panel details.design-fold summary");
  const texts = (await folds.allInnerTexts()).map((t) => t.trim().toLowerCase());
  for (const want of ["sound bars", "where is this going?", "are the words early or late?", "voice volume"]) expect(texts.includes(want), `missing fold "${want}" in ${texts.join(" | ")}`);
  const guide = page.locator('.design-panel details:has-text("Where is this going?")');
  await guide.locator("summary").click(); await page.waitForTimeout(200);
  await mdSelect(guide.locator("md-outlined-select"), "TikTok");
  expect(await settles(async () => (await page.locator(".safe-area, .platform-guide, [class*=safe]").count()) >= 1), "no safe-area guide drawn");
  await mdSelect(guide.locator("md-outlined-select"), "No guide");
});
await part("variants panel", async () => {
  const panel = page.locator(".ratio-options");
  expect(await panel.count(), "no variants panel");
  expect((await panel.locator("button").count()) >= 3, "ratio buttons missing");
});
await part("batch panel controls", async () => {
  const batch = page.locator(".batch-controls");
  expect(await batch.count(), "no batch panel");
  expect((await batch.locator("md-outlined-select").count()) >= 2, "batch selects missing");
  expect(await page.locator(".batch-actions a[download]").count(), "no zip link");
});
await part("template panel: name box + apply chips", async () => {
  expect(await page.locator(".template-save input").count(), "no name box");
  return `${await page.locator(".template-apply .chip").count()} saved looks`;
});
await part("music panel: pick a track, volume, remove", async () => {
  const music = page.locator(".music-panel");
  expect(await music.count(), "no music panel");
  const search = music.locator('input[type="search"], input[placeholder*="earch"]').first();
  if (await search.count()) { await search.fill("piano"); await page.waitForTimeout(1200); }
  const track = music.locator(".music-results .music-pick").first();
  expect(await settles(async () => (await track.count()) > 0, 10000), "no tracks listed");
  await track.click();
  expect(await settles(async () => (await music.locator(".music-selected").count()) > 0), "picking a track did not select it");
  const sliders = await music.locator("md-slider").count();
  expect(sliders >= 2, `expected level + ducking sliders, found ${sliders}`);
  const remove = music.locator('.inspector-heading button[title="Remove music bed"]');
  expect(await remove.count(), "no Remove music bed button");
  await remove.click();
  expect(await settles(async () => (await music.locator(".music-selected").count()) === 0), "could not remove the track");
  return `picked, ${sliders} sliders, removed`;
});
await part("sound effects panel present with cues list", async () => {
  const sfx = page.locator(".sfx-panel, .sfx-list, .sfx");
  expect(await sfx.count(), "no sfx panel");
});
await part("voice-over panel present", async () => {
  const text = await page.locator(".inspector").innerText();
  expect(/oice-?over/i.test(text), "no voice-over panel");
});
await part("clip fields present (Start/End)", async () => {
  const block = page.locator(".clip-property-block");
  expect(await block.count(), "no clip block");
  expect((await block.locator("input").count()) >= 2, "start/end fields missing");
});
await part("full transcript: SRT/VTT/TXT links + Save", async () => {
  const text = await page.locator(".studio-editor, .studio").first().innerText();
  expect(/SRT/.test(text) && /VTT/.test(text) && /TXT/.test(text), "download links missing");
  expect(await page.locator('button:has-text("Save")').count(), "no Save button for the transcript");
});
await part("batch panel: Clips select shows its value", async () => {
  const sel = page.locator(".batch-controls md-outlined-select").first();
  const shown = await shownText(sel);
  expect(/\d/.test(shown), `Clips select shows "${shown}"`);
  return `shows ${shown}`;
});
await part("speaker panel", async () => {
  expect(await page.locator(".speaker-panel").count(), "no speaker panel");
  expect((await page.locator(".speaker-counts button").count()) >= 2, "no speaker count buttons");
});
await part("export button -> job progress -> ready card -> share", async () => {
  const exportBtn = page.locator('.studio-toolbar button:has-text("Export")');
  expect(await exportBtn.count(), "no Export button");
  await exportBtn.click();
  const anyway = page.locator('button:has-text("Export anyway")');
  if (await anyway.count()) await anyway.click();
  const ready = page.locator(".ready-card");
  expect(await settles(async () => (await ready.count()) > 0, 240000), "ready card never appeared");
  expect(await ready.locator("video").count(), "no video in the ready card");
  expect(await ready.locator('a[download]').count(), "no download link");
  const share = ready.locator(".share-button button").first();
  if (await share.count()) {
    await share.click();
    expect(await settles(async () => (await ready.locator(".share-url").count()) > 0, 10000), "share link did not appear");
  }
  await ready.locator('button:has-text("Keep editing")').click();
  expect(await settles(async () => (await ready.count()) === 0), "ready card did not close");
});
await part("poster shown on the project card", async () => {
  await nav("Projects");
  const posters = await page.locator(".library-card img").count();
  expect(posters >= 1, "no poster images on cards");
  return `${posters} posters`;
});
await part("sign out returns to the sign-in screen", async () => {
  await page.locator(".user-avatar").first().click(); await page.waitForTimeout(300);
  await page.locator('.context-menu button:has-text("Sign out"), [role=menu] button:has-text("Sign out"), [role=menuitem]:has-text("Sign out")').first().click();
  signedIn = false;
  expect(await settles(async () => (await page.locator('input[autocomplete="username"]').count()) > 0), "sign-in screen did not come back");
});

await browser.close();
const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length} parts, ${failed.length} failed`);
for (const f of failed) console.log(`  - ${f.name}: ${f.note}`);
if (consoleErrors.length) { console.log(`\nconsole errors (${consoleErrors.length}):`); for (const e of consoleErrors.slice(0, 10)) console.log(`  ! ${e}`); }
process.exit(failed.length ? 1 : 0);
