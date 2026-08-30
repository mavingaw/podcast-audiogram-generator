export type User = {
  id: string;
  username: string;
  is_admin: boolean;
  disabled: boolean;
  created_at: string;
};

export type MediaAsset = {
  id: string;
  original_name: string;
  content_type: string;
  size_bytes: number;
  duration_seconds: number | null;
  has_transcript: boolean;
  /** The show's artwork, when this came from a feed. */
  artwork_media_id?: string | null;
  has_waveform: boolean;
  transcript: Transcript | null;
};

export type Transcript = {
  language: string;
  duration: number;
  segments: TranscriptSegment[];
};

export type TranscriptWord = { start: number; end: number; text: string };

export type TranscriptSegment = {
  id: number;
  speaker: string;
  start: number;
  end: number;
  text: string;
  words?: TranscriptWord[];
};

/** Mirrors backend/app/services/transcription.py so the preview breaks lines
 *  exactly where the renderer will. */
const MAX_CAPTION_SECONDS = 3.5;

/** Mirrors BRAND in backend/app/services/scene.py. The renderer owns the real
 *  values; these exist so the editor previews what the export will produce. */
export const BRAND = {
  obsidian: "#0B0D11",
  surface: "#161B22",
  blue: "#89CFF0",
  blueLight: "#D4EEFC",
  gold: "#D4AF37",
  goldLight: "#F3E5AB",
  offWhite: "#F8FAFC",
} as const;

export const DEFAULT_BACKGROUND = BRAND.obsidian;
export const DEFAULT_ACCENT = BRAND.blue;

/** Mirrors caption_char_budget in backend/app/services/scene.py. The budget has
 *  to match the burned-in font size, or the preview shows two lines where the
 *  export renders four. */
const CAPTION_SIZE_RATIOS: Record<string, number> = {
  social: 0.092,
  boxed: 0.071,
  shout: 0.103,
  kinder: 0.074,
  clean: 0.053,
};

/** Mirrors join_words in backend/app/services/transcription.py. Whisper emits
 *  tokens carrying their own leading space, and hyphenated speech arrives as
 *  "day", "-to", "-day"; joining those on " " reads as "day -to -day". */
const ATTACHING = ",.!?;:%)]}'’”-";
const OPENING = "([{‘“";

function joinWords(words: TranscriptWord[]): string {
  let out = "";
  for (const word of words) {
    const token = word.text ?? "";
    if (!token) continue;
    if (!out) {
      out = token.trimStart();
      continue;
    }
    const leadingSpace = /^\s/.test(token);
    const first = token.trimStart()[0] ?? "";
    if (leadingSpace || ATTACHING.includes(first) || OPENING.includes(out.slice(-1))) {
      out += leadingSpace ? token : token.trimStart();
    } else {
      out += " " + token.trimStart();
    }
  }
  return out.trim();
}

/** Mirrors the `margin_ratio` of each preset in scene.py. Captions are drawn
 *  up from the bottom edge by this share of frame height. */
const CAPTION_MARGIN_RATIOS: Record<string, number> = {
  social: 0.30,
  boxed: 0.31,
  shout: 0.32,
  kinder: 0.31,
  clean: 0.31,
};

const RATIO_WIDTH_OVER_HEIGHT: Record<string, number> = {
  "9:16": 1080 / 1920,
  "4:5": 1080 / 1350,
  "1:1": 1,
  "16:9": 1920 / 1080,
};

/** Where the renderer will actually draw the caption block, as top and height
 *  in percent of frame height.
 *
 *  The editor used to place the caption layer wherever its stored geometry
 *  said — 88% of the frame by default — while the renderer ignored that and
 *  positioned captions from the preset's margin instead. The preview showed
 *  captions buried in the platform-UI guide the editor itself draws, and the
 *  export put them somewhere else entirely.
 */
export function captionBand(
  preset: string,
  ratio: string,
  lines = 2,
): { top: number; height: number } {
  const margin = CAPTION_MARGIN_RATIOS[preset] ?? CAPTION_MARGIN_RATIOS.social;
  const sizeRatio = CAPTION_SIZE_RATIOS[preset] ?? CAPTION_SIZE_RATIOS.social;
  const widthOverHeight = RATIO_WIDTH_OVER_HEIGHT[ratio] ?? RATIO_WIDTH_OVER_HEIGHT["9:16"];
  // Font size is a share of width; converting to a share of height is what
  // makes a caption block look so much taller in landscape.
  const blockHeight = lines * sizeRatio * widthOverHeight * 100;
  const bottom = (1 - margin) * 100;
  return { top: Math.max(0, bottom - blockHeight), height: blockHeight };
}

export function captionCharBudget(preset: string): number {
  const ratio = CAPTION_SIZE_RATIOS[preset] ?? CAPTION_SIZE_RATIOS.social;
  return Math.max(12, Math.floor((1 - 2 * 0.08) / (ratio * 0.5)));
}

export type CaptionLine = { start: number; end: number; text: string };

export function captionLines(
  transcript: Transcript | null,
  start: number,
  end: number,
  maxChars = 18,
): CaptionLine[] {
  if (!transcript) return [];
  const lines: CaptionLine[] = [];

  for (const segment of transcript.segments) {
    if (segment.end <= start || segment.start >= end) continue;
    const words = (segment.words ?? []).filter(
      (word) => word.end > start && word.start < end,
    );
    if (!words.length) {
      const text = segment.text.trim();
      if (text) {
        lines.push({
          start: Math.max(0, segment.start - start),
          end: Math.max(0.3, Math.min(segment.end, end) - start),
          text,
        });
      }
      continue;
    }
    for (const group of balancedGroups(words, maxChars)) {
      lines.push({
        start: Math.max(0, group[0].start - start),
        end: Math.max(0.3, Math.min(group[group.length - 1].end, end) - start),
        text: joinWords(group),
      });
    }
  }
  return lines.filter((line) => line.text);
}

function balancedGroups(words: TranscriptWord[], budget: number): TranscriptWord[][] {
  if (!words.length) return [];
  const length = joinWords(words).length;
  const span = words[words.length - 1].end - words[0].start;
  const count = Math.max(
    1,
    Math.ceil(length / budget),
    span > MAX_CAPTION_SECONDS ? Math.ceil(span / MAX_CAPTION_SECONDS) : 1,
  );
  const target = length / count;

  const groups: TranscriptWord[][] = [];
  let current: TranscriptWord[] = [];
  for (const word of words) {
    const candidate = [...current, word];
    // A line must never begin with a token that attaches to the previous one.
    const attaches = ATTACHING.includes((word.text ?? "").trimStart()[0] ?? "");
    if (current.length && !attaches && groups.length < count - 1) {
      const withWord = joinWords(candidate).length;
      const without = joinWords(current).length;
      if (Math.abs(withWord - target) > Math.abs(without - target)) {
        groups.push(current);
        current = [word];
        continue;
      }
    }
    current = candidate;
  }
  if (current.length) groups.push(current);
  return groups;
}

export type Project = {
  id: string;
  media_id: string | null;
  title: string;
  clip_start: number;
  clip_end: number;
  aspect_ratio: "9:16" | "1:1" | "4:5" | "16:9";
  scene: Record<string, unknown>;
  source?: string;
  review_state?: string;
};

/** A saved design, reusable across episodes. Carries no episode media. */
export type SavedTemplate = {
  id: string;
  name: string;
  aspect_ratio: Project["aspect_ratio"];
  scene: Record<string, unknown>;
  created_at: string;
};

/** A clip the finder thinks is worth posting, with its reasoning. */
export type SuggestedClip = {
  start: number;
  end: number;
  duration: number;
  text: string;
  score: number;
  reasons: string[];
  warnings: string[];
  /** Always words from the clip itself — the model may select a line, never write one. */
  title: string;
  /** Present when a local model read this clip. */
  llm?: {
    hook: number;
    standalone: number;
    interest: number;
    reason: string;
    headline: string;
  };
};

/** Whether a clip meets one platform's requirements. */
export type Destination = {
  platform: string;
  label: string;
  ok: boolean;
  blocking: string[];
  warnings: string[];
};

export type PlatformSpec = {
  key: string;
  label: string;
  ratios: string[];
  preferred_ratio: string;
  min_seconds: number;
  max_seconds: number;
  max_bytes: number;
  containers: string[];
  video_codecs: string[];
  audio_codecs: string[];
  max_video_bitrate: number;
  frame_rates: number[];
  safe_area: string | null;
  checked: string;
  notes: string;
};

export type InboxClip = Project & {
  episode: string;
  rendered: boolean;
};

export type Feed = {
  id: string;
  url: string;
  title: string;
  active: boolean;
  clip_count: number;
  aspect_ratio: string;
  template_id: string | null;
  auto_render: boolean;
  last_checked: string | null;
  last_error: string | null;
  episodes: number;
};

export type FeedEpisode = {
  id: string;
  title: string;
  published: string | null;
  status: string;
  media_id: string | null;
  error: string | null;
};

export type Speaker = {
  id: number;
  name: string;
  colour: string;
  segments: number;
};

export type Job = {
  id: string;
  kind: string;
  status: "queued" | "running" | "complete" | "failed" | "canceled";
  progress: number;
  subject_id: string | null;
  message: string;
  error: string | null;
  created_at: string;
  updated_at: string;
  result: {
    downloads?: Record<string, string>;
    files?: Record<string, string>;
  } | null;
};

export type Sound = {
  id: string;
  kind: "music" | "sfx";
  pack: string;
  title: string;
  author: string;
  attribution: string;
  license: string;
  genre: string;
  tags: string[];
  duration_seconds: number | null;
  seamless_loop: boolean;
  size_bytes: number;
  preview_url: string;
};

export type SoundPack = {
  slug: string;
  name: string;
  kind: "music" | "sfx";
  author: string;
  license: string;
  attribution: string;
  redistributable: boolean;
  notes: string;
  installed: boolean;
  sound_count: number;
};

/** The music bed a project mixes under its voice track. */
export type MusicBed = {
  soundId: string;
  gainDb: number;
  duckDb: number;
  fadeInSeconds: number;
  fadeOutSeconds: number;
  startOffsetSeconds: number;
  loop: boolean;
  /** Level changes over the clip: (clip seconds, dB relative to the level). */
  automation?: { at: number; gainDb: number }[];
};

export const defaultMusicBed = (soundId: string): MusicBed => ({
  soundId,
  gainDb: -18,
  duckDb: -12,
  fadeInSeconds: 1,
  fadeOutSeconds: 2,
  startOffsetSeconds: 0,
  loop: true,
});

export type TranscriptionSettings = {
  model: string;
  language: string;
  enabled: boolean;
  installed: boolean;
  device: string;
  compute_type: string;
  models: string[];
  encoder: {
    selected: string;
    ffmpeg_encoder: string;
    hardware: boolean;
    nvenc_available: boolean;
    override: string;
  };
};

export type RatioPreset = {
  ratio: string;
  label: string;
  for: string;
  platform: string;
  dimensions: [number, number];
};

export type Gpu = {
  index: string;
  uuid: string;
  name: string;
  memory: string;
  driver: string;
};

/**
 * The largest body worth sending as a single request.
 *
 * Cloudflare's free plan hard-refuses bodies over 100 MB, and that refusal
 * happens at their edge, so nothing in this application can catch it or report
 * it. 64 MB leaves room for the multipart wrapper and for the limit to be
 * lower than documented on some path we do not control.
 */
const SINGLE_REQUEST_LIMIT = 64 * 1024 * 1024;

/**
 * Send one slice of a chunked upload, retrying a failure that may be transient.
 *
 * A single dropped chunk on a phone would otherwise throw away everything
 * uploaded so far, which for an hour-long episode is the difference between a
 * hiccup and starting again.
 */
async function sendChunk(
  uploadId: string,
  index: number,
  slice: Blob,
  attempts = 3,
): Promise<void> {
  let lastError: unknown;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      const response = await fetch(
        `/api/media/upload/${uploadId}/chunk/${index}`,
        {
          method: "PUT",
          credentials: "include",
          headers: { "Content-Type": "application/octet-stream" },
          body: slice,
        },
      );
      if (response.ok) return;
      const payload = await response
        .json()
        .catch(() => ({ detail: response.statusText }));
      const message = errorMessage(payload.detail) || response.statusText;
      // A rejected chunk is a rejected upload: out of order, expired, or over
      // the size it declared. Retrying sends the same bytes to the same answer.
      if (response.status !== 500 && response.status !== 502) {
        throw new Error(message);
      }
      lastError = new Error(message);
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, 500 * (attempt + 1)));
  }
  throw lastError instanceof Error
    ? lastError
    : new Error("The upload was interrupted.");
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    credentials: "include",
    headers:
      init?.body instanceof FormData
        ? undefined
        : { "Content-Type": "application/json" },
    ...init,
  });
  if (!response.ok) {
    const payload = await response
      .json()
      .catch(() => ({ detail: response.statusText }));
    throw new Error(errorMessage(payload.detail) || response.statusText);
  }
  return response.json() as Promise<T>;
}

/**
 * Turn a FastAPI `detail` into something a person can read.
 *
 * Handlers raise a plain string, but request-validation failures return a list
 * of `{loc, msg, type}` objects. Interpolating that straight into the UI
 * produced a literal "[object Object]" where the reason should have been.
 */
export function errorMessage(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const parts = detail
      .map((entry) => {
        if (typeof entry === "string") return entry;
        if (entry && typeof entry === "object") {
          const item = entry as { loc?: unknown[]; msg?: string };
          // Drop the "body" prefix pydantic puts on every location.
          const field = Array.isArray(item.loc)
            ? item.loc.filter((part) => part !== "body").join(".")
            : "";
          return field && item.msg ? `${field}: ${item.msg}` : (item.msg ?? "");
        }
        return "";
      })
      .filter(Boolean);
    if (parts.length) return parts.join("; ");
  }
  if (detail && typeof detail === "object") {
    const message = (detail as { msg?: string; message?: string });
    if (message.msg) return message.msg;
    if (message.message) return message.message;
  }
  return "";
}

export const api = {
  bootstrapState: () => request<{ initialized: boolean }>("/api/bootstrap"),
  bootstrap: (username: string, password: string) =>
    request<{ user: User }>("/api/bootstrap", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  signupState: () =>
    request<{ open: boolean; code_required: boolean }>("/api/auth/signup"),
  register: (username: string, password: string, code?: string) =>
    request<{ user: User }>("/api/auth/register", {
      method: "POST",
      body: JSON.stringify({ username, password, code: code || undefined }),
    }),
  /** Remove an account and everything it owns; administrators only. */
  deleteUser: (userId: string) =>
    request<{ ok: boolean; removed_projects: number }>(`/api/users/${userId}`, { method: "DELETE" }),
  /** The sign-up code and a link carrying it; administrators only. */
  inviteLink: () => request<{ code: string | null; link: string | null }>("/api/settings/invite"),
  setSignups: (open: boolean) =>
    request<{ open: boolean }>("/api/settings/signups", {
      method: "PUT",
      body: JSON.stringify({ open }),
    }),
  me: () => request<{ user: User }>("/api/me"),
  session: () => request<{ user: User | null }>("/api/session"),
  showArtwork: () => request<{ media_id: string | null }>("/api/settings/artwork"),
  setShowArtwork: (mediaId: string | null) =>
    request<{ media_id: string | null; applied_to: number }>("/api/settings/artwork", {
      method: "PUT",
      body: JSON.stringify({ media_id: mediaId }),
    }),
  facts: (n = 8) => request<{ facts: string[] }>(`/api/facts?n=${n}`),
  login: (username: string, password: string) =>
    request<{ user: User }>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  users: () => request<{ users: User[] }>("/api/users"),
  createUser: (payload: {
    username: string;
    password: string;
    is_admin: boolean;
  }) =>
    request<{ user: User }>("/api/users", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateUser: (
    userId: string,
    payload: { is_admin?: boolean; disabled?: boolean; password?: string },
  ) =>
    request<{ user: User }>(`/api/users/${userId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  gpus: () => request<{ gpus: Gpu[] }>("/api/gpus"),
  transcriptionSettings: () =>
    request<TranscriptionSettings>("/api/settings/transcription"),
  saveTranscriptionSettings: (payload: {
    model?: string;
    language?: string;
    enabled?: boolean;
  }) =>
    request<TranscriptionSettings>("/api/settings/transcription", {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  gpuSettings: () => request<Record<string, string>>("/api/settings/gpu"),
  saveGpuSettings: (payload: {
    transcription_gpu_uuid?: string;
    encoding_gpu_uuid?: string;
  }) =>
    request<{ ok: boolean }>("/api/settings/gpu", {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  media: () => request<{ media: MediaAsset[] }>("/api/media"),
  mediaFileUrl: (mediaId: string) => `/api/media/${mediaId}/file`,
  inbox: () => request<{ count: number; clips: InboxClip[] }>("/api/inbox"),
  approveClip: (projectId: string) =>
    request<{ project: Project; job: Job | null }>(
      `/api/projects/${projectId}/approve`,
      { method: "POST" },
    ),
  rejectClip: (projectId: string) =>
    request<{ ok: boolean }>(`/api/projects/${projectId}/reject`, { method: "POST" }),
  feeds: () => request<{ feeds: Feed[] }>("/api/feeds"),
  addFeed: (url: string, options: Partial<Feed> = {}) =>
    request<{ feed: Feed }>("/api/feeds", {
      method: "POST",
      body: JSON.stringify({ url, ...options }),
    }),
  updateFeed: (feedId: string, updates: Partial<Feed>) =>
    request<{ feed: Feed }>(`/api/feeds/${feedId}`, {
      method: "PATCH",
      body: JSON.stringify(updates),
    }),
  deleteFeed: (feedId: string) =>
    request<{ ok: boolean }>(`/api/feeds/${feedId}`, { method: "DELETE" }),
  /** Queue the next `count` newest episodes this feed has not imported. */
  importOlder: (feedId: string, count: number) =>
    request<{ queued: { id: string; title: string }[]; remaining: number }>(
      `/api/feeds/${feedId}/import`,
      { method: "POST", body: JSON.stringify({ count }) },
    ),
  checkFeeds: () => request<{ job: Job }>("/api/feeds/check", { method: "POST" }),
  feedEpisodes: (feedId: string) =>
    request<{ episodes: FeedEpisode[] }>(`/api/feeds/${feedId}/episodes`),
  batchClips: (
    mediaId: string,
    options: {
      count: number;
      aspect_ratio?: string;
      render?: boolean;
      template_id?: string | null;
      look?: Record<string, unknown> | null;
    },
  ) =>
    request<{ projects: Project[]; jobs: Job[]; skipped: number }>(
      `/api/media/${mediaId}/batch`,
      { method: "POST", body: JSON.stringify(options) },
    ),
  batchZipUrl: (mediaId: string) => `/api/media/${mediaId}/exports.zip`,
  speakers: (mediaId: string) =>
    request<{ speakers: Speaker[]; multi: boolean; detection: { ready: boolean } }>(
      `/api/media/${mediaId}/speakers`,
    ),
  detectSpeakers: (mediaId: string, speakerCount: number | null) =>
    request<{ speaker_count: number; speakers: Speaker[] }>(
      `/api/media/${mediaId}/speakers/detect`,
      { method: "POST", body: JSON.stringify({ speaker_count: speakerCount }) },
    ),
  renameSpeaker: (mediaId: string, speakerId: number, name: string) =>
    request<{ speakers: Speaker[] }>(
      `/api/media/${mediaId}/speakers/${speakerId}/name`,
      { method: "POST", body: JSON.stringify({ name }) },
    ),
  assignSpeaker: (mediaId: string, start: number, end: number, speakerId: number) =>
    request<{ changed: number; speakers: Speaker[] }>(
      `/api/media/${mediaId}/speakers/assign`,
      { method: "POST", body: JSON.stringify({ start, end, speaker_id: speakerId }) },
    ),
  snapClip: (mediaId: string, start: number, end: number) =>
    request<{
      start: number;
      end: number;
      moved: boolean;
      moved_start: number;
      moved_end: number;
    }>(`/api/media/${mediaId}/snap`, {
      method: "POST",
      body: JSON.stringify({ start, end }),
    }),
  suggestedClips: (mediaId: string, limit = 6) =>
    request<{ ready: boolean; clips: SuggestedClip[]; reason?: string }>(
      `/api/media/${mediaId}/clips?limit=${limit}`,
    ),
  mediaPeaks: (
    mediaId: string,
    buckets: number,
    start?: number,
    end?: number,
  ) => {
    const query = new URLSearchParams({ buckets: String(Math.round(buckets)) });
    if (start !== undefined) query.set("start", start.toFixed(3));
    if (end !== undefined) query.set("end", end.toFixed(3));
    return request<{ ready: boolean; duration: number | null; peaks: number[] }>(
      `/api/media/${mediaId}/peaks?${query}`,
    );
  },
  /**
   * Upload a file, in pieces when it is large.
   *
   * Cloudflare's free plan refuses any request body over 100 MB at its own
   * edge, so from outside the LAN a normal podcast episode never reached the
   * server at all: no log line, no error from us, just a 413 from a machine in
   * another country. Sending the file as several smaller requests goes under
   * that limit, and gives real progress while it does.
   *
   * Small files still go in one request. Three round trips to move four
   * megabytes is worse, and this path is exercised constantly by artwork.
   */
  uploadMedia: async (
    file: File,
    onProgress?: (fraction: number) => void,
  ): Promise<{ media: MediaAsset; jobs: Job[] }> => {
    if (file.size <= SINGLE_REQUEST_LIMIT) {
      const form = new FormData();
      form.append("file", file);
      onProgress?.(0);
      const result = await request<{ media: MediaAsset; jobs: Job[] }>(
        "/api/media/upload",
        { method: "POST", body: form },
      );
      onProgress?.(1);
      return result;
    }

    const begun = await request<{ upload_id: string; chunk_bytes: number }>(
      "/api/media/upload/begin",
      {
        method: "POST",
        body: JSON.stringify({
          filename: file.name,
          content_type: file.type,
          total_bytes: file.size,
        }),
      },
    );

    const size = begun.chunk_bytes;
    const count = Math.ceil(file.size / size);
    try {
      for (let index = 0; index < count; index += 1) {
        const slice = file.slice(index * size, Math.min(file.size, (index + 1) * size));
        await sendChunk(begun.upload_id, index, slice);
        onProgress?.((index + 1) / count);
      }
    } catch (error) {
      // Take the partial file off the server's disk rather than leaving it for
      // the sweeper: the user is standing right there and may retry at once.
      await fetch(`/api/media/upload/${begun.upload_id}`, {
        method: "DELETE",
        credentials: "include",
      }).catch(() => undefined);
      throw error;
    }

    return request<{ media: MediaAsset; jobs: Job[] }>(
      `/api/media/upload/${begun.upload_id}/finish`,
      { method: "POST" },
    );
  },
  /** The library without transcripts: what the poll asks for. */
  mediaLight: () => request<{ media: MediaAsset[] }>("/api/media?transcripts=0"),
  /** One media record with its transcript. */
  mediaOne: (mediaId: string) => request<{ media: MediaAsset }>(`/api/media/${mediaId}`),
  transcribeMedia: (mediaId: string) =>
    request<{ job: Job }>(`/api/media/${mediaId}/transcribe`, { method: "POST" }),
  deleteMedia: (mediaId: string) =>
    request<{ ok: boolean; projects_affected: number }>(`/api/media/${mediaId}`, {
      method: "DELETE",
    }),
  updateTranscript: (mediaId: string, transcript: Transcript) =>
    request<{ media: MediaAsset }>(`/api/media/${mediaId}/transcript`, {
      method: "PATCH",
      body: JSON.stringify({ transcript }),
    }),
  projects: () => request<{ projects: Project[] }>("/api/projects"),
  createProject: (title: string, media_id?: string) =>
    request<{ project: Project }>("/api/projects", {
      method: "POST",
      body: JSON.stringify({ title, media_id }),
    }),
  /** The clip's own audio for the Studio player: cut server-side, cached,
   *  under a megabyte. Keyed by the range so moving an edge refetches. */
  projectPreviewUrl: (projectId: string, start: number, end: number) =>
    `/api/projects/${projectId}/preview.m4a?r=${start.toFixed(3)}-${end.toFixed(3)}`,
  /** Keep a recording made in Studio, without the episode's analysis jobs. */
  saveVoiceover: (projectId: string, blob: Blob) => {
    const form = new FormData();
    const ext = blob.type.includes("mp4") ? "m4a" : blob.type.includes("ogg") ? "ogg" : "webm";
    form.append("file", blob, `voiceover.${ext}`);
    return request<{ media: MediaAsset }>(`/api/projects/${projectId}/voiceover`, {
      method: "POST",
      body: form,
    });
  },
  updateProject: (project: Project, updates: Partial<Project>) =>
    request<{ project: Project }>(`/api/projects/${project.id}`, {
      method: "PATCH",
      body: JSON.stringify(updates),
    }),
  revisions: (projectId: string) =>
    request<{ revisions: { id: string; label: string; created_at: string }[] }>(
      `/api/projects/${projectId}/revisions`,
    ),
  restoreRevision: (projectId: string, revisionId: string) =>
    request<{ project: Project }>(
      `/api/projects/${projectId}/revisions/${revisionId}/restore`,
      { method: "POST" },
    ),
  deleteProject: (projectId: string) =>
    request<{ ok: boolean }>(`/api/projects/${projectId}`, { method: "DELETE" }),
  renderProject: (project: Project, force = false) =>
    request<{ job: Job; reused: boolean; reason?: string }>(
      `/api/projects/${project.id}/render${force ? "?force=true" : ""}`,
      { method: "POST" },
    ),
  jobs: () => request<{ jobs: Job[] }>("/api/jobs"),
  cancelJob: (jobId: string) =>
    request<{ job: Job }>(`/api/jobs/${jobId}/cancel`, { method: "POST" }),
  ratios: () => request<{ ratios: RatioPreset[] }>("/api/ratios"),
  createVariants: (projectId: string, ratios: string[], render = true) =>
    request<{ projects: Project[]; jobs: Job[] }>(
      `/api/projects/${projectId}/variants`,
      { method: "POST", body: JSON.stringify({ ratios, render }) },
    ),
  platforms: () => request<{ platforms: PlatformSpec[] }>("/api/platforms"),
  destinations: (projectId: string) =>
    request<{
      duration: number;
      aspect_ratio: string;
      file_bytes: number | null;
      rendered: boolean;
      destinations: Destination[];
    }>(`/api/projects/${projectId}/destinations`),
  templates: () => request<{ templates: SavedTemplate[] }>("/api/templates"),
  saveTemplate: (name: string, projectId: string) =>
    request<{ template: SavedTemplate }>("/api/templates", {
      method: "POST",
      body: JSON.stringify({ name, project_id: projectId }),
    }),
  deleteTemplate: (templateId: string) =>
    request<{ ok: boolean }>(`/api/templates/${templateId}`, { method: "DELETE" }),
  applyTemplate: (projectId: string, templateId: string) =>
    request<{ project: Project }>(
      `/api/projects/${projectId}/template/${templateId}`,
      { method: "POST" },
    ),
  sounds: (params: { kind?: string; genre?: string; search?: string; limit?: number } = {}) => {
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== "") query.set(key, String(value));
    }
    const suffix = query.toString();
    return request<{ sounds: Sound[] }>(`/api/library/sounds${suffix ? `?${suffix}` : ""}`);
  },
  soundGenres: () => request<{ genres: string[] }>("/api/library/genres"),
  soundPacks: () => request<{ packs: SoundPack[] }>("/api/library/packs"),
  sfxRoles: () =>
    request<{ roles: Record<string, string>; attribution: string }>("/api/library/sfx"),
};
