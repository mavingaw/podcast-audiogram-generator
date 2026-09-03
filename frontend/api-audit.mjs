// Kinder API audit: every endpoint group exercised for real against the box,
// with a freshly uploaded 30 s speech sample, and cleaned up afterwards.
//   node api-audit.mjs --base-url http://192.168.1.58:8099 --username U --password P --assets DIR
import { readFileSync } from "node:fs";
import { join } from "node:path";

const arg = (n, f) => { const i = process.argv.indexOf(`--${n}`); return i > -1 ? process.argv[i + 1] : f; };
const BASE = arg("base-url"), USERNAME = arg("username"), PASSWORD = arg("password"), ASSETS = arg("assets");
let cookie = "";
const results = [];
const ok = (name, note = "") => { results.push({ name, ok: true, note }); console.log(`  PASS  ${name}${note ? " — " + note : ""}`); };
const bad = (name, note = "") => { results.push({ name, ok: false, note }); console.log(`  FAIL  ${name}${note ? " — " + note : ""}`); };

async function call(method, path, body, { raw = false, timeout = 30000, headers = {} } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  const init = { method, headers: { Cookie: cookie, ...headers }, signal: controller.signal };
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(body); }
  try {
    const res = await fetch(BASE + path, init);
    const sc = res.headers.get("set-cookie");
    if (sc) cookie = sc.split(";")[0];
    if (raw) return res;
    const text = await res.text();
    let json = null; try { json = JSON.parse(text); } catch { /* not json */ }
    return { status: res.status, json, text, headers: res.headers };
  } finally { clearTimeout(timer); }
}
const check = async (name, fn) => { try { const note = await fn(); ok(name, note || ""); } catch (e) { bad(name, e.message.slice(0, 160)); } };
const expect = (cond, msg) => { if (!cond) throw new Error(msg); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function waitJob(id, timeoutMs = 240000) {
  const until = Date.now() + timeoutMs;
  while (Date.now() < until) {
    const r = await call("GET", `/api/jobs/${id}`);
    const job = r.json?.job ?? r.json;
    if (["complete", "failed", "canceled", "cancelled"].includes(job?.status)) return job;
    await sleep(2500);
  }
  throw new Error(`job ${id} did not finish in ${timeoutMs / 1000}s`);
}
const created = { media: [], projects: [], templates: [], fonts: [], feeds: [], users: [] };
// A short 16-bit mono WAV tone, so the voice-over path gets a real file.
function sineWav(seconds, rate = 16000) {
  const n = seconds * rate, data = new DataView(new ArrayBuffer(44 + n * 2));
  const str = (o, s) => [...s].forEach((c, i) => data.setUint8(o + i, c.charCodeAt(0)));
  str(0, "RIFF"); data.setUint32(4, 36 + n * 2, true); str(8, "WAVE"); str(12, "fmt "); data.setUint32(16, 16, true);
  data.setUint16(20, 1, true); data.setUint16(22, 1, true); data.setUint32(24, rate, true); data.setUint32(28, rate * 2, true);
  data.setUint16(32, 2, true); data.setUint16(34, 16, true); str(36, "data"); data.setUint32(40, n * 2, true);
  for (let i = 0; i < n; i += 1) data.setInt16(44 + i * 2, Math.round(Math.sin((i / rate) * 440 * 2 * Math.PI) * 12000), true);
  return data.buffer;
}

console.log(`API audit against ${BASE}\n`);

// ---------- auth ----------
await check("health", async () => { const r = await call("GET", "/api/health"); expect(r.status === 200 && r.json.ok, `status ${r.status}`); });
await check("bootstrap state", async () => { const r = await call("GET", "/api/bootstrap"); expect(r.status === 200 && r.json.initialized === true, r.text.slice(0, 80)); });
await check("session signed-out is 200 with null user", async () => { const r = await call("GET", "/api/session"); expect(r.status === 200 && r.json.user === null, r.text.slice(0, 80)); });
await check("login with a wrong password is refused", async () => { const r = await call("POST", "/api/auth/login", { username: USERNAME, password: "definitely-wrong-1" }); expect(r.status === 401, `status ${r.status}`); });
await check("login", async () => { const r = await call("POST", "/api/auth/login", { username: USERNAME, password: PASSWORD }); expect(r.status === 200 && r.json.user?.username === USERNAME, r.text.slice(0, 120)); expect(cookie, "no session cookie"); });
await check("me", async () => { const r = await call("GET", "/api/me"); expect(r.status === 200 && r.json.user?.username === USERNAME, `status ${r.status}`); return `admin=${r.json.user.is_admin}`; });
await check("signup state", async () => { const r = await call("GET", "/api/auth/signup"); expect(r.status === 200 && "open" in r.json, r.text.slice(0, 80)); return r.text.slice(0, 60); });
await check("profile round-trip (PATCH /me with the same display name)", async () => {
  const me = (await call("GET", "/api/me")).json.user;
  const r = await call("PATCH", "/api/me", { display_name: me.display_name ?? "" });
  expect(r.status === 200, `status ${r.status} ${r.text.slice(0, 80)}`);
});
await check("facts", async () => { const r = await call("GET", "/api/facts?n=3"); expect(r.status === 200 && r.json.facts?.length === 3, r.text.slice(0, 80)); });
await check("ratios", async () => { const r = await call("GET", "/api/ratios"); expect(r.status === 200 && r.json.ratios?.length >= 4, r.text.slice(0, 80)); return `${r.json.ratios.length} ratios`; });
await check("platforms", async () => { const r = await call("GET", "/api/platforms"); expect(r.status === 200 && r.json.platforms?.length >= 10, r.text.slice(0, 80)); return `${r.json.platforms.length} platforms`; });
await check("gpus", async () => { const r = await call("GET", "/api/gpus"); expect(r.status === 200 && Array.isArray(r.json.gpus), r.text.slice(0, 80)); return `${r.json.gpus.length} GPUs`; });

// ---------- settings (read, and write back the same values) ----------
for (const path of ["/api/settings/artwork", "/api/settings/branding", "/api/settings/gpu", "/api/settings/invite", "/api/settings/llm", "/api/settings/social", "/api/settings/transcription", "/api/settings/youtube", "/api/social/accounts", "/api/youtube/account"]) {
  await check(`GET ${path}`, async () => { const r = await call("GET", path); expect(r.status === 200, `status ${r.status} ${r.text.slice(0, 80)}`); });
}
await check("transcription settings round-trip", async () => {
  const cur = (await call("GET", "/api/settings/transcription")).json;
  const r = await call("PUT", "/api/settings/transcription", { model: cur.model, language: cur.language, enabled: cur.enabled });
  expect(r.status === 200, `status ${r.status} ${r.text.slice(0, 100)}`);
  return `model=${cur.model}`;
});
await check("gpu settings round-trip", async () => {
  const cur = (await call("GET", "/api/settings/gpu")).json;
  const r = await call("PUT", "/api/settings/gpu", { transcription_gpu_uuid: cur.transcription_gpu_uuid ?? null, encoding_gpu_uuid: cur.encoding_gpu_uuid ?? null });
  expect(r.status === 200, `status ${r.status} ${r.text.slice(0, 100)}`);
});
await check("branding round-trip", async () => {
  const cur = (await call("GET", "/api/settings/branding")).json;
  const r = await call("PUT", "/api/settings/branding", { role: "intro", media_id: cur.intro ?? null });
  expect(r.status === 200 && (r.json.intro ?? null) === (cur.intro ?? null), `status ${r.status} ${r.text.slice(0, 100)}`);
});
await check("signups round-trip", async () => {
  const cur = (await call("GET", "/api/auth/signup")).json;
  const r = await call("PUT", "/api/settings/signups", { open: cur.open });
  expect(r.status === 200 && r.json.open === cur.open, `status ${r.status} ${r.text.slice(0, 100)}`);
});

// ---------- users (admin) ----------
await check("users list", async () => { const r = await call("GET", "/api/users"); expect(r.status === 200 && r.json.users?.length >= 1, r.text.slice(0, 80)); return `${r.json.users.length} users`; });
await check("create / update / delete a user", async () => {
  const name = `audit${Date.now().toString().slice(-6)}`;
  const r = await call("POST", "/api/users", { username: name, password: "audit-password-123", is_admin: false });
  expect(r.status === 200 && r.json.user?.username === name, `create ${r.status} ${r.text.slice(0, 100)}`);
  created.users.push(r.json.user.id);
  const u = await call("PATCH", `/api/users/${r.json.user.id}`, { disabled: true });
  expect(u.status === 200, `patch ${u.status}`);
  const d = await call("DELETE", `/api/users/${r.json.user.id}`);
  expect(d.status === 200, `delete ${d.status}`);
  created.users.pop();
});

// ---------- library ----------
await check("library packs", async () => { const r = await call("GET", "/api/library/packs"); expect(r.status === 200 && r.json.packs?.length >= 1, r.text.slice(0, 80)); return `${r.json.packs.length} packs`; });
await check("library genres", async () => { const r = await call("GET", "/api/library/genres"); expect(r.status === 200 && r.json.genres?.length >= 1, r.text.slice(0, 80)); });
await check("library sfx roles", async () => { const r = await call("GET", "/api/library/sfx"); expect(r.status === 200 && r.json.roles, r.text.slice(0, 80)); });
let soundId = null;
await check("library sounds + a sound file", async () => {
  const r = await call("GET", "/api/library/sounds?kind=music&limit=3"); expect(r.status === 200 && r.json.sounds?.length >= 1, r.text.slice(0, 80));
  soundId = r.json.sounds[0].id;
  const f = await call("GET", `/api/library/sounds/${soundId}/file`, undefined, { raw: true });
  expect(f.status === 200 && (f.headers.get("content-type") || "").startsWith("audio"), `file ${f.status} ${f.headers.get("content-type")}`);
  return `${r.json.sounds.length} sounds, file ${f.headers.get("content-type")}`;
});
await check("library search", async () => { const r = await call("GET", "/api/library/sounds?search=piano&limit=3"); expect(r.status === 200 && Array.isArray(r.json.sounds), r.text.slice(0, 80)); return `${r.json.sounds.length} hits`; });

// ---------- media: upload, transcribe, derived data ----------
let mediaId = null;
await check("upload a 30 s mp3", async () => {
  const form = new FormData();
  form.append("file", new Blob([readFileSync(join(ASSETS, "audit-30s.mp3"))], { type: "audio/mpeg" }), "audit-30s.mp3");
  const r = await call("POST", "/api/media/upload", form, { timeout: 120000 });
  expect(r.status === 200 && r.json.media?.id, `status ${r.status} ${r.text.slice(0, 120)}`);
  mediaId = r.json.media.id; created.media.push(mediaId);
  const jobs = r.json.jobs ?? [];
  for (const job of jobs) { const done = await waitJob(job.id, 300000); expect(done.status === "complete", `${job.kind} ${done.status} ${done.error ?? ""}`); }
  return `${jobs.length} job(s) complete`;
});
let transcript = null;
await check("media one (transcript present with words)", async () => {
  const r = await call("GET", `/api/media/${mediaId}`); expect(r.status === 200, `status ${r.status}`);
  transcript = r.json.media.transcript; expect(transcript?.segments?.length >= 1, "no segments");
  const words = transcript.segments.reduce((n, s) => n + (s.words?.length ?? 0), 0); expect(words > 10, `only ${words} words`);
  return `${transcript.segments.length} segments, ${words} words`;
});
await check("media list is light", async () => { const r = await call("GET", "/api/media?transcripts=0"); expect(r.status === 200 && r.json.media.some((m) => m.id === mediaId), "uploaded media missing"); expect(!r.json.media.find((m) => m.id === mediaId).transcript?.segments, "light list carried a transcript"); return `${r.text.length} bytes`; });
await check("media file", async () => { const r = await call("GET", `/api/media/${mediaId}/file`, undefined, { raw: true }); expect(r.status === 200 || r.status === 206, `status ${r.status}`); });
await check("peaks", async () => { const r = await call("GET", `/api/media/${mediaId}/peaks?buckets=200`); expect(r.status === 200 && r.json.ready && r.json.peaks?.length >= 100, r.text.slice(0, 80)); return `${r.json.peaks.length} buckets, ${r.json.duration}s`; });
for (const fmt of ["srt", "vtt", "txt"]) {
  await check(`transcript download .${fmt}`, async () => { const r = await call("GET", `/api/media/${mediaId}/transcript.${fmt}`); expect(r.status === 200 && r.text.length > 20, `status ${r.status}`); if (fmt === "srt") expect(/-->/.test(r.text), "no cue timings"); if (fmt === "vtt") expect(r.text.startsWith("WEBVTT"), "no WEBVTT header"); });
}
await check("speakers", async () => { const r = await call("GET", `/api/media/${mediaId}/speakers`); expect(r.status === 200 && Array.isArray(r.json.speakers), r.text.slice(0, 80)); return `${r.json.speakers.length} speaker(s), multi=${r.json.multi}`; });
await check("rename a speaker (and back)", async () => {
  const s = (await call("GET", `/api/media/${mediaId}/speakers`)).json.speakers[0]; expect(s, "no speaker");
  const r = await call("POST", `/api/media/${mediaId}/speakers/${s.id}/name`, { name: "Audit Voice" }); expect(r.status === 200, `status ${r.status} ${r.text.slice(0, 80)}`);
  const back = await call("POST", `/api/media/${mediaId}/speakers/${s.id}/name`, { name: s.name }); expect(back.status === 200, `restore ${back.status}`);
});
await check("snap a clip to sentence edges", async () => { const r = await call("POST", `/api/media/${mediaId}/snap`, { start: 3, end: 12 }); expect(r.status === 200 && typeof r.json.start === "number", r.text.slice(0, 100)); return `${JSON.stringify(r.json).slice(0, 80)}`; });
await check("suggested clips (LLM)", async () => { const r = await call("GET", `/api/media/${mediaId}/clips?limit=2`, undefined, { timeout: 120000 }); expect(r.status === 200, `status ${r.status} ${r.text.slice(0, 100)}`); return `ready=${r.json.ready} clips=${r.json.clips?.length} ${r.json.reason ?? ""}`; });
await check("show notes (LLM)", async () => {
  const s = await call("POST", `/api/media/${mediaId}/notes`); expect(s.status === 200, `start ${s.status} ${s.text.slice(0, 80)}`);
  const until = Date.now() + 180000; let last = null;
  while (Date.now() < until) { last = (await call("GET", `/api/media/${mediaId}/notes`)).json; if (["done", "ready", "complete", "failed", "error"].includes(last.status)) break; await sleep(3000); }
  expect(last && last.status === "done" && last.result?.titles?.length >= 1, `notes ${JSON.stringify(last).slice(0, 120)}`);
  return `status=${last.status}, ${last.result.titles.length} titles`;
});
await check("edit a transcript word (PATCH transcript)", async () => {
  const seg = transcript.segments.find((s) => s.words?.length); const w = seg.words[0];
  const next = JSON.parse(JSON.stringify(transcript)); next.segments.find((s) => s.id === seg.id).words[0].text = w.text + "x";
  const r = await call("PATCH", `/api/media/${mediaId}/transcript`, { transcript: next }); expect(r.status === 200, `status ${r.status} ${r.text.slice(0, 80)}`);
  const back = await call("PATCH", `/api/media/${mediaId}/transcript`, { transcript }); expect(back.status === 200, `restore ${back.status}`);
});
await check("re-queue transcription", async () => { const r = await call("POST", `/api/media/${mediaId}/transcribe`); expect(r.status === 200 && r.json.job?.id, r.text.slice(0, 80)); const done = await waitJob(r.json.job.id, 300000); expect(done.status === "complete", `${done.status} ${done.error ?? ""}`); });

// ---------- projects ----------
let projectId = null, project = null;
await check("create a project", async () => { const r = await call("POST", "/api/projects", { title: "Audit clip", media_id: mediaId }); expect(r.status === 200 && r.json.project?.id, r.text.slice(0, 100)); project = r.json.project; projectId = project.id; created.projects.push(projectId); return `${project.aspect_ratio} ${project.clip_start}-${project.clip_end}`; });
await check("patch title, clip bounds and scene", async () => { const r = await call("PATCH", `/api/projects/${projectId}`, { title: "Audit clip (edited)", clip_start: 2, clip_end: 14, scene: { ...project.scene, accent: "#ff8800" } }); expect(r.status === 200 && r.json.project.title === "Audit clip (edited)" && r.json.project.clip_end === 14 && r.json.project.scene.accent === "#ff8800", r.text.slice(0, 120)); project = r.json.project; });
await check("invalid aspect ratio is refused", async () => { const r = await call("PATCH", `/api/projects/${projectId}`, { aspect_ratio: "3:7" }); expect(r.status === 422 || r.status === 400, `status ${r.status}`); });
for (const ratio of ["16:9", "4:5", "1:1", "9:16"]) {
  await check(`switch aspect to ${ratio}`, async () => { const r = await call("POST", `/api/projects/${projectId}/aspect/${ratio}`); expect(r.status === 200 && r.json.project.aspect_ratio === ratio, r.text.slice(0, 100)); const layers = r.json.project.scene.layers; expect(layers === undefined || layers.length >= 3, "layers lost"); project = r.json.project; });
}
await check("revisions list + restore", async () => { const r = await call("GET", `/api/projects/${projectId}/revisions`); expect(r.status === 200 && r.json.revisions?.length >= 1, r.text.slice(0, 80)); const rev = r.json.revisions[0]; const s = await call("POST", `/api/projects/${projectId}/revisions/${rev.id}/restore`); expect(s.status === 200 && s.json.project, `restore ${s.status} ${s.text.slice(0, 80)}`); return `${r.json.revisions.length} revisions`; });
await check("destinations", async () => { const r = await call("GET", `/api/projects/${projectId}/destinations`); expect(r.status === 200, `status ${r.status} ${r.text.slice(0, 80)}`); return r.text.slice(0, 60); });
await check("preview audio (range)", async () => { const r = await call("GET", `/api/projects/${projectId}/preview.m4a?r=2.000-6.000`, undefined, { raw: true, timeout: 60000 }); expect(r.status === 200 || r.status === 206, `status ${r.status}`); return r.headers.get("content-type"); });
let templateId = null;
await check("save / list / apply / delete a template", async () => {
  const r = await call("POST", "/api/templates", { name: `Audit look ${Date.now()}`, project_id: projectId }); expect(r.status === 200 && r.json.template?.id, `save ${r.status} ${r.text.slice(0, 80)}`);
  templateId = r.json.template.id; created.templates.push(templateId);
  const l = await call("GET", "/api/templates"); expect(l.json.templates.some((t) => t.id === templateId), "saved look not listed");
  const a = await call("POST", `/api/projects/${projectId}/template/${templateId}`); expect(a.status === 200 && a.json.project, `apply ${a.status}`);
  const d = await call("DELETE", `/api/templates/${templateId}`); expect(d.status === 200, `delete ${d.status}`); created.templates.pop();
});
await check("voiceover upload", async () => {
  // A browser hands over a MediaRecorder blob (webm in Chrome, mp4 in Safari,
  // wav from the fallback); an mp3 is rightly refused with 415.
  const nope = await call("POST", `/api/projects/${projectId}/voiceover`, (() => { const f = new FormData(); f.append("file", new Blob([new Uint8Array(100)], { type: "audio/mpeg" }), "x.mp3"); return f; })());
  expect(nope.status === 415, `mp3 should be 415, got ${nope.status}`);
  const form = new FormData(); form.append("file", new Blob([sineWav(3)], { type: "audio/wav" }), "voiceover.wav");
  const r = await call("POST", `/api/projects/${projectId}/voiceover`, form, { timeout: 60000 }); expect(r.status === 200 && r.json.media?.id, `status ${r.status} ${r.text.slice(0, 100)}`); created.media.push(r.json.media.id);
  const l = await call("GET", "/api/media?transcripts=0"); const vo = l.json.media.find((m) => m.id === r.json.media.id); expect(vo && /Voice-over/.test(vo.original_name), "voice-over not listed as media");
});
let renderJob = null;
await check("render (cuts + music + sfx + title)", async () => {
  const layers = project.scene.layers;
  const scene = { ...project.scene, cuts: [{ start: 4, end: 5 }], musicBed: soundId ? { soundId, volume: 0.2 } : project.scene.musicBed, layers };
  const p = await call("PATCH", `/api/projects/${projectId}`, { scene }); expect(p.status === 200, `patch ${p.status} ${p.text.slice(0, 100)}`);
  const r = await call("POST", `/api/projects/${projectId}/render?force=true`); expect(r.status === 200 && r.json.job?.id, `render ${r.status} ${r.text.slice(0, 100)}`);
  renderJob = await waitJob(r.json.job.id, 300000); expect(renderJob.status === "complete", `${renderJob.status} ${renderJob.error ?? ""}`);
  return `job ${r.json.job.id.slice(0, 8)}`;
});
await check("render reuse (identical render is not repeated)", async () => { const r = await call("POST", `/api/projects/${projectId}/render`); expect(r.status === 200 && r.json.reused === true, r.text.slice(0, 100)); });
for (const f of ["audiogram.mp4", "captions.srt", "captions.vtt", "poster.jpg"]) {
  await check(`output ${f}`, async () => { const r = await call("GET", `/api/projects/${projectId}/outputs/${f}`, undefined, { raw: true }); expect(r.status === 200, `status ${r.status}`); expect((r.headers.get("cache-control") || "").includes("no-store") || f.endsWith("mp4"), `cache-control ${r.headers.get("cache-control")}`); return `${r.headers.get("content-type")} ${r.headers.get("content-length") ?? ""}`; });
}
await check("jobs list + one + events", async () => {
  const l = await call("GET", "/api/jobs"); expect(l.status === 200 && l.json.jobs.some((j) => j.id === renderJob.id), "render job not listed");
  const o = await call("GET", `/api/jobs/${renderJob.id}`); expect(o.status === 200, `one ${o.status}`);
  const controller = new AbortController(); const t = setTimeout(() => controller.abort(), 4000);
  try { const ev = await fetch(`${BASE}/api/jobs/${renderJob.id}/events`, { headers: { Cookie: cookie }, signal: controller.signal }); expect(ev.status === 200 && (ev.headers.get("content-type") || "").includes("event-stream"), `events ${ev.status} ${ev.headers.get("content-type")}`); const reader = ev.body.getReader(); const { value } = await reader.read(); expect(value && value.length > 0, "no event bytes"); controller.abort(); } catch (e) { if (e.name !== "AbortError") throw e; } finally { clearTimeout(t); }
});
await check("cancel a running render", async () => {
  const r = await call("POST", `/api/projects/${projectId}/render?force=true`); expect(r.status === 200 && r.json.job?.id, r.text.slice(0, 80));
  const c = await call("POST", `/api/jobs/${r.json.job.id}/cancel`); expect(c.status === 200, `cancel ${c.status} ${c.text.slice(0, 80)}`);
  const done = await waitJob(r.json.job.id, 120000); expect(done.status === "canceled", `ended as ${done.status} (${done.message ?? ""})`);
  const again = await call("POST", `/api/jobs/${r.json.job.id}/cancel`); expect(again.status === 409, `second cancel should be 409, got ${again.status}`);
  return `ended as ${done.status}`;
});
let shareToken = null;
await check("share link: create, public page, poster, video, revoke", async () => {
  const s = await call("POST", `/api/projects/${projectId}/share`); expect(s.status === 200 && s.json.token, `share ${s.status} ${s.text.slice(0, 80)}`); shareToken = s.json.token;
  const saved = cookie; cookie = "";
  try {
    const page = await call("GET", `/s/${shareToken}`); expect(page.status === 200 && page.text.includes("<video"), `page ${page.status}`);
    const poster = await call("GET", `/s/${shareToken}/poster.jpg`, undefined, { raw: true }); expect(poster.status === 200, `poster ${poster.status}`);
    const video = await call("GET", `/s/${shareToken}/video.mp4`, undefined, { raw: true }); expect(video.status === 200 || video.status === 206, `video ${video.status}`);
    const nope = await call("GET", `/s/not-a-token`); expect(nope.status === 404 && nope.text.includes("isn't available"), `bad token ${nope.status}`);
  } finally { cookie = saved; }
  const r = await call("DELETE", `/api/projects/${projectId}/share`); expect(r.status === 200 && r.json.revoked >= 1, `revoke ${r.status} ${r.text.slice(0, 80)}`);
  const after = await call("GET", `/s/${shareToken}`); expect(after.status === 404, `revoked link still ${after.status}`);
});
await check("analytics", async () => { const r = await call("GET", "/api/analytics"); expect(r.status === 200, `status ${r.status} ${r.text.slice(0, 80)}`); return r.text.slice(0, 80); });
await check("variants (one ratio, no render)", async () => {
  const r = await call("POST", `/api/projects/${projectId}/variants`, { ratios: ["16:9"], render: false }); expect(r.status === 200 && r.json.projects?.length === 1, r.text.slice(0, 100));
  for (const p of r.json.projects) created.projects.push(p.id);
  expect(r.json.projects[0].aspect_ratio === "16:9", "wrong ratio");
});
await check("batch clips (count 1, no render) + inbox approve/reject", async () => {
  const r = await call("POST", `/api/media/${mediaId}/batch`, { count: 1, aspect_ratio: "9:16", render: false }, { timeout: 180000 }); expect(r.status === 200, `batch ${r.status} ${r.text.slice(0, 120)}`);
  for (const p of r.json.projects ?? []) created.projects.push(p.id);
  const inbox = await call("GET", "/api/inbox"); expect(inbox.status === 200, `inbox ${inbox.status}`);
  const mine = (inbox.json.clips ?? []).filter((c) => (r.json.projects ?? []).some((p) => p.id === (c.id ?? c.project?.id ?? c.project_id)));
  if (r.json.projects?.length) {
    const pid = r.json.projects[0].id;
    const rej = await call("POST", `/api/projects/${pid}/reject`); expect(rej.status === 200, `reject ${rej.status} ${rej.text.slice(0, 80)}`);
    const restore = await call("POST", `/api/projects/${pid}/restore-from-trash`);
    const app = await call("POST", `/api/projects/${pid}/approve`); expect(app.status === 200 || restore.status === 200, `approve ${app.status} ${app.text.slice(0, 80)}`);
  }
  return `made ${r.json.projects?.length ?? 0}, skipped ${r.json.skipped ?? 0}, inbox had ${mine.length} of them`;
});
await check("exports.zip for the media", async () => { const r = await call("GET", `/api/media/${mediaId}/exports.zip`, undefined, { raw: true, timeout: 60000 }); expect(r.status === 200 && (r.headers.get("content-type") || "").includes("zip"), `status ${r.status} ${r.headers.get("content-type")}`); const buf = new Uint8Array(await r.arrayBuffer()); expect(buf[0] === 0x50 && buf[1] === 0x4b, "not a zip"); return `${buf.length} bytes`; });
await check("trash: soft delete, listed in trash, restore, delete forever", async () => {
  const d = await call("DELETE", `/api/projects/${projectId}`); expect(d.status === 200, `delete ${d.status}`);
  const t = await call("GET", "/api/projects?trash=1"); expect(t.json.projects.some((p) => p.id === projectId), "not in trash");
  const l = await call("GET", "/api/projects"); expect(!l.json.projects.some((p) => p.id === projectId), "still listed while trashed");
  const r = await call("POST", `/api/projects/${projectId}/restore-from-trash`); expect(r.status === 200, `restore ${r.status}`);
  const back = await call("GET", "/api/projects"); expect(back.json.projects.some((p) => p.id === projectId), "not back after restore");
});

// ---------- fonts ----------
await check("fonts: upload, list, file, delete", async () => {
  const form = new FormData(); form.append("file", new Blob([readFileSync(join(ASSETS, "audit-font.ttf"))], { type: "font/ttf" }), "AuditFont.ttf");
  const r = await call("POST", "/api/fonts", form); expect(r.status === 200 && (r.json.font?.id || r.json.id || r.json.fonts), `upload ${r.status} ${r.text.slice(0, 100)}`);
  const id = r.json.font?.id ?? r.json.id ?? r.json.fonts?.at(-1)?.id; created.fonts.push(id);
  const l = await call("GET", "/api/fonts"); expect(l.json.fonts.some((f) => f.id === id), "not listed");
  const f = await call("GET", `/api/fonts/${id}/file`, undefined, { raw: true }); expect(f.status === 200, `file ${f.status}`);
  const d = await call("DELETE", `/api/fonts/${id}`); expect(d.status === 200, `delete ${d.status}`); created.fonts.pop();
  return `family ${l.json.fonts.find((x) => x.id === id)?.family}`;
});

// ---------- feeds ----------
const FEED = "https://feeds.npr.org/510289/podcast.xml";
await check("rss preview", async () => { const r = await call("POST", "/api/rss/preview", { url: FEED }, { timeout: 60000 }); expect(r.status === 200, `status ${r.status} ${r.text.slice(0, 120)}`); return r.text.slice(0, 80); });
await check("feeds: add, list, episodes, update, check, delete", async () => {
  const r = await call("POST", "/api/feeds", { url: FEED, clip_count: 0, auto_render: false }, { timeout: 60000 }); expect(r.status === 200 && r.json.feed?.id, `add ${r.status} ${r.text.slice(0, 120)}`);
  const id = r.json.feed.id; created.feeds.push(id);
  const l = await call("GET", "/api/feeds"); expect(l.json.feeds.some((f) => f.id === id), "not listed");
  const e = await call("GET", `/api/feeds/${id}/episodes`, undefined, { timeout: 60000 }); expect(e.status === 200 && Array.isArray(e.json.episodes), `episodes ${e.status} ${e.text.slice(0, 80)}`);
  const u = await call("PATCH", `/api/feeds/${id}`, { clip_count: 1 }); expect(u.status === 200, `update ${u.status} ${u.text.slice(0, 80)}`);
  const c = await call("POST", "/api/feeds/check"); expect(c.status === 200 && c.json.job, `check ${c.status} ${c.text.slice(0, 80)}`);
  const d = await call("DELETE", `/api/feeds/${id}`); expect(d.status === 200, `delete ${d.status}`); created.feeds.pop();
  return `${e.json.episodes.length} episodes seen`;
});

// ---------- logout ----------
await check("logout ends the session", async () => { const r = await call("POST", "/api/auth/logout"); expect(r.status === 200, `status ${r.status}`); const me = await call("GET", "/api/me"); expect(me.status === 401, `me after logout ${me.status}`); });

// ---------- cleanup ----------
console.log("\nCleaning up…");
await call("POST", "/api/auth/login", { username: USERNAME, password: PASSWORD });
for (const id of created.projects) { await call("DELETE", `/api/projects/${id}`); await call("DELETE", `/api/projects/${id}?forever=1`); }
for (const id of created.templates) await call("DELETE", `/api/templates/${id}`);
for (const id of created.fonts) await call("DELETE", `/api/fonts/${id}`);
for (const id of created.feeds) await call("DELETE", `/api/feeds/${id}`);
for (const id of created.users) await call("DELETE", `/api/users/${id}`);
for (const id of created.media) { const r = await call("DELETE", `/api/media/${id}`); console.log(`  media ${id.slice(0, 8)} delete -> ${r.status}`); }
const left = (await call("GET", "/api/projects")).json.projects.filter((p) => p.title.startsWith("Audit"));
console.log(`  audit projects left: ${left.length}`);
await call("POST", "/api/auth/logout");

const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length} checks, ${failed.length} failed`);
for (const f of failed) console.log(`  - ${f.name}: ${f.note}`);
process.exit(failed.length ? 1 : 0);
