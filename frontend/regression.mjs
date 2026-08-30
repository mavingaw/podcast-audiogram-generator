/**
 * Full-flow regression through a real browser: pick a source, type a clip
 * range, open Studio, add an effect, export, download the MP4.
 *
 * The API tests cannot see a React crash and the smoke test does not
 * export. This does what a person does, against whatever URL it is given —
 * by default the public one, through Cloudflare, which is where the real
 * problems (100 MB body cap, stale bundles, slow first paint) have lived.
 *
 *   node regression.mjs --base-url https://kinder.example.com \
 *       --username mujin --password … [--source "Season 4"]
 *
 * Exits non-zero on any problem, so a scheduler can notice.
 */
import { chromium } from "playwright";

const arg = (name, fallback) => {
  const index = process.argv.indexOf(`--${name}`);
  return index > -1 ? process.argv[index + 1] : fallback;
};
const BASE = arg("base-url", process.env.KINDER_URL || "http://127.0.0.1:8080");
const USERNAME = arg("username", process.env.KINDER_USER || "demo");
const PASSWORD = arg("password", process.env.KINDER_PASSWORD || "");
const SOURCE = arg("source", process.env.KINDER_SOURCE || "");

const problems = [];
const log = (...a) => console.log(new Date().toISOString(), ...a);
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1500, height: 950 } });
page.on("pageerror", (e) => problems.push("pageerror: " + e.message));
page.on("response", (r) => {
  if (r.status() >= 400 && !/\/api\/me$/.test(r.url())) problems.push(`HTTP ${r.status()} ${r.request().method()} ${r.url()}`);
});
const api = (path, init) => page.evaluate(async ({ path, init }) => {
  const r = await fetch(path, { credentials: "include", ...(init || {}) });
  return { status: r.status, body: await r.json().catch(() => null) };
}, { path, init });

let projectId = null;
try {
  await page.goto(BASE, { waitUntil: "networkidle" });
  await page.fill('input[autocomplete="username"]', USERNAME);
  await page.fill('input[type="password"]', PASSWORD);
  await page.click(".primary");
  await page.waitForTimeout(2500);

  await page.locator(".new-project").first().click();
  await page.waitForTimeout(600);
  await page.locator(".destination-grid button").first().click();
  await page.locator(".flow-next .primary").click();
  await page.waitForTimeout(600);
  const rows = page.locator(".source-row button");
  const source = SOURCE ? rows.filter({ hasText: SOURCE }).first() : rows.first();
  if ((await source.count()) === 0) throw new Error("no source to pick");
  await source.click();
  await page.locator(".flow-next .primary").click();
  await page.waitForTimeout(1500);

  const nums = page.locator('.clip-fields input');
  await nums.nth(0).click({ clickCount: 3 });
  await page.keyboard.type("60", { delay: 30 });
  await page.keyboard.press("Tab");
  await nums.nth(1).click({ clickCount: 3 });
  await page.keyboard.type("80", { delay: 30 });
  await page.keyboard.press("Tab");
  await page.waitForTimeout(500);
  await page.locator(".flow-next .primary").click();
  await page.waitForTimeout(600);
  await page.locator(".flow-next .primary").click();
  await page.waitForTimeout(800);

  const before = (await api("/api/projects")).body.projects.length;
  const t0 = Date.now();
  await page.locator('button:has-text("Open in Studio")').first().click();
  await page.waitForFunction(() => {
    const el = document.querySelector("audio,video");
    return el && el.readyState >= 3;
  }, null, { timeout: 60000 }).catch(() => problems.push("Studio player never became playable"));
  log(`studio playable after ${Date.now() - t0} ms`);
  const projects = (await api("/api/projects")).body.projects;
  if (projects.length !== before + 1) problems.push("Open in Studio did not create a project");
  const project = projects.find((p) => p.clip_start === 60 && p.clip_end === 80) || projects[0];
  projectId = project.id;

  // The effects panel lives under "Everything".
  await page.locator('.studio-mode button:has-text("Everything")').click().catch(() => {});
  await page.waitForTimeout(400);
  await page.locator(".sfx-panel input").first().fill("whoosh");
  await page.waitForTimeout(3000);
  const plus = page.locator('.sfx-result button[title^="Add at"]').first();
  if (await plus.count()) {
    await plus.click();
    await page.waitForTimeout(800);
  } else problems.push("no effects listed for 'whoosh'");

  await page.getByRole("button", { name: /^Export$/ }).first().click().catch(() => problems.push("no Export button"));
  let job = null;
  for (let i = 0; i < 60; i++) {
    await page.waitForTimeout(4000);
    const jobs = (await api("/api/jobs")).body.jobs.filter((j) => j.kind === "render" && j.subject_id === projectId);
    job = jobs[0];
    if (job && ["complete", "failed"].includes(job.status)) break;
  }
  if (job?.status !== "complete") problems.push(`render ${job?.status ?? "never appeared"}: ${job?.error ?? ""}`);
  else {
    // The person must be told, on the screen they are looking at.
    await page.waitForTimeout(3500);
    if ((await page.locator(".ready-card").count()) === 0) problems.push("no 'Your video is ready' card after the render");
    else {
      log("ready card shown");
      // Copy link must produce a public page that opens with no session.
      await page.locator(".ready-card .share-button button").first().click();
      await page.waitForTimeout(1500);
      const url = await page.locator(".ready-card .share-url").inputValue().catch(() => "");
      if (!url.includes("/s/")) problems.push("copy link did not produce a share link");
      else {
        const anon = await browser.newContext();
        const p2 = await anon.newPage();
        const res = await p2.goto(url);
        if (!res || res.status() !== 200) problems.push(`share page returned ${res?.status()}`);
        else log("share page opens without a session");
        await anon.close();
      }
      const chips = await page.locator(".ready-card .destination-chip").count();
      if (chips < 5) problems.push(`ready card lists only ${chips} platforms`);
    }
    const size = await page.evaluate(async (id) => (await (await fetch(`/api/projects/${id}/outputs/audiogram.mp4`, { credentials: "include" })).arrayBuffer()).byteLength, projectId);
    log(`mp4 ${size} bytes`);
    if (size < 100_000) problems.push(`mp4 suspiciously small: ${size} bytes`);
  }
} catch (error) {
  problems.push("flow aborted: " + (error instanceof Error ? error.message : String(error)));
} finally {
  if (projectId) await api(`/api/projects/${projectId}`, { method: "DELETE" }).catch(() => undefined);
  await browser.close();
}

if (problems.length) {
  console.error("REGRESSION FAILED\n  " + problems.join("\n  "));
  process.exit(1);
}
log("regression passed");
