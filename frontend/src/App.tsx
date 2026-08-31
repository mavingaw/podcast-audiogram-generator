import { CSSProperties, ReactNode, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowDown,
  ArrowUp,
  AudioLines,
  ChevronRight,
  Download,
  Eye,
  EyeOff,
  FileAudio,
  Film,
  FolderOpen,
  Grid2X2,
  Layers3,
  LayoutTemplate,
  Lock,
  Loader2,
  LogIn,
  Menu,
  Minimize2,
  Move,
  Music,
  Play,
  Plus,
  ChevronLeft,
  Minus,
  RefreshCw,
  Rss,
  Inbox as InboxIcon,
  Save,
  Search,
  Settings2,
  Sparkles,
  Upload,
  Volume2,
  VolumeX,
  UserPlus,
  Users,
  Trash2,
  Unlock,
  Share2,
  Check,
  X,
  WandSparkles,
  ZoomIn,
  Image as ImageIcon,
  Link2,
  ExternalLink,
  Droplets,
  ChartColumn,
  Copy,
} from "lucide-react";
import {
  api,
  captionCharBudget,
  captionLines,
  errorMessage,
  Gpu,
  Job,
  MediaAsset,
  MusicBed,
  BRAND,
  captionBand,
  Feed,
  FeedEpisode,
  InboxClip,
  DEFAULT_ACCENT,
  DEFAULT_BACKGROUND,
  Destination,
  Project,
  SavedTemplate,
  Speaker,
  SuggestedClip,
  Transcript,
  User,
} from "./api";
import { Coach, MAKE_A_CLIP } from "./Coach";
import { ContextMenuHost, MenuButton, MenuItem, openMenu } from "./ContextMenu";
import { DesignPanel } from "./DesignPanel";
import { HelpButton, applyLargeText, readLargeText } from "./Help";
import { HistoryPanel } from "./HistoryPanel";
import { SfxCue, SfxPanel } from "./SfxPanel";
import { VoiceoverPanel } from "./VoiceoverPanel";
import { WorkingCard, plainStage } from "./Working";
import { CutRange, TranscriptCuts, cutDuration, merge as mergeCuts } from "./TranscriptCuts";
import { VariantsPanel } from "./VariantsPanel";
import { MusicPanel } from "./MusicPanel";
import { TranscriptionPanel } from "./TranscriptionSettings";
import { LiveBars, usePeaks, WaveformCanvas } from "./Waveform";
import { loadSfx, play as playSfx, setSfxEnabled, sfxEnabled } from "./sfx";

type View = "home" | "quick" | "studio" | "projects" | "templates" | "feeds" | "inbox" | "exports" | "analytics" | "trash" | "settings";
type AuthView = "loading" | "bootstrap" | "login" | "app";
type Ratio = "9:16" | "1:1" | "4:5" | "16:9";
type Layer = {
  mediaId?: string;
  radius?: number;
  id: string;
  name: string;
  type: "title" | "artwork" | "waveform" | "captions" | "background";
  /** How the layer arrives: fade or rise in over `enterSeconds`. */
  enter?: "none" | "fade" | "rise" | "drop" | "slide";
  opacity?: number;
  enterSeconds?: number;
  x: number;
  y: number;
  width: number;
  height: number;
  visible: boolean;
  locked: boolean;
  color?: string;
  text?: string;
  startTime?: number;
  endTime?: number;
  fontScale?: number;
  align?: string;
};
// Mirrors PLATFORM_SAFE_AREAS in backend/app/services/scene.py.
const SAFE_AREAS: Record<string, { label: string; bottom: number; top: number; right: number }> = {
  tiktok: { label: "TikTok", bottom: 0.22, top: 0.1, right: 0.16 },
  reels: { label: "Reels", bottom: 0.2, top: 0.1, right: 0.14 },
  shorts: { label: "Shorts", bottom: 0.16, top: 0.08, right: 0.14 },
  feed: { label: "Feed", bottom: 0.06, top: 0.06, right: 0.06 },
};

// Keep in step with WAVE_STYLES in backend/app/services/scene.py.
const destinations = [
  {
    id: "9:16" as Ratio,
    name: "Instagram Reel",
    detail: "Vertical social video",
    size: "1080 x 1920",
  },
  {
    id: "1:1" as Ratio,
    name: "Instagram Feed",
    detail: "Square episode card",
    size: "1080 x 1080",
  },
  {
    id: "4:5" as Ratio,
    name: "Instagram Portrait",
    detail: "Feed-first portrait",
    size: "1080 x 1350",
  },
  {
    id: "16:9" as Ratio,
    name: "YouTube",
    detail: "Full episode canvas",
    size: "1280 x 720",
  },
];
/**
 * Starting looks for Quick Create. Each is a whole look — colours, caption
 * style, typeface, waveform — not just two colours, and every one stays
 * editable in Studio. Kept to looks that read at thumbnail size: a feed is
 * where these are seen.
 */
/** Rearrangements of the default stack, so templates differ in shape, not
 * just colour. "classic" is the stack as-is; "bigArt" makes the artwork the
 * hero; "banner" drops the artwork for a big title; "minimal" is captions
 * and a whisper of waveform; "artBackdrop" throws the cover art across the
 * whole background, sharp, and hides the tile. */
type TemplateLayout = "classic" | "bigArt" | "banner" | "minimal" | "artBackdrop";

type StarterTemplate = {
  layout?: TemplateLayout;
  id: string;
  name: string;
  style: string;
  background: string;
  accent: string;
  captionPreset?: string;
  captionColor?: string;
  font?: string;
  captionFont?: string;
  waveStyle?: string;
  peakAccent?: string | false;
};
const templates: StarterTemplate[] = [
  { id: "kinder", name: "Kinder", style: "Obsidian / baby blue", background: BRAND.obsidian, accent: BRAND.blue,
    captionPreset: "social", font: "inter", waveStyle: "pulse" },
  { id: "frost", name: "Frost", style: "Frosted glass on light", background: "#e9eef5", accent: "#2f6fed",
    captionPreset: "frost", captionColor: "#0f1a2b", font: "manrope", waveStyle: "pulseFine", peakAccent: "#2f6fed" },
  { id: "smoke", name: "Smoke", style: "Dark glass over artwork", background: "#0b0d11", accent: "#a9b7c6",
    captionPreset: "smoke", font: "sora", waveStyle: "pulse", peakAccent: "#ffffff" },
  { id: "midnight", name: "Midnight Gold", style: "Podcast / contrast", background: BRAND.surface, accent: BRAND.gold,
    captionPreset: "boxed", font: "inter", waveStyle: "pulseChunky", peakAccent: BRAND.gold },
  { id: "paper", name: "Paper Cut", style: "Editorial / clean", background: "#f3eee5", accent: "#8d3f35",
    captionPreset: "card", captionColor: "#1a1512", font: "manrope", waveStyle: "envelope", peakAccent: "#8d3f35" },
  { id: "neon", name: "Neon", style: "Club poster", background: "#0a0612", accent: "#ff3fd1",
    captionPreset: "shout", font: "bebas", captionFont: "bebas", waveStyle: "pulseFine", peakAccent: "#3fefff" },
  { id: "ocean", name: "Ocean", style: "Cool and calm", background: "#061a2b", accent: "#3fb8ff",
    captionPreset: "outline", font: "sora", waveStyle: "pulse", peakAccent: "#ffffff" },
  { id: "ember", name: "Ember", style: "Warm and loud", background: "#14100f", accent: "#ffb454",
    captionPreset: "shout", font: "bebas", waveStyle: "pulseChunky", peakAccent: "#ff6b3d" },
  { id: "forest", name: "Forest", style: "Earthy", background: "#0e1a14", accent: "#7bd88f",
    captionPreset: "smoke", font: "manrope", waveStyle: "pulse", peakAccent: "#d4f5a3" },
  { id: "grape", name: "Grape", style: "Late night", background: "#160f24", accent: "#c792ea",
    captionPreset: "social", font: "inter", waveStyle: "pulseFine", peakAccent: "#ffd166" },
  { id: "sand", name: "Sand", style: "Warm light", background: "#f3e9d8", accent: "#c2743a",
    captionPreset: "frost", captionColor: "#2a1f14", font: "sora", waveStyle: "envelopeFine", peakAccent: "#c2743a" },
  { id: "mono", name: "Mono", style: "Black and white", background: "#000000", accent: "#ffffff",
    captionPreset: "outline", font: "inter", waveStyle: "pulse", peakAccent: false },
  // Different shapes, not just different colours.
  { id: "poster", name: "Poster", style: "Big cover, gallery light", background: "#efe7da", accent: "#403428",
    captionPreset: "card", captionColor: "#221a12", font: "sora", waveStyle: "envelope",
    peakAccent: "#403428", layout: "bigArt" },
  { id: "billboard", name: "Billboard", style: "Huge title, no art", background: "#0c0c0c", accent: "#ffd400",
    captionPreset: "shout", font: "bebas", captionFont: "bebas", waveStyle: "solid",
    peakAccent: "#ffd400", layout: "banner" },
  { id: "zen", name: "Zen", style: "Just the words", background: "#101812", accent: "#cfe3d4",
    captionPreset: "outline", font: "manrope", waveStyle: "envelopeFine", peakAccent: false,
    layout: "minimal" },
  { id: "vinyl", name: "Vinyl", style: "Cover art everywhere", background: "#0b0d11", accent: "#f5f1e8",
    captionPreset: "smoke", font: "sora", waveStyle: "pulse", peakAccent: "#f5f1e8",
    layout: "artBackdrop" },
  { id: "bubblegum", name: "Bubblegum", style: "Sweet and bright", background: "#ffd6e7", accent: "#c2185b",
    captionPreset: "pill", captionColor: "#3a0a1f", font: "manrope", waveStyle: "pulseFine",
    peakAccent: "#c2185b" },
  { id: "terminal", name: "Terminal", style: "Green on black", background: "#050805", accent: "#39ff8a",
    captionPreset: "boxed", font: "inter", waveStyle: "envelopeChunky", peakAccent: "#39ff8a" },
  { id: "headline", name: "Headline", style: "Newspaper morning", background: "#f6f2ea", accent: "#b3261e",
    captionPreset: "card", captionColor: "#171310", font: "inter", waveStyle: "envelope",
    peakAccent: "#b3261e", layout: "banner" },
  { id: "spotlight", name: "Spotlight", style: "Hero art, deep night", background: "#07070d", accent: "#8ab4ff",
    captionPreset: "social", font: "inter", waveStyle: "pulseChunky", peakAccent: "#8ab4ff",
    layout: "bigArt" },
];
/** The default stack, per shape.
 *
 * One layout cannot serve every ratio, because the two things that bound it
 * scale differently. Captions are sized from frame *width* but positioned by a
 * margin measured in frame *height*, so the band they occupy grows enormously
 * as a frame gets wider — from 44% of a 9:16 frame to 69% of a 16:9 one. And
 * the bottom fifth of a vertical frame belongs to the platform's own interface,
 * while landscape has no such chrome.
 *
 * So each shape gets a stack built around where its captions actually land:
 * artwork, then title, then the caption band, then the waveform. The numbers
 * are checked against the renderer by
 * `test_the_default_layout_never_overlaps_in_any_shape`, which is what stops
 * this drifting again — it has drifted twice.
 */
type Band = { y: number; height: number };
const LAYOUT: Record<Ratio, { artwork: Band; title: Band; waveform: Band }> = {
  "9:16": {
    artwork: { y: 12, height: 32 },
    title: { y: 46, height: 8 },
    waveform: { y: 71, height: 9 },
  },
  "4:5": {
    artwork: { y: 10, height: 29 },
    title: { y: 41, height: 8 },
    waveform: { y: 71, height: 9 },
  },
  "1:1": {
    artwork: { y: 8, height: 27 },
    title: { y: 37, height: 8 },
    // The bundle suggests sitting lower here, but a square post still has
    // platform UI along the bottom, so it keeps the vertical placement.
    waveform: { y: 71, height: 9 },
  },
  "16:9": {
    // Landscape captions are vast — they start at 31% of the frame — so the
    // artwork and title share what little is left above them.
    artwork: { y: 4, height: 14 },
    title: { y: 20, height: 8 },
    // No platform UI to avoid, so the waveform sits lower.
    waveform: { y: 80, height: 12 },
  },
};

const RATIO_SHAPE: Record<Ratio, [number, number]> = {
  "9:16": [9, 16], "4:5": [4, 5], "1:1": [1, 1], "16:9": [16, 9],
};
/** A centred box that is square in pixels, as percent width and x. */
function squareSlot(heightPercent: number, ratio: Ratio) {
  const [w, h] = RATIO_SHAPE[ratio] ?? [9, 16];
  const width = Math.min(76, Math.round(heightPercent * (h / w) * 100) / 100);
  return { width, x: Math.round(((100 - width) / 2) * 100) / 100 };
}

function defaultLayers(ratio: Ratio = "9:16"): Layer[] {
  return [
    {
      id: "background",
      name: "Background",
      type: "background",
      x: 0,
      y: 0,
      width: 100,
      height: 100,
      visible: true,
      locked: true,
      startTime: 0,
      endTime: 45,
    },
    {
      id: "artwork",
      name: "Podcast Artwork",
      type: "artwork",
      // Square in pixels, centred: podcast artwork is square by convention and
      // a 76%-wide landscape slot cropped the bottom off every logo. Mirrors
      // square_slot() in backend/app/services/scene.py.
      x: squareSlot(LAYOUT[ratio].artwork.height, ratio).x,
      y: LAYOUT[ratio].artwork.y,
      width: squareSlot(LAYOUT[ratio].artwork.height, ratio).width,
      height: LAYOUT[ratio].artwork.height,
      visible: true,
      locked: false,
      startTime: 0,
      endTime: 45,
    },
    {
      id: "waveform",
      name: "Waveform",
      type: "waveform",
      x: 12,
      // Below the caption band. At y 62 the waveform ran straight through where
      // captions are drawn, which an outlined caption half-hid and a solid
      // plate made obvious. See WAVEFORM_PLACEMENT for why this varies by shape.
      y: LAYOUT[ratio].waveform.y,
      width: 76,
      height: LAYOUT[ratio].waveform.height,
      visible: true,
      locked: false,
      startTime: 0,
      endTime: 45,
    },
    {
      id: "title",
      name: "Episode Title",
      type: "title",
      x: 12,
      // Above the caption band, which is where the renderer starts drawing.
      // This used to sit at 77%, straight through the waveform.
      y: LAYOUT[ratio].title.y,
      width: 76,
      height: LAYOUT[ratio].title.height,
      visible: true,
      locked: false,
      // The token, not placeholder text: the render fills in the episode's
      // name, and the preview resolves it below so the two agree.
      text: "{{episode}}",
      startTime: 0,
      endTime: 45,
    },
    {
      id: "captions",
      name: "Captions",
      type: "captions",
      x: 12,
      // Stored to match where the renderer will draw them. The canvas derives
      // this from the preset anyway, so the two can never disagree.
      y: captionBand("social", ratio).top,
      width: 76,
      height: captionBand("social", ratio).height,
      visible: true,
      locked: false,
      text: "Your story, in motion.",
      startTime: 0,
      endTime: 45,
    },
  ];
}
function layoutLayers(ratio: Ratio, layout: TemplateLayout | undefined): Layer[] {
  const layers = defaultLayers(ratio);
  const find = (type: string) => layers.find((l) => l.type === type);
  const artwork = find("artwork");
  const title = find("title");
  const wave = find("waveform");
  switch (layout) {
    case "bigArt": {
      // The cover is the show: nearly half the frame, title tucked under it.
      if (artwork) {
        const h = ratio === "16:9" ? 52 : 40;
        const w = Math.min(76, h * (ratio === "9:16" ? 16 / 9 : ratio === "4:5" ? 5 / 4 : ratio === "16:9" ? 9 / 16 : 1));
        Object.assign(artwork, { y: 6, height: h, width: w, x: (100 - w) / 2 });
      }
      if (title && artwork) Object.assign(title, { y: artwork.y + artwork.height + 2 });
      break;
    }
    case "banner": {
      if (artwork) artwork.visible = false;
      if (title) Object.assign(title, { y: 10, height: 14, x: 8, width: 84 });
      if (wave) Object.assign(wave, { y: 30, height: 10 });
      break;
    }
    case "minimal": {
      if (artwork) artwork.visible = false;
      if (title) title.visible = false;
      if (wave) Object.assign(wave, { y: 88, height: 5, x: 20, width: 60 });
      break;
    }
    case "artBackdrop": {
      // The art becomes the whole background (createProject wires that up);
      // the tile would double it.
      if (artwork) artwork.visible = false;
      break;
    }
  }
  return layers;
}
function getLayers(project: Project | null): Layer[] {
  const layers = project?.scene?.layers;
  return Array.isArray(layers) && layers.length
    ? (layers as Layer[])
    : defaultLayers(project?.aspect_ratio ?? "9:16");
}

export function App() {
  const [auth, setAuth] = useState<AuthView>("loading");
  const [user, setUser] = useState<User | null>(null);
  const [view, setView] = useState<View>("home");
  const [media, setMedia] = useState<MediaAsset[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [gpus, setGpus] = useState<Gpu[]>([]);
  const [users, setUsers] = useState<User[]>([]);
  const [gpuSettings, setGpuSettings] = useState<Record<string, string>>({});
  // Bumped after every save so the history list follows the edits rather than
  // going stale until the panel is remounted.
  const [historyVersion, setHistoryVersion] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const editSeq = useRef(0);
  const [navOpen, setNavOpen] = useState(false);
  const [saved, setSaved] = useState<SavedTemplate[]>([]);
  const [inboxCount, setInboxCount] = useState(0);
  const [error, setError] = useState<string | null>(null);
  // The inbox badge only helps somebody already looking at the app. The tab
  // title carries the count so a background tab shows it, and when the count
  // rises and notifications are allowed, the browser says so out loud.
  const lastInboxRef = useRef<number | null>(null);
  useEffect(() => {
    document.title = inboxCount > 0 ? `(${inboxCount}) Kinder` : "Kinder";
    const previous = lastInboxRef.current;
    lastInboxRef.current = inboxCount;
    if (previous === null || inboxCount <= previous) return;
    if (typeof Notification !== "undefined" && Notification.permission === "granted") {
      const fresh = inboxCount - previous;
      try {
        new Notification("Kinder", {
          body: `${fresh} new clip${fresh === 1 ? "" : "s"} waiting for your approval.`,
        });
      } catch {
        // Some browsers refuse constructor notifications; the title still shows it.
      }
    }
  }, [inboxCount]);
  const [soundOn, setSoundOn] = useState(sfxEnabled());
  const selected =
    projects.find((p) => p.id === selectedId) ?? projects[0] ?? null;
  const selectedMedia = selected
    ? (media.find((m) => m.id === selected.media_id) ?? null)
    : null;
  async function loadData(active = user) {
    const [m, p, j, g, s, u, t] = await Promise.all([
      api.mediaLight(),
      api.projects(),
      api.jobs(),
      api.gpus(),
      api.gpuSettings(),
      active?.is_admin ? api.users() : Promise.resolve({ users: [] }),
      api.templates(),
    ]);
    // Keep transcripts already in hand; fetch the rest one at a time, the
    // selected project's media first, then the most recent. The full list
    // was 1.5 MB on every load with two transcribed episodes.
    setMedia((current) => {
      const known = new Map(current.map((item) => [item.id, item]));
      return m.media.map((item) => ({ ...item, transcript: known.get(item.id)?.transcript ?? null }));
    });
    setProjects(p.projects);
    setJobs(j.jobs);
    setSaved(t.templates);
    const selectedMediaId = p.projects.find((project) => project.id === selectedId)?.media_id ?? null;
    const wanted = m.media
      .filter((item) => item.has_transcript)
      .sort((a, b) => (a.id === selectedMediaId ? -1 : b.id === selectedMediaId ? 1 : 0))
      .slice(0, 4);
    void (async () => {
      for (const item of wanted) {
        const detail = await api.mediaOne(item.id).catch(() => null);
        if (!detail) continue;
        setMedia((current) =>
          current.map((entry) =>
            entry.id === item.id && !entry.transcript
              ? { ...entry, transcript: detail.media.transcript ?? null }
              : entry,
          ),
        );
      }
    })();
    // Cheap, and it is what tells you a feed did something while you were away.
    api.inbox().then((result) => setInboxCount(result.count)).catch(() => undefined);
    setGpus(g.gpus);
    setGpuSettings(s);
    setUsers(u.users);
    // Interface cues come from the licensed pack the backend serves, so they
    // only exist once a library has been imported.
    api
      .sfxRoles()
      .then((payload) => loadSfx(payload.roles))
      .catch(() => undefined);
    if (!selectedId && p.projects[0]) setSelectedId(p.projects[0].id);
  }
  // The large-text choice is per browser and must hold from the very first
  // paint, before anyone has signed in.
  useEffect(() => {
    applyLargeText(readLargeText());
    // Back from Google's sign-in.
    const q = new URLSearchParams(window.location.search);
    const social = q.get("social");
    if (social) {
      const result = q.get("result");
      window.history.replaceState({}, "", window.location.pathname);
      window.setTimeout(() => {
        if (result === "connected") window.alert(`${social} is connected. Finished clips now have a Post button for it.`);
        else if (result === "denied") window.alert(`${social} was not connected — the sign-in was cancelled.`);
        else window.alert(`${social} was not connected: ` + (q.get("why") || "something went wrong"));
      }, 300);
      return;
    }
    const yt = q.get("youtube");
    if (yt) {
      window.history.replaceState({}, "", window.location.pathname);
      window.setTimeout(() => {
        if (yt === "connected") window.alert("YouTube is connected. Every finished clip now has a Post to YouTube button.");
        else if (yt === "denied") window.alert("YouTube was not connected — the sign-in was cancelled.");
        else window.alert("YouTube was not connected: " + (q.get("why") || "something went wrong"));
      }, 300);
    }
  }, []);
  const [coaching, setCoaching] = useState(false);
  useEffect(() => {
    let ignore = false;
    (async () => {
      try {
        const state = await api.bootstrapState();
        if (!state.initialized) {
          if (!ignore) setAuth("bootstrap");
          return;
        }
        // A 200 whether or not anyone is signed in: a cold load of the
        // sign-in page should not begin with an error in the console.
        const session = await api.session();
        if (ignore) return;
        if (!session.user) {
          setAuth("login");
          return;
        }
        setUser(session.user);
        setAuth("app");
        await loadData(session.user);
      } catch {
        if (!ignore) setAuth("login");
      }
    })();
    return () => {
      ignore = true;
    };
  }, []);
  // Refresh while work is in flight, then back off.
  //
  // A fixed 2.5s interval that also overlapped its own slow responses kept
  // sockets open until the browser ran out of them (ERR_NO_BUFFER_SPACE). This
  // waits for each round trip to finish, skips polling entirely while the tab
  // is hidden, and slows to a heartbeat once nothing is running.
  useEffect(() => {
    if (auth !== "app") return;
    let stopped = false;
    let timer = 0;

    const tick = async () => {
      if (stopped) return;
      if (document.visibilityState === "hidden") {
        timer = window.setTimeout(tick, 5000);
        return;
      }
      let busy = false;
      try {
        // The light list: no transcripts. With two transcribed episodes the
        // full list was 1.5 MB a poll, per open tab, for a field nothing in
        // the poll read. Transcripts already held are kept; one that has
        // just finished is fetched on its own, a few per tick.
        const [jobResult, mediaResult] = await Promise.all([api.jobs(), api.mediaLight()]);
        if (stopped) return;
        setJobs(jobResult.jobs);
        const missing: string[] = [];
        setMedia((current) => {
          const known = new Map(current.map((item) => [item.id, item]));
          return mediaResult.media.map((item) => {
            const previous = known.get(item.id);
            if (item.has_transcript && previous?.transcript) {
              return { ...item, transcript: previous.transcript };
            }
            if (item.has_transcript && missing.length < 3) missing.push(item.id);
            return { ...item, transcript: previous?.transcript ?? null };
          });
        });
        for (const id of missing) {
          const detail = await api.mediaOne(id).catch(() => null);
          if (stopped || !detail) continue;
          setMedia((current) =>
            current.map((item) => (item.id === id ? { ...item, transcript: detail.media.transcript ?? null } : item)),
          );
        }
        busy = jobResult.jobs.some((job) =>
          ["queued", "running"].includes(job.status),
        );
      } catch {
        // A failed poll is not worth surfacing; the next one may succeed.
      }
      if (!stopped) timer = window.setTimeout(tick, busy ? 2000 : 15000);
    };

    const onVisible = () => {
      if (document.visibilityState === "visible" && !stopped) {
        window.clearTimeout(timer);
        void tick();
      }
    };

    void tick();
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [auth]);
  async function authenticated(next: User) {
    setUser(next);
    setAuth("app");
    await loadData(next);
  }
  async function createProject(
    item?: MediaAsset,
    ratio: Ratio = "9:16",
    template = templates[0],
  ) {
    const result = await api.createProject(
      item ? item.original_name.replace(/\.[^.]+$/, "") : "Untitled audiogram",
      item?.id,
    );
    // A feed episode carries the show's artwork; a clip cut from it should
    // never open on a flat colour when the logo is right there. It goes in
    // both places it belongs: the background, blurred and dimmed so captions
    // stay legible, and the artwork slot, sharp.
    const artwork = item?.artwork_media_id ?? null;
    const scene = {
      ...result.project.scene,
      background: template.background,
      accent: template.accent,
      template: template.id,
      // The rest of the look. Absent fields fall back to the scene defaults.
      ...(template.captionPreset ? { captionPreset: template.captionPreset } : {}),
      ...(template.captionColor ? { captionColor: template.captionColor } : {}),
      ...(template.font ? { font: template.font } : {}),
      ...(template.captionFont ? { captionFont: template.captionFont } : {}),
      ...(template.waveStyle ? { waveStyle: template.waveStyle } : {}),
      ...(template.peakAccent !== undefined ? { peakAccent: template.peakAccent } : {}),
      layers: layoutLayers(ratio, template.layout).map((layer) =>
        layer.type === "artwork" && artwork ? { ...layer, mediaId: artwork } : layer,
      ),
      ...(artwork
        ? {
            backgroundImage:
              template.layout === "artBackdrop"
                ? { mediaId: artwork, blur: 3, dim: 0.35 }
                : { mediaId: artwork, blur: 22, dim: 0.45 },
          }
        : {}),
    };
    const saved = await api.updateProject(result.project, {
      aspect_ratio: ratio as Project["aspect_ratio"],
      scene,
    });
    setProjects([saved.project, ...projects]);
    setSelectedId(saved.project.id);
    return saved.project;
  }
  async function updateProject(updates: Partial<Project>) {
    if (!selected) return;
    const id = selected.id;
    // Optimistic, and functional: the next click must see this change, not
    // wait a round-trip for it. Clicking a word right after "Restore all"
    // used to toggle against the pre-restore cuts and silently undo itself.
    const seq = ++editSeq.current;
    setProjects((current) =>
      current.map((p) => (p.id === id ? { ...p, ...updates, scene: { ...p.scene, ...(updates.scene ?? {}) } } : p)),
    );
    const result = await api.updateProject(selected, updates);
    // Only the newest edit reconciles with the server: responses to rapid
    // edits can come back out of order, and an older response replacing the
    // whole project would silently undo the edits after it.
    if (seq !== editSeq.current) return;
    setProjects((current) =>
      current.map((p) => (p.id === result.project.id ? result.project : p)),
    );
    // The server may or may not have recorded a revision for this change —
    // it coalesces a burst into one — so the panel is told to look again
    // rather than being handed a count to trust.
    setHistoryVersion((current) => current + 1);
  }
  async function updateTranscript(mediaId: string, transcript: Transcript) {
    const result = await api.updateTranscript(mediaId, transcript);
    setMedia(media.map((m) => (m.id === mediaId ? result.media : m)));
  }
  if (auth === "loading")
    return (
      <div className="auth-screen">
        <Loader2 className="spin" size={28} />
      </div>
    );
  if (auth === "bootstrap" || auth === "login")
    return (
      <AuthScreen
        mode={auth}
        onDone={authenticated}
        error={error}
        onError={setError}
      />
    );
  return (
    <div className={`app-shell ${navOpen ? "nav-open" : ""}`}>
      <ContextMenuHost />
      <FontFaces />
      {coaching && <Coach steps={MAKE_A_CLIP} onDone={() => setCoaching(false)} />}
      <Sidebar
        inboxCount={inboxCount}
        user={user}
        view={view}
        setView={setView}
        projects={projects}
        selected={selected}
        setSelected={(p) => {
          setSelectedId(p.id);
          setView("studio");
        }}
        onNew={() => setView("quick")}
        onClose={() => setNavOpen(false)}
        onDelete={async (p) => {
          await api.deleteProject(p.id);
          if (selectedId === p.id) setSelectedId(null);
          await loadData();
        }}
      />
      <main className="app-main">
        <header className="app-header">
          <button className="mobile-menu icon-button" title="Open navigation" onClick={() => setNavOpen((value) => !value)}>
            <Menu size={18} />
          </button>
          <div>
            <span className="kicker">Kinder</span>
            <h1>
              {view === "home"
                ? "What do you want to create?"
                : view === "quick"
                  ? "Quick Create"
                  : view === "studio"
                    ? (selected?.title ?? "Studio Editor")
                    : view[0].toUpperCase() + view.slice(1)}
            </h1>
          </div>
          <div className="header-actions">
            <button
              className="primary compact mobile-new"
              onClick={() => setView("quick")}
              title="Start a new clip"
            >
              + New
            </button>
            <UserMenu
              user={user}
              onSettings={() => setView("settings")}
              onSignOut={async () => {
                await api.logout().catch(() => undefined);
                window.location.reload();
              }}
            />
            <HelpButton
              view={view}
              onStart={() => {
                // Start from Home so the first thing pointed at is the button
                // everyone starts with.
                setView("home");
                setCoaching(true);
              }}
            />
            <button
              className="icon-button"
              title={soundOn ? "Mute interface sounds" : "Unmute interface sounds"}
              onClick={() => {
                const next = !soundOn;
                setSfxEnabled(next);
                setSoundOn(next);
                if (next) playSfx("select");
              }}
            >
              {soundOn ? <Volume2 size={17} /> : <VolumeX size={17} />}
            </button>
            <button
              className="icon-button"
              title="Refresh"
              onClick={() => {
                playSfx("cursor");
                void loadData();
              }}
            >
              <RefreshCw size={17} />
            </button>
          </div>
        </header>
        {view === "home" && (
          <Home
            onCreate={() => setView("quick")}
            onShowMe={() => setCoaching(true)}
            onTemplates={() => setView("templates")}
            saved={saved}
            onStudio={() => setView("studio")}
            onFeeds={() => setView("feeds")}
            projects={projects}
            mediaCount={media.length}
            username={user?.username ?? ""}
            onOpen={(p) => {
              setSelectedId(p.id);
              setView("studio");
            }}
          />
        )}
        {view === "quick" && (
          <QuickCreate
            onRefresh={() => loadData()}
            onGoToExports={() => setView("exports")}
            onGoToSettings={() => setView("settings")}
            media={media}
            jobs={jobs}
            selectedMedia={selectedMedia}
            onUpload={async (file, onProgress) => {
              const result = await api.uploadMedia(file, onProgress);
              await loadData();
              return result.media;
            }}
            onCreate={async (r, t, source, start, end) => {
              const p = await createProject(source, r, t);
              await api.updateProject(p, { clip_start: start, clip_end: end });
              await loadData();
              setSelectedId(p.id);
              setView("studio");
            }}
          />
        )}
        {view === "studio" && (
          <Studio
            onNewClip={() => setView("quick")}
            project={selected}
            media={selectedMedia}
            allMedia={media}
            jobs={jobs}
            onUpdate={updateProject}
            onTranscriptUpdate={updateTranscript}
            onMediaAdded={(asset) => setMedia((current) => [asset, ...current])}
            onReloadProjects={() => loadData()}
            historyVersion={historyVersion}
            templates={saved}
            onSaveTemplate={async (name) => {
              if (!selected) return;
              await api.saveTemplate(name, selected.id);
              await loadData();
            }}
            onApplyTemplate={async (templateId) => {
              if (!selected) return;
              await api.applyTemplate(selected.id, templateId);
              await loadData();
            }}
            onRender={async (force = false) => {
              if (!selected) return null;
              const result = await api.renderProject(selected, force);
              await loadData();
              // Reusing a finished render is the right answer, but a button
              // that appears to do nothing reads as broken, so say so.
              return result.reused ? (result.reason ?? "Already exported.") : null;
            }}
          />
        )}
        {view === "projects" && (
          <ProjectBrowser
            projects={projects}
            onOpen={(p) => {
              setSelectedId(p.id);
              setView("studio");
            }}
            onDelete={async (p) => {
              await api.deleteProject(p.id);
              if (selectedId === p.id) setSelectedId(null);
              await loadData();
            }}
            onRename={async (p, title) => {
              await api.updateProject(p, { title });
              await loadData();
            }}
            onRefreshAll={() => loadData()}
          />
        )}
        {view === "templates" && (
          <TemplateGallery
            saved={saved}
            onUse={async (t) => {
              const p = await createProject(
                selectedMedia ?? media[0],
                "9:16",
                t,
              );
              setSelectedId(p.id);
              setView("studio");
            }}
            onUseSaved={async (t) => {
              const p = await createProject(selectedMedia ?? media[0], t.aspect_ratio);
              await api.applyTemplate(p.id, t.id);
              await loadData();
              setSelectedId(p.id);
              setView("studio");
            }}
            onDeleteSaved={async (t) => {
              await api.deleteTemplate(t.id);
              await loadData();
            }}
          />
        )}
        {view === "inbox" && (
          <ReviewInbox
            onReload={() => loadData()}
            onCount={(count) => setInboxCount(count)}
          />
        )}
        {view === "feeds" && (
          <Feeds templates={saved} jobs={jobs} onReload={() => loadData()} />
        )}
        {view === "exports" && (
          <Exports
            jobs={jobs}
            projects={projects}
            onReload={() => loadData()}
            onOpen={(p) => {
              setSelectedId(p.id);
              setView("studio");
            }}
          />
        )}
        {view === "analytics" && <AnalyticsPage />}
        {view === "trash" && <TrashPage onChanged={() => loadData()} />}
        {view === "settings" && (
          <SettingsPage
            user={user}
            media={media}
            onUpload={async (file, onProgress) => {
              const result = await api.uploadMedia(file, onProgress);
              await loadData();
              return result.media;
            }}
            onRefresh={() => loadData()}
            admin={
              user?.is_admin ? (
                <AdminStrip
                  isAdmin={Boolean(user?.is_admin)}
                  users={users}
                  gpus={gpus}
                  values={gpuSettings}
                  onSave={async (v) => {
                    await api.saveGpuSettings(v);
                    setGpuSettings(v);
                  }}
                  onReload={async () => setUsers((await api.users()).users)}
                />
              ) : null
            }
          />
        )}
      </main>
    </div>
  );
}

function Sidebar({
  inboxCount,
  user,
  view,
  setView,
  projects,
  selected,
  setSelected,
  onDelete,
  onNew,
  onClose,
}: {
  inboxCount: number;
  user: User | null;
  view: View;
  setView: (v: View) => void;
  projects: Project[];
  selected: Project | null;
  setSelected: (p: Project) => void;
  onDelete?: (p: Project) => Promise<void>;
  onNew: () => void;
  onClose: () => void;
}) {
  return (
    <aside className="sidebar">
      <div className="brand">
        <img
          className="brand-mark"
          src="/brand/kinder-logo-horizontal.svg"
          alt="Kinder"
          width={180}
          height={40}
        />
        <button className="brand-user" title="Your settings" onClick={() => { setView("settings"); onClose(); }}>
          {user?.username}
        </button>
      </div>
      <button className="new-project" onClick={() => { onNew(); onClose(); }}>
        <Plus size={17} /> New creation
      </button>
      <nav className="main-nav">
        {(
          [
            ["home", "Home", Grid2X2],
            ["projects", "Projects", FolderOpen],
            ["templates", "Templates", LayoutTemplate],
            ["feeds", "Feeds", Rss],
            ["inbox", "Inbox", InboxIcon],
            ["exports", "Exports", Download],
            ["analytics", "Analytics", ChartColumn],
            ["trash", "Trash", Trash2],
            ["settings", "Settings", Settings2],
          ] as const
        ).map(([id, label, Icon]) => (
          <button
            className={view === id ? "active" : ""}
            key={id}
            onClick={() => { setView(id); onClose(); }}
          >
            <Icon size={17} />
            {label}
            {/* Automation is only trustworthy if you can see it did something,
                without having to go and look. */}
            {id === "inbox" && inboxCount > 0 && (
              <span className="nav-badge">{inboxCount}</span>
            )}
          </button>
        ))}
      </nav>
      <div className="sidebar-label">Recent projects</div>
      <div className="recent-projects">
        {projects.slice(0, 5).map((p) => (
          <button
            className={selected?.id === p.id ? "active" : ""}
            key={p.id}
            onClick={() => { setSelected(p); onClose(); }}
            onContextMenu={(e) =>
              openMenu(e, projectMenu(p, {
                open: () => { setSelected(p); onClose(); },
                remove: onDelete,
              }), p.title)
            }
          >
            <Film size={14} />
            <span>{p.title}</span>
          </button>
        ))}
      </div>

    </aside>
  );
}
function Home({
  onCreate,
  onShowMe,
  onStudio,
  onFeeds,
  onTemplates,
  saved,
  projects,
  mediaCount,
  username,
  onOpen,
}: {
  onCreate: () => void;
  onShowMe: () => void;
  onStudio: () => void;
  onFeeds: () => void;
  onTemplates: () => void;
  saved: SavedTemplate[];
  projects: Project[];
  mediaCount: number;
  username: string;
  onOpen: (p: Project) => void;
}) {
  // A first sign-in lands on an empty workspace with six tiles and no
  // indication of which to press. Shown until there is a project or a file,
  // or until it is dismissed — remembered per account, per browser.
  const guideKey = `kinder:guide-dismissed:${username}`;
  const [guideDismissed, setGuideDismissed] = useState<boolean>(() => {
    try {
      return localStorage.getItem(guideKey) === "1";
    } catch {
      return false;
    }
  });
  const showGuide = !guideDismissed && projects.length === 0 && mediaCount === 0;
  const actions = [
    ["Manual audiograms", "Pick the moment yourself", AudioLines, onCreate, "teal"],
    ["Automatic audiograms", "Kinder picks the best moments for you", WandSparkles, onCreate, "violet"],
    ["Video podcasting", "A full episode as a video", Film, onCreate, "amber"],
    ["Transcribe, edit & grow", "Every word, editable and cuttable", Sparkles, onCreate, "pink"],
    ["Advanced editor", "The whole Studio, blank canvas", Settings2, onStudio, "purple"],
  ] as const;
  return (
    <div className="home">
      <section className="welcome">
        <h2>What shall we create today?</h2>
        <p>Everything stays on your own server, and nothing posts without you.</p>
      </section>
      {showGuide && (
        <section className="first-run">
          <div className="section-bar">
            <div>
              <span className="kicker">Getting started</span>
              <h2>Three steps to your first clip</h2>
            </div>
            <button
              className="text-button"
              onClick={() => {
                try {
                  localStorage.setItem(guideKey, "1");
                } catch {
                  // Nothing to do; the guide just shows again next time.
                }
                setGuideDismissed(true);
              }}
            >
              Hide this
            </button>
          </div>
          <ol className="first-run-steps">
            <li>
              <strong>Bring in an episode.</strong>
              <span>
                Paste your show's RSS link under Feeds and every new episode arrives on its
                own — or upload one file to try it now.
              </span>
              <div className="mini-fields">
                <button className="primary compact" onClick={onShowMe}>Show me how</button>
                <button className="ghost compact" onClick={onFeeds}>Add my feed</button>
                <button className="ghost compact" onClick={onCreate}>Upload an episode</button>
              </div>
            </li>
            <li>
              <strong>Pick the moment.</strong>
              <span>
                Once it is transcribed, search the words or take a suggested clip, then Open in
                Studio.
              </span>
            </li>
            <li>
              <strong>Make it yours and export.</strong>
              <span>
                Captions, live waveform and your show's artwork are already on it. Add a music
                bed, a title, an effect — then Export and download the MP4.
              </span>
            </li>
          </ol>
        </section>
      )}
      <div className="creation-grid five">
        {actions.map(([title, detail, Icon, action, tint]) => (
          <button className="creation-tile" data-tint={tint} key={title} onClick={action}>
            <span className="tile-icon">
              <Icon size={21} />
            </span>
            <span>
              <strong>{title}</strong>
              <small>{detail}</small>
            </span>
            <ChevronRight size={18} />
          </button>
        ))}
      </div>
      <HomeTemplates saved={saved} onCreate={onCreate} onTemplates={onTemplates} />
      <FeedNudge onFeeds={onFeeds} />
      <section className="recent-section">
        <div className="section-bar">
          <div>
            <span className="kicker">Workspace</span>
            <h2>Recent projects</h2>
          </div>
          <button className="text-button" onClick={onCreate}>
            View all <ChevronRight size={15} />
          </button>
        </div>
        {projects.length ? (
          <div className="project-cards">
            {projects.slice(0, 3).map((p) => (
              <button key={p.id} onClick={() => onOpen(p)}>
                <Poster projectId={p.id} ratio={p.aspect_ratio} icon={26} rendered={p.rendered} />
                <strong>{p.title}</strong>
                <small>{p.aspect_ratio} · Updated recently</small>
              </button>
            ))}
          </div>
        ) : (
          <div className="empty-state">
            <FileAudio size={28} />
            <strong>Your next episode starts here.</strong>
            <span>Create a project or upload source media to begin.</span>
          </div>
        )}
      </section>
    </div>
  );
}
/**
 * The one thing that makes Kinder effortless is a watched feed: episodes
 * arrive, get transcribed and cut on their own. Until one is set, Home says
 * so and takes the link right here.
 */
function FeedNudge({ onFeeds }: { onFeeds: () => void }) {
  const [feedCount, setFeedCount] = useState<number | null>(null);
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  useEffect(() => {
    api.feeds().then((r) => setFeedCount(r.feeds.length)).catch(() => setFeedCount(null));
  }, []);
  if (feedCount === null || feedCount > 0 || done) {
    return done ? (
      <section className="feed-nudge done">
        <Rss size={18} />
        <div>
          <strong>Watching {done}.</strong>
          <span className="muted"> New episodes arrive, get their words written down, and clips are suggested — nothing is posted anywhere.</span>
        </div>
        <button className="ghost compact" onClick={onFeeds}>See feeds</button>
      </section>
    ) : null;
  }
  async function watch() {
    const trimmed = url.trim();
    if (!trimmed) return;
    setBusy(true);
    setNote(null);
    try {
      const r = await api.addFeed(trimmed);
      setDone(r.feed.title || trimmed);
      playSfx("confirm");
    } catch (error) {
      setNote(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="feed-nudge">
      <Rss size={18} />
      <div className="feed-nudge-text">
        <strong>Let episodes come to you</strong>
        <span className="muted">
          Paste your podcast's RSS link and every new episode is fetched, transcribed and cut into
          suggested clips on its own. Find the link on your podcast host under “Share” or “RSS”.
        </span>
        {note && <span className="error">{note}</span>}
      </div>
      <form
        className="feed-nudge-form"
        onSubmit={(e) => {
          e.preventDefault();
          void watch();
        }}
      >
        <input
          type="url"
          inputMode="url"
          placeholder="https://example.com/feed.xml"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          disabled={busy}
          aria-label="RSS link"
        />
        <button className="primary compact" type="submit" disabled={busy || !url.trim()}>
          {busy ? "Checking…" : "Watch my feed"}
        </button>
      </form>
    </section>
  );
}

function QuickCreate({
  onRefresh,
  onGoToExports,
  onGoToSettings,
  media,
  jobs,
  selectedMedia,
  onUpload,
  onCreate,
}: {
  onRefresh: () => Promise<void>;
  onGoToExports: () => void;
  onGoToSettings: () => void;
  media: MediaAsset[];
  jobs: Job[];
  selectedMedia: MediaAsset | null;
  onUpload: (f: File, onProgress?: (fraction: number) => void) => Promise<MediaAsset>;
  onCreate: (
    r: Ratio,
    t: (typeof templates)[number],
    source: MediaAsset,
    start: number,
    end: number,
  ) => Promise<void>;
}) {
  const [step, setStep] = useState(0);
  const [ratio, setRatio] = useState<Ratio>("9:16");
  const [template, setTemplate] = useState(templates[0]);
  const [upload, setUpload] = useState<{ name: string; fraction: number; startedAt: number } | null>(
    null,
  );
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [sourceId, setSourceId] = useState(
    selectedMedia?.id ?? media[0]?.id ?? "",
  );
  const [start, setStart] = useState(0);
  const [end, setEnd] = useState(45);
  const source =
    media.find((m) => m.id === sourceId) ?? selectedMedia ?? media[0] ?? null;
  const duration = Math.max(60, source?.duration_seconds ?? 180);
  const segments = source?.transcript?.segments ?? [];
  // Images are artwork, not something to cut a clip from; they are chosen
  // from the Design panel instead. Listing them here as sources that were
  // forever "analyzing" was the most-asked question in the first week.
  const sources = media.filter((m) => !m.content_type.startsWith("image/"));
  // Newest job of each kind only: a file transcribed twice used to show
  // two identical "complete" rows, which reads like something went wrong.
  const sourceJobs = source
    ? jobs
        .filter(
          (job) =>
            job.subject_id === source.id &&
            ["analyze_media", "transcribe"].includes(job.kind),
        )
        .filter((job, index, all) => all.findIndex((other) => other.kind === job.kind) === index)
    : [];
  const activeSourceJobs = sourceJobs.filter((job) =>
    ["queued", "running"].includes(job.status),
  );
  const latestTranscriptJob = sourceJobs.find((job) => job.kind === "transcribe");
  return (
    <div className="quick-flow">
      <div className="flow-steps">
        {["Destination", "Source", "Clip", "Template", "Create"].map(
          (name, i) => (
            <button
              className={step === i ? "current" : step > i ? "done" : ""}
              key={name}
              onClick={() => setStep(i)}
            >
              <span>{i + 1}</span>
              {name}
            </button>
          ),
        )}
      </div>
      {step === 0 && (
        <div className="flow-pane">
          <PaneHeading
            n="01"
            title="Where will this live?"
            text="Start with a canvas built for the way people will watch it."
          />
          <div className="destination-grid">
            {destinations.map((d) => (
              <button
                className={ratio === d.id ? "selected" : ""}
                key={d.id}
                onClick={() => setRatio(d.id)}
              >
                <div
                  className={`destination-preview ratio-${d.id.replace(":", "-")}`}
                >
                  <span>{d.id}</span>
                </div>
                <strong>{d.name}</strong>
                <small>
                  {d.detail} · {d.size}
                </small>
              </button>
            ))}
          </div>
          <FlowNext onClick={() => setStep(1)} />
        </div>
      )}
      {step === 1 && (
        <div className="flow-pane">
          <PaneHeading
            n="02"
            title="Choose your source"
            text="Upload local media. Analysis and transcription run in the background."
          />
          <label className={`upload-drop${upload ? " busy" : ""}`}>
            <Upload size={26} />
            {upload ? (
              <WorkingCard
                title={`Uploading ${upload.name}`}
                stage={upload.fraction >= 1 ? "Nearly there — the box is checking the file…" : "Sending the file to your Kinder…"}
                fraction={upload.fraction >= 1 ? null : upload.fraction}
                startedAt={upload.startedAt}
              />
            ) : (
              <>
                <strong>Upload episode media</strong>
                <span>MP3, WAV, FLAC, M4A, MP4, MOV</span>
              </>
            )}
            <input
              type="file"
              accept="audio/*,video/*"
              disabled={upload !== null}
              onChange={async (e) => {
                const f = e.target.files?.[0];
                if (!f) return;
                // Clearing the input lets the same file be chosen again after a
                // failure; a browser fires no change event for an unchanged
                // selection, so a retry would do nothing at all.
                e.target.value = "";
                setUploadError(null);
                setUpload({ name: f.name, fraction: 0, startedAt: Date.now() });
                try {
                  const uploaded = await onUpload(f, (fraction) =>
                    setUpload((u) => ({ name: f.name, fraction, startedAt: u?.startedAt ?? Date.now() })),
                  );
                  setSourceId(uploaded.id);
                } catch (error) {
                  // Previously this threw into nothing: the upload failed, the
                  // page looked idle, and there was no way to tell whether the
                  // file had been rejected or the click had missed.
                  setUploadError(
                    error instanceof Error
                      ? error.message
                      : "The upload did not finish.",
                  );
                } finally {
                  setUpload(null);
                }
              }}
            />
          </label>
          {uploadError && <p className="form-error">{uploadError}</p>}
          {media.length > 0 && (
            <div className="source-head">
              <span className="sidebar-label">
                Your library · {media.length}
              </span>
              {media.length > 1 && (
                <button
                  className="link-button"
                  onClick={async () => {
                    // Everything except what is selected, because the usual
                    // reason to clear is a pile of old uploads sitting between
                    // you and the file you actually want.
                    const doomed = media.filter((m) => m.id !== source?.id);
                    if (
                      !window.confirm(
                        `Remove ${doomed.length} file${doomed.length === 1 ? "" : "s"} from the library? Clips you have already made are kept.`,
                      )
                    )
                      return;
                    setUploadError(null);
                    for (const item of doomed) {
                      try {
                        await api.deleteMedia(item.id);
                      } catch {
                        // One failure should not abandon the rest; whatever is
                        // left is still listed afterwards.
                      }
                    }
                    await onRefresh();
                  }}
                >
                  Clear all but this one
                </button>
              )}
            </div>
          )}
          <p className="muted small settings-pointer">
            Cover picture, intro &amp; outro and posting accounts live in{" "}
            <button className="text-button inline" onClick={onGoToSettings}>your Settings</button>.
          </p>
          {source && activeSourceJobs.length > 0 && (
            // While something is running on the chosen file the card sits
            // right under the drop zone, where the eye already is, rather
            // than below a long list.
            <JobProgressPanel
              title="Working on your episode"
              jobs={activeSourceJobs}
              fallback=""
            />
          )}
          {sources.length > 0 && (
            <div className="source-list">
              {sources.map((m) => {
                const state = sourceState(m, jobs);
                const removeSource = async () => {
                  if (
                    !window.confirm(
                      `Remove ${m.original_name} from the library? Clips you have already made from it are kept.`,
                    )
                  )
                    return;
                  try {
                    await api.deleteMedia(m.id);
                    if (source?.id === m.id) setSourceId("");
                    await onRefresh();
                  } catch (error) {
                    setUploadError(errorMessage(error));
                  }
                };
                const sourceMenu: MenuItem[] = [
                  { label: "Use this file", onSelect: () => setSourceId(m.id) },
                  {
                    label: state.kind === "failed" ? "Transcribe again" : "Transcribe again",
                    hint: state.kind === "working" ? "already running" : undefined,
                    disabled: state.kind === "working",
                    onSelect: async () => {
                      try {
                        await api.transcribeMedia(m.id);
                        await onRefresh();
                      } catch (error) {
                        setUploadError(errorMessage(error));
                      }
                    },
                  },
                  "separator",
                  { label: "Remove from library…", danger: true, onSelect: () => void removeSource() },
                ];
                return (
                <div
                  className={`source-row${source?.id === m.id ? " selected" : ""}`}
                  key={m.id}
                  onContextMenu={(e) => openMenu(e, sourceMenu, m.original_name)}
                >
                  <button onClick={() => setSourceId(m.id)}>
                    <FileAudio size={18} />
                    <span>
                      {m.original_name}
                      <small className={state.kind === "failed" ? "source-failed" : undefined}>
                        {state.text}
                      </small>
                    </span>
                    {source?.id === m.id && <b>✓</b>}
                  </button>
                  {state.kind === "failed" && (
                    <button
                      className="source-retry"
                      onClick={async () => {
                        setUploadError(null);
                        try {
                          await api.transcribeMedia(m.id);
                          await onRefresh();
                        } catch (error) {
                          setUploadError(errorMessage(error));
                        }
                      }}
                    >
                      Try again
                    </button>
                  )}
                  <MenuButton items={sourceMenu} title={m.original_name} />
                  <button
                    className="source-remove"
                    title={`Remove ${m.original_name}`}
                    aria-label={`Remove ${m.original_name}`}
                    // Deleting a source does not delete the clips made from
                    // it, so this is not the destructive act it looks like;
                    // it is still worth confirming, because the file itself
                    // is gone and would have to be uploaded again.
                    onClick={() => void removeSource()}
                  >
                    <Trash2 size={15} />
                  </button>
                </div>
                );
              })}
            </div>
          )}
          {source && activeSourceJobs.length === 0 && (
            <JobProgressPanel
              title="Source processing"
              jobs={activeSourceJobs.length ? activeSourceJobs : sourceJobs.slice(0, 2)}
              fallback={
                source.has_transcript
                  ? "Transcript ready"
                  : latestTranscriptJob?.status === "failed"
                    ? latestTranscriptJob.error ?? "Transcription failed"
                    : "Waiting for analysis and transcription"
              }
            />
          )}
          {source?.has_transcript && (
            <AutoClips mediaId={source.id} ratio={ratio} onRefresh={onRefresh} onGoToExports={onGoToExports} />
          )}
          {source?.has_transcript && <ShowNotes mediaId={source.id} />}
          <FlowNext disabled={!source} onClick={() => setStep(2)} />
        </div>
      )}
      {step === 2 && (
        <div className="flow-pane">
          <PaneHeading
            n="03"
            title="Find the moment"
            text="Click a transcript line or drag the range to set your clip."
          />
          <ClipSelector
            start={start}
            end={end}
            duration={duration}
            segments={segments}
            mediaId={source?.id ?? null}
            transcriptReady={Boolean(source?.has_transcript)}
            onChange={(s, e) => {
              setStart(s);
              setEnd(e);
            }}
          />
          {source && !segments.length && (
            <JobProgressPanel
              title="Transcript status"
              jobs={activeSourceJobs.length ? activeSourceJobs : sourceJobs.slice(0, 2)}
              fallback="Transcript has not finished yet. This panel updates automatically."
              onCancelled={onRefresh}
            />
          )}
          <FlowNext onClick={() => setStep(3)} />
        </div>
      )}
      {step === 3 && (
        <div className="flow-pane">
          <PaneHeading
            n="04"
            title="Choose a visual direction"
            text="Every template stays editable when you open the project in Studio."
          />
          <div className="template-grid">
            {templates.map((t) => (
              <button
                className={template.id === t.id ? "selected" : ""}
                key={t.id}
                onClick={() => setTemplate(t)}
              >
                <TemplateThumb template={t} />
                <strong>{t.name}</strong>
                <small>{t.style}</small>
              </button>
            ))}
          </div>
          <FlowNext label="Review creation" onClick={() => setStep(4)} />
        </div>
      )}
      {step === 4 && (
        <div className="flow-pane create-confirm">
          <span className="success-mark">✓</span>
          <span className="kicker">Ready to create</span>
          <h2>{source?.original_name ?? "Your audiogram"}</h2>
          <p>
            {ratio} canvas · {template.name} template ·{" "}
            {Math.max(0, end - start).toFixed(1)} second clip
          </p>
          <button
            className="primary large"
            data-coach="open-studio"
            disabled={!source}
            onClick={() =>
              source && onCreate(ratio, template, source, start, end)
            }
          >
            <WandSparkles size={18} /> Open in Studio
          </button>
        </div>
      )}
    </div>
  );
}
function PaneHeading({
  n,
  title,
  text,
}: {
  n: string;
  title: string;
  text: string;
}) {
  return (
    <div className="pane-heading">
      <span className="kicker">Step {n}</span>
      <h2>{title}</h2>
      <p>{text}</p>
    </div>
  );
}
/** A readable name for each kind of background job. */
const JOB_LABELS: Record<string, string> = {
  analyze_media: "Analyze media",
  waveform: "Waveform",
  transcribe: "Transcribe",
  render: "Render",
  model_download: "Download model",
};

const ACTIVE = ["queued", "running"];

const jobSeenAt = new Map<string, number>();
function JobProgressPanel({
  title,
  jobs,
  fallback,
  onCancelled,
}: {
  title: string;
  jobs: Job[];
  fallback: string;
  onCancelled?: () => void;
}) {
  const [cancelling, setCancelling] = useState<Set<string>>(new Set());

  async function cancel(job: Job) {
    setCancelling((current) => new Set(current).add(job.id));
    playSfx("cancel");
    try {
      await api.cancelJob(job.id);
      onCancelled?.();
    } catch {
      // The job most likely finished first, which the next poll will show.
    } finally {
      setCancelling((current) => {
        const next = new Set(current);
        next.delete(job.id);
        return next;
      });
    }
  }

  const lead = jobs.find((job) => ACTIVE.includes(job.status));
  // Timed from when this browser first saw the job, not from the server's
  // clock: the two are in different time zones and the difference showed
  // as a stopwatch stuck at 0:00.
  if (lead && !jobSeenAt.has(lead.id)) jobSeenAt.set(lead.id, Date.now());
  return (
    <div className="job-progress-panel">
      <span className="sidebar-label">{title}</span>
      {lead && (
        <WorkingCard
          title={JOB_LABELS[lead.kind] ?? lead.kind}
          stage={plainStage(lead.message, lead.kind)}
          fraction={lead.status === "running" && lead.progress > 0 ? lead.progress / 100 : null}
          startedAt={jobSeenAt.get(lead.id) ?? Date.now()}
          compact
        />
      )}
      {jobs.length ? (
        jobs.map((job) => (
          <div className={`job-progress-row ${job.status}`} key={job.id}>
            <div>
              <strong>{JOB_LABELS[job.kind] ?? job.kind}</strong>
              <small>{job.error ?? job.message}</small>
            </div>
            <span className="job-actions">
              {job.status === "running" ? `${job.progress}%` : job.status}
              {ACTIVE.includes(job.status) && (
                <button
                  className="layer-action"
                  title="Cancel this job"
                  disabled={cancelling.has(job.id)}
                  onClick={() => void cancel(job)}
                >
                  {cancelling.has(job.id) ? (
                    <Loader2 className="spin" size={12} />
                  ) : (
                    <X size={12} />
                  )}
                </button>
              )}
            </span>
            <progress value={job.progress} max="100" />
          </div>
        ))
      ) : (
        <p className="muted">{fallback}</p>
      )}
    </div>
  );
}
function FlowNext({
  onClick,
  label = "Continue",
  disabled = false,
}: {
  onClick: () => void;
  label?: string;
  disabled?: boolean;
}) {
  return (
    <div className="flow-next">
      <button className="primary" disabled={disabled} onClick={onClick}>
        {label}
        <ChevronRight size={17} />
      </button>
    </div>
  );
}
/** Clips the finder thinks are worth posting.
 *
 * Scrubbing an episode for the thirty seconds that will travel is the slow part
 * of this job, and the transcript and the peak envelope already know most of
 * what is needed to guess. Every suggestion shows why it was picked, because a
 * recommendation you cannot interrogate is one you cannot tune or trust.
 */
/** Who is in this recording.
 *
 * Detection is asked to find a specific number of people rather than guessing.
 * The guess was measured and it is not good enough: on a real single-host
 * episode it reported four speakers. The number of people in the room is the one
 * thing the person editing definitely knows, so the interface asks.
 */
function SpeakerPanel({ mediaId }: { mediaId: string | null }) {
  const [speakers, setSpeakers] = useState<Speaker[]>([]);
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => {
    if (!mediaId) return;
    let stale = false;
    api
      .speakers(mediaId)
      .then((result) => {
        if (stale) return;
        setSpeakers(result.speakers);
        setReady(result.detection.ready);
      })
      .catch(() => undefined);
    return () => {
      stale = true;
    };
  }, [mediaId]);

  if (!mediaId || !ready) return null;

  async function detect(count: number) {
    setBusy(true);
    setNote(null);
    try {
      const result = await api.detectSpeakers(mediaId!, count);
      setSpeakers(result.speakers);
      setNote(
        result.speaker_count > 1
          ? `Found ${result.speaker_count}. Captions will be tinted per speaker.`
          : "Treated as one voice.",
      );
      playSfx("confirm");
    } catch (error) {
      setNote(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="speaker-panel">
      <div className="speaker-head">
        <Users size={13} />
        <strong>How many people are talking?</strong>
        <div className="speaker-counts">
          {[1, 2, 3, 4].map((count) => (
            <button
              key={count}
              disabled={busy}
              className={speakers.length === count ? "selected" : ""}
              title={count === 1 ? "One voice — no tinting" : `${count} people`}
              onClick={() => void detect(count)}
            >
              {count}
            </button>
          ))}
        </div>
      </div>
      {speakers.length > 1 && (
        <div className="speaker-list">
          {speakers.map((speaker) => (
            <label key={speaker.id} className="speaker-row">
              <span className="speaker-dot" style={{ background: speaker.colour }} />
              <input
                defaultValue={speaker.name}
                maxLength={40}
                placeholder={`Speaker ${speaker.id}`}
                onBlur={async (event) => {
                  const name = event.target.value.trim();
                  if (name === speaker.name) return;
                  try {
                    const result = await api.renameSpeaker(mediaId!, speaker.id, name);
                    setSpeakers(result.speakers);
                  } catch {
                    // Leave the field as typed; the label simply did not save.
                  }
                }}
              />
              <small>{speaker.segments} lines</small>
            </label>
          ))}
        </div>
      )}
      {note && <p className="panel-note">{note}</p>}
      {busy && <p className="muted suggestion-note">Listening…</p>}
    </div>
  );
}

function SuggestedClips({
  mediaId,
  transcriptReady,
  onPick,
}: {
  mediaId: string | null;
  transcriptReady: boolean;
  onPick: (clip: SuggestedClip) => void;
}) {
  const [clips, setClips] = useState<SuggestedClip[] | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(true);

  useEffect(() => {
    if (!mediaId || !transcriptReady) {
      setClips(null);
      return;
    }
    let stale = false;
    setBusy(true);
    api
      .suggestedClips(mediaId, 6)
      .then((result) => {
        if (stale) return;
        setClips(result.clips);
        setNote(result.clips.length ? null : (result.reason ?? null));
      })
      .catch(() => {
        if (!stale) setNote("Could not read suggestions for this audio.");
      })
      .finally(() => !stale && setBusy(false));
    return () => {
      stale = true;
    };
  }, [mediaId, transcriptReady]);

  if (!mediaId || !transcriptReady) return null;
  if (busy) return <p className="muted suggestion-note">Finding the best moments for you…</p>;
  if (note) return <p className="muted suggestion-note">{note}</p>;
  if (!clips || !clips.length) return null;

  return (
    <div className="suggestions">
      <button className="suggestions-head" onClick={() => setOpen((v) => !v)}>
        <WandSparkles size={14} />
        <strong>{clips.length} suggested clips</strong>
        {clips.some((clip) => clip.llm) && (
          <span className="read-badge" title="A local model read these and ranked them">
            read
          </span>
        )}
        <small>{open ? "Hide" : "Show"}</small>
      </button>
      {open && (
        <div className="suggestion-list">
          {clips.map((clip) => (
            <button
              key={`${clip.start}-${clip.end}`}
              className="suggestion"
              onClick={() => {
                playSfx("select");
                onPick(clip);
              }}
            >
              <div className="suggestion-top">
                <strong>{clip.title}</strong>
                <span className="suggestion-time">
                  {formatTime(clip.start)} · {clip.duration.toFixed(0)}s
                </span>
              </div>
              <p>{clip.text.slice(0, 150)}{clip.text.length > 150 ? "…" : ""}</p>
              <div className="suggestion-why">
                {clip.llm?.reason && (
                  // The model's judgement, marked as such: it is an opinion
                  // about the clip, not a measurement of it.
                  <span className="tag read" title="The model's assessment">
                    {clip.llm.reason}
                  </span>
                )}
                {clip.reasons
                  .filter((reason) => reason !== clip.llm?.reason)
                  .slice(0, 3)
                  .map((reason) => (
                    <span key={reason} className="tag good">{reason}</span>
                  ))}
                {clip.warnings.slice(0, 2).map((warning) => (
                  <span key={warning} className="tag warn">{warning}</span>
                ))}
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function ClipSelector({
  start,
  end,
  duration,
  segments,
  mediaId,
  transcriptReady,
  onChange,
}: {
  start: number;
  end: number;
  duration: number;
  segments: { id: number; start: number; end: number; text: string }[];
  mediaId: string | null;
  transcriptReady: boolean;
  onChange: (s: number, e: number) => void;
}) {
  const [query, setQuery] = useState("");
  // What the boundaries were before the last snap, so it can be taken back.
  const [snapUndo, setSnapUndo] = useState<{ start: number; end: number } | null>(null);
  const waveformRef = useRef<HTMLDivElement>(null);

  // The visible slice of the source. A 58-minute episode drawn across 900px is
  // four seconds per pixel, which is useless for trimming an edge; zooming
  // re-fetches peaks for the window rather than stretching what we already have.
  const [view, setView] = useState<{ start: number; end: number } | null>(null);
  const viewStart = view ? Math.max(0, view.start) : 0;
  const viewEnd = view ? Math.min(duration, view.end) : duration;
  const viewSpan = Math.max(0.5, viewEnd - viewStart);
  const zoomed = viewSpan < duration - 0.01;
  const { peaks, ready } = usePeaks(
    mediaId,
    320,
    zoomed ? { start: viewStart, end: viewEnd } : undefined,
  );

  // Zooming to the selection keeps a little air either side, so the handles
  // are reachable rather than pinned to the edges.
  function zoomToSelection() {
    const pad = Math.max(0.4, (end - start) * 0.25);
    setView({ start: Math.max(0, start - pad), end: Math.min(duration, end + pad) });
    playSfx("cursor");
  }
  // Searching the transcript is how you find a hook in a 58-minute episode;
  // scrolling several hundred lines is not.
  const matches = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return segments;
    return segments.filter((segment) => segment.text.toLowerCase().includes(needle));
  }, [segments, query]);
  const dragRef = useRef<{
    mode: "new" | "move" | "start" | "end";
    origin: number;
    start: number;
    end: number;
  } | null>(null);
  /** Pointer x to a time, within whatever window is on screen. */
  const position = (clientX: number) => {
    const rect = waveformRef.current?.getBoundingClientRect();
    if (!rect) return 0;
    const fraction = (clientX - rect.left) / rect.width;
    return Math.max(viewStart, Math.min(viewEnd, viewStart + fraction * viewSpan));
  };
  /** A time to a percentage across the visible window. */
  const offset = (seconds: number) =>
    ((Math.max(viewStart, Math.min(viewEnd, seconds)) - viewStart) / viewSpan) * 100;
  const begin = (
    event: React.PointerEvent,
    mode: "new" | "move" | "start" | "end",
  ) => {
    event.preventDefault();
    const point = position(event.clientX);
    dragRef.current = { mode, origin: point, start, end };
    try { (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId); } catch { /* best-effort */ }
  };
  const move = (event: React.PointerEvent) => {
    const drag = dragRef.current;
    if (!drag) return;
    const point = position(event.clientX);
    if (drag.mode === "new")
      onChange(
        Math.min(drag.origin, point),
        Math.max(drag.origin + 0.5, point),
      );
    if (drag.mode === "start") onChange(Math.min(point, end - 0.5), end);
    if (drag.mode === "end") onChange(start, Math.max(start + 0.5, point));
    if (drag.mode === "move") {
      const delta = point - drag.origin;
      const length = drag.end - drag.start;
      const nextStart = Math.max(
        0,
        Math.min(duration - length, drag.start + delta),
      );
      onChange(nextStart, nextStart + length);
    }
  };
  // Snapping happens on release, never during the drag: moving the edge under
  // a pointer that is still down fights the person holding it.
  const finish = () => {
    const dragged = dragRef.current;
    dragRef.current = null;
    if (!dragged || !mediaId) return;
    void snapEdges();
  };

  async function snapEdges() {
    if (!mediaId) return;
    try {
      const result = await api.snapClip(mediaId, start, end);
      if (!result.moved) return;
      setSnapUndo({ start, end });
      onChange(result.start, result.end);
      playSfx("cursor");
    } catch {
      // A failed snap is not a failed edit; the clip stays where it was put.
    }
  }
  // Typing a Start beyond the current End used to snap the Start back to
  // End − 0.5, so entering 120 into a 0–45 clip produced 44.5. The intent of
  // typing a start time is to start there; the clip keeps its length and the
  // end moves with it.
  const applyStart = (value: number) => {
    const next = Math.max(0, Math.min(Number.isFinite(value) ? value : 0, duration - 0.5));
    if (next < end) {
      onChange(next, end);
      return;
    }
    const length = Math.max(0.5, end - start);
    onChange(next, Math.min(duration, next + length));
  };
  const applyEnd = (value: number) =>
    onChange(start, Math.min(duration, Math.max(start + 0.5, value)));
  const applyDuration = (value: number) =>
    onChange(start, Math.min(duration, Math.max(start + 0.5, start + value)));
  return (
    <div className="clip-selector">
      <div
        className="waveform-editor"
        ref={waveformRef}
        onPointerDown={(e) => begin(e, "new")}
        onPointerMove={move}
        onPointerUp={finish}
        onPointerCancel={finish}
      >
        <WaveformCanvas peaks={peaks} ready={ready} className="waveform-canvas" />
        {!ready && <span className="waveform-pending">Analysing audio…</span>}
        <div
          className="range-overlay"
          style={{
            left: `${offset(start)}%`,
            width: `${Math.max(0, offset(end) - offset(start))}%`,
          }}
          onPointerDown={(e) => {
            // Without this the event also reaches the container's "new"
            // handler, which overwrites the drag and turns every attempt to
            // move the region into a fresh selection.
            e.stopPropagation();
            begin(e, "move");
          }}
        >
          <button
            className="range-handle start"
            aria-label="Drag clip start"
            onPointerDown={(e) => {
              e.stopPropagation();
              begin(e, "start");
            }}
          />
          <button
            className="range-handle end"
            aria-label="Drag clip end"
            onPointerDown={(e) => {
              e.stopPropagation();
              begin(e, "end");
            }}
          />
        </div>
      </div>
      <div className="clip-times">
        <span className="zoom-controls">
          <button
            className="layer-action"
            title="Zoom to the selected clip"
            onClick={zoomToSelection}
          >
            <ZoomIn size={14} />
          </button>
          <button
            className="layer-action"
            title="Show the whole episode"
            disabled={!zoomed}
            onClick={() => {
              setView(null);
              playSfx("cursor");
            }}
          >
            <Minimize2 size={14} />
          </button>
        </span>
        <strong>{formatTime(start)}</strong>
        <span className="clip-status">
          {formatTime(end - start)} selected
          {zoomed && ` · showing ${formatTime(viewStart)}–${formatTime(viewEnd)}`}
        </span>
        <strong>{formatTime(end)}</strong>
      </div>
      <div className="clip-fields">
        <TimeField label="Start" value={start} onCommit={applyStart} />
        <TimeField label="End" value={end} min={0.5} onCommit={applyEnd} />
        <TimeField label="Duration" value={end - start} min={0.5} onCommit={applyDuration} />
      </div>
      {end - start > 180 && (
        <p className="clip-long-note">
          This clip is {formatTime(end - start)} long. Social clips work best under
          90 seconds, and Reels and TikTok will not take more than a few minutes —
          click a line of the words below to jump to a moment, then drag the
          yellow handles in.
        </p>
      )}

      {snapUndo && (
        <div className="snap-note">
          <span>Trimmed to whole words.</span>
          <button
            onClick={() => {
              onChange(snapUndo.start, snapUndo.end);
              setSnapUndo(null);
            }}
          >
            Undo
          </button>
        </div>
      )}
      <SpeakerPanel mediaId={mediaId} />
      <SuggestedClips
        mediaId={mediaId}
        transcriptReady={transcriptReady}
        onPick={(clip) => onChange(clip.start, clip.end)}
      />
      {segments.length > 0 && (
        <label className="transcript-search">
          <Search size={13} />
          <input
            placeholder={`Search ${segments.length} lines for the moment…`}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          {query && (
            <button
              className="layer-action"
              title="Clear search"
              onClick={() => setQuery("")}
            >
              <X size={13} />
            </button>
          )}
        </label>
      )}
      <div className="transcript-pick">
        {segments.length ? (
          matches.length ? (
            matches.map((s) => (
              <button
                key={s.id}
                className={s.start >= start && s.end <= end ? "in-clip" : ""}
                onClick={() => {
                  playSfx("select");
                  // Pad the selection slightly: a clip that starts exactly on
                  // the first phoneme sounds clipped.
                  onChange(Math.max(0, s.start - 0.3), Math.min(duration, s.end + 0.3));
                }}
              >
                <small>{formatTime(s.start)}</small>
                <span>{highlight(s.text, query)}</span>
              </button>
            ))
          ) : (
            <p className="muted">No line matches “{query}”.</p>
          )
        ) : (
          <p className="muted">
            {transcriptReady
              ? "No speech was found in this media. Drag across the waveform to choose a section."
              : "Transcript is being prepared. Drag across the waveform to choose a section."}
          </p>
        )}
      </div>
    </div>
  );
}

/** Put a public link to this clip on the clipboard. */
async function copyShareLink(projectId: string): Promise<string> {
  const { url } = await api.share(projectId);
  try {
    await navigator.clipboard.writeText(url);
  } catch {
    // Clipboard blocked (http, or a permissions policy): the link is still
    // shown by the button so it can be copied by hand.
  }
  return url;
}

/** "Copy link": one press, and the link is on the clipboard. */
function ShareButton({ projectId }: { projectId: string }) {
  const [state, setState] = useState<"idle" | "busy" | "copied" | "error">("idle");
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  return (
    <span className="share-button">
      <button
        className="button-link quiet"
        disabled={state === "busy"}
        title="Make a link anyone can open to watch and download this clip"
        onClick={async () => {
          setState("busy");
          try {
            const link = await copyShareLink(projectId);
            setUrl(link);
            setState("copied");
            playSfx("confirm");
            window.setTimeout(() => setState("idle"), 4000);
          } catch (e) {
            setError(errorMessage(e));
            setState("error");
          }
        }}
      >
        <Link2 size={14} /> {state === "copied" ? "Link copied" : state === "busy" ? "Making link…" : "Copy link"}
      </button>
      {state === "copied" && url && <input className="share-url" readOnly value={url} onFocus={(e) => e.currentTarget.select()} aria-label="Share link" />}
      {state === "error" && error && <small className="error">{error}</small>}
    </span>
  );
}

/**
 * Post this clip to the person's own YouTube channel. Connect once (Google's
 * sign-in), then every ready card has the button. Private by default.
 */
function YouTubePost({ projectId, defaultTitle }: { projectId: string; defaultTitle: string }) {
  const [acct, setAcct] = useState<{ configured: boolean; connected: boolean; channel: string } | null>(null);
  const [open, setOpen] = useState(false);
  const [ytTitle, setYtTitle] = useState(defaultTitle);
  const [description, setDescription] = useState("");
  const [privacy, setPrivacy] = useState("private");
  const [state, setState] = useState<"idle" | "busy" | "done" | "error">("idle");
  const [result, setResult] = useState<string | null>(null);
  useEffect(() => {
    api.youtubeAccount().then(setAcct).catch(() => setAcct(null));
  }, []);
  if (!acct || !acct.configured) return null;
  if (!acct.connected) {
    return (
      <div className="yt-post">
        <button
          className="ghost"
          onClick={async () => {
            try {
              const { url } = await api.youtubeConnect();
              window.location.href = url;
            } catch (e) {
              setResult(errorMessage(e));
            }
          }}
        >
          <Upload size={14} /> Connect YouTube to post from here
        </button>
        {result && <small className="error">{result}</small>}
      </div>
    );
  }
  if (state === "done" && result) {
    return (
      <div className="yt-post done">
        <Check size={15} /> Posted to YouTube ({privacy}) —{" "}
        <a href={result} target="_blank" rel="noreferrer">{result}</a>
      </div>
    );
  }
  return (
    <div className="yt-post">
      {!open ? (
        <button className="ghost" onClick={() => setOpen(true)}>
          <Upload size={14} /> Post to YouTube{acct.channel ? ` (${acct.channel})` : ""}
        </button>
      ) : (
        <form
          className="yt-form"
          onSubmit={async (e) => {
            e.preventDefault();
            setState("busy");
            try {
              const r = await api.postToYoutube(projectId, { title: ytTitle, description, privacy });
              setResult(r.url);
              setState("done");
              playSfx("confirm");
            } catch (err) {
              setResult(errorMessage(err));
              setState("error");
            }
          }}
        >
          <label>
            Title
            <input value={ytTitle} maxLength={100} onChange={(e) => setYtTitle(e.target.value)} />
          </label>
          <label>
            Description
            <textarea rows={2} value={description} onChange={(e) => setDescription(e.target.value)} />
          </label>
          <label>
            Who can see it
            <select value={privacy} onChange={(e) => setPrivacy(e.target.value)}>
              <option value="private">Only me (private)</option>
              <option value="unlisted">Anyone with the link (unlisted)</option>
              <option value="public">Everyone (public)</option>
            </select>
          </label>
          <div className="yt-form-actions">
            <button className="primary" type="submit" disabled={state === "busy"}>
              <Upload size={14} /> {state === "busy" ? "Uploading to YouTube…" : "Post"}
            </button>
            <button type="button" className="ghost" onClick={() => setOpen(false)}>Cancel</button>
          </div>
          {state === "error" && result && <small className="error">{result}</small>}
        </form>
      )}
    </div>
  );
}

/**
 * Post buttons for every connected platform, and Connect buttons for the
 * configured-but-not-connected ones. Shows nothing until an admin has put
 * at least one platform's keys in.
 */
function SocialPostRow({ projectId, defaultTitle }: { projectId: string; defaultTitle: string }) {
  const [accounts, setAccounts] = useState<
    { key: string; label: string; posts: string; configured: boolean; connected: boolean; name: string }[]
  >([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [notes, setNotes] = useState<Record<string, string>>({});
  useEffect(() => {
    api.socialAccounts().then((r) => setAccounts(r.accounts.filter((a) => a.key !== "youtube" && a.configured))).catch(() => undefined);
  }, []);
  if (!accounts.length) return null;
  const note = (key: string, text: string) => setNotes((n) => ({ ...n, [key]: text }));
  return (
    <div className="social-row">
      {accounts.map((a) =>
        a.connected ? (
          <button
            key={a.key}
            className="ghost compact"
            disabled={busy === a.key}
            title={`Posts ${a.posts}`}
            onClick={async () => {
              setBusy(a.key);
              try {
                const r = await api.postToSocial(projectId, a.key, { title: defaultTitle });
                note(a.key, r.detail + (r.url ? ` — ${r.url}` : ""));
                playSfx("confirm");
              } catch (e) {
                note(a.key, errorMessage(e));
              } finally {
                setBusy(null);
              }
            }}
          >
            <Share2 size={13} /> {busy === a.key ? `Posting to ${a.label}…` : `Post to ${a.label}${a.name ? ` (${a.name})` : ""}`}
          </button>
        ) : (
          <button
            key={a.key}
            className="ghost compact"
            title={a.posts}
            onClick={async () => {
              try {
                const { url } = await api.socialConnect(a.key);
                window.location.href = url;
              } catch (e) {
                note(a.key, errorMessage(e));
              }
            }}
          >
            Connect {a.label}
          </button>
        ),
      )}
      {Object.entries(notes).map(([k, v]) => (
        <small key={k} className="social-note">{v}</small>
      ))}
    </div>
  );
}

/** Admin: one row of app keys per platform. Drop the keys in and the
 * Connect buttons light up for everyone. */
function SocialAdmin() {
  const [rows, setRows] = useState<
    { key: string; label: string; note: string; posts: string; client_id: string; has_secret: boolean }[]
  >([]);
  const [drafts, setDrafts] = useState<Record<string, { id: string; secret: string }>>({});
  const [note, setNote] = useState<string | null>(null);
  useEffect(() => {
    api.socialSettings().then((r) => {
      setRows(r.providers);
      setDrafts(Object.fromEntries(r.providers.map((p) => [p.key, { id: p.client_id, secret: "" }])));
    }).catch(() => undefined);
  }, []);
  if (!rows.length) return null;
  return (
    <details className="yt-admin">
      <summary><Share2 size={14} /> Social posting · {rows.filter((r) => r.has_secret && r.client_id).length} of {rows.length} set up</summary>
      <p className="muted small">
        Each platform needs its own developer app. Make the app on the platform's developer site, set its
        redirect URL to <code>{window.location.origin}/api/social/&lt;platform&gt;/callback</code> (e.g.{" "}
        <code>/api/social/meta/callback</code>), then drop the keys in here — Connect and Post buttons appear
        for everyone as soon as a row is saved.
      </p>
      {rows.map((r) => (
        <form
          key={r.key}
          className="social-admin-row"
          onSubmit={async (e) => {
            e.preventDefault();
            const d = drafts[r.key] ?? { id: "", secret: "" };
            try {
              const saved = await api.setSocialSettings(r.key, d.id, d.secret);
              setRows((all) => all.map((row) => (row.key === r.key ? { ...row, client_id: saved.client_id, has_secret: saved.has_secret } : row)));
              setDrafts((all) => ({ ...all, [r.key]: { id: saved.client_id, secret: "" } }));
              setNote(`${r.label} saved.`);
            } catch (err) {
              setNote(errorMessage(err));
            }
          }}
        >
          <strong>{r.label}</strong>
          <small className="muted">{r.note}</small>
          <div className="social-admin-fields">
            <input
              placeholder="Client / app ID"
              value={drafts[r.key]?.id ?? ""}
              onChange={(e) => setDrafts((all) => ({ ...all, [r.key]: { id: e.target.value, secret: all[r.key]?.secret ?? "" } }))}
            />
            <input
              type="password"
              placeholder={r.has_secret ? "(saved — leave blank to keep)" : "Client secret"}
              value={drafts[r.key]?.secret ?? ""}
              onChange={(e) => setDrafts((all) => ({ ...all, [r.key]: { id: all[r.key]?.id ?? "", secret: e.target.value } }))}
            />
            <button className="primary compact" type="submit">Save</button>
          </div>
        </form>
      ))}
      {note && <p className="muted small">{note}</p>}
    </details>
  );
}

/** Admin: the Google OAuth client that lets people connect their channel. */
function YouTubeAdmin() {
  const [clientId, setClientId] = useState("");
  const [secret, setSecret] = useState("");
  const [hasSecret, setHasSecret] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [acct, setAcct] = useState<{ connected: boolean; channel: string } | null>(null);
  useEffect(() => {
    api.youtubeSettings().then((s) => { setClientId(s.client_id); setHasSecret(s.has_secret); }).catch(() => undefined);
    api.youtubeAccount().then(setAcct).catch(() => undefined);
  }, []);
  const callback = `${window.location.origin}/api/youtube/callback`;
  return (
    <details className="yt-admin">
      <summary><Upload size={14} /> YouTube posting {hasSecret && clientId ? "· set up" : "· not set up"}</summary>
      <p className="muted small">
        Lets people connect their own YouTube channel and post clips from Kinder. Needs a Google
        OAuth client: in Google Cloud Console create a project, enable the <em>YouTube Data API v3</em>,
        make an OAuth client of type <em>Web application</em>, and add this redirect URL:{" "}
        <code>{callback}</code>. Paste the client ID and secret here.
      </p>
      <form
        className="yt-admin-form"
        onSubmit={async (e) => {
          e.preventDefault();
          try {
            const s = await api.setYoutubeSettings(clientId, secret);
            setHasSecret(s.has_secret);
            setSecret("");
            setNote("Saved. Anyone can now press Connect YouTube on a finished clip.");
          } catch (err) {
            setNote(errorMessage(err));
          }
        }}
      >
        <label>
          Client ID
          <input value={clientId} onChange={(e) => setClientId(e.target.value)} placeholder="1234-abcd.apps.googleusercontent.com" />
        </label>
        <label>
          Client secret
          <input type="password" value={secret} onChange={(e) => setSecret(e.target.value)} placeholder={hasSecret ? "(saved — leave blank to keep)" : "GOCSPX-…"} />
        </label>
        <button className="primary compact" type="submit">Save</button>
        {acct?.connected && (
          <button
            type="button"
            className="ghost compact"
            onClick={async () => {
              await api.youtubeDisconnect();
              setAcct({ connected: false, channel: "" });
            }}
          >
            Disconnect my channel{acct.channel ? ` (${acct.channel})` : ""}
          </button>
        )}
      </form>
      {note && <p className="muted small">{note}</p>}
    </details>
  );
}

/** The clip's own footage behind the canvas, kept in step with playback.

 Sync is deliberately loose: the video is a backdrop, and correcting only
 when it drifts past a third of a second avoids the stutter of constant
 seeking. */
function VideoBackdrop({ src, time, playing }: { src: string; time: number; playing: boolean }) {
  const ref = useRef<HTMLVideoElement | null>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (playing) void el.play().catch(() => undefined);
    else el.pause();
  }, [playing]);
  useEffect(() => {
    const el = ref.current;
    if (!el || !Number.isFinite(time)) return;
    if (Math.abs(el.currentTime - time) > 0.35) el.currentTime = time;
  }, [time]);
  return <video ref={ref} className="canvas-backdrop video" src={src} muted playsInline preload="auto" />;
}

/** The rendered clip's own frame as the card image, when there is one. */
function Poster({
  projectId,
  ratio,
  icon,
  rendered,
  compact = false,
}: {
  projectId: string;
  ratio: string;
  icon: number;
  rendered: boolean;
  compact?: boolean;
}) {
  // The server says whether a render exists (`rendered`); nothing here ever
  // requests an image that is not there, because Chrome logs every 404 to
  // the console — even from fetch — and the smoke test watches the console.
  const [ok, setOk] = useState(rendered);
  if (compact) {
    return ok ? (
      <img className="export-poster" src={api.posterUrl(projectId)} alt="" onError={() => setOk(false)} />
    ) : (
      <div className="export-icon">
        <Film size={icon} />
      </div>
    );
  }
  return (
    <div className={`project-thumb ratio-${ratio.replace(":", "-")}${ok ? " has-poster" : ""}`}>
      {ok ? (
        <img src={api.posterUrl(projectId)} alt="" loading="lazy" onError={() => setOk(false)} />
      ) : (
        <AudioLines size={icon} />
      )}
    </div>
  );
}

/** Where this clip has already been posted from Kinder. */
function PostedBadges({ project }: { project: Project }) {
  const posted = Array.isArray(project.scene?.posted) ? (project.scene.posted as { platform: string; url: string; privacy?: string }[]) : [];
  if (!posted.length) return null;
  return (
    <div className="posted-badges">
      {posted.map((p, i) => (
        <a key={i} className="posted-badge" href={p.url} target="_blank" rel="noreferrer" title={`Posted to ${p.platform}${p.privacy ? ` (${p.privacy})` : ""}`}>
          <Check size={11} /> Posted to {p.platform === "youtube" ? "YouTube" : p.platform}{p.privacy && p.privacy !== "public" ? ` · ${p.privacy}` : ""}
        </a>
      ))}
    </div>
  );
}

/** Where the finished file can be posted, as a row of ticks and crosses. */
function ReadyDestinations({ projectId }: { projectId: string }) {
  const [rows, setRows] = useState<Destination[] | null>(null);
  useEffect(() => {
    let stale = false;
    api.destinations(projectId).then((r) => { if (!stale) setRows(r.destinations); }).catch(() => undefined);
    return () => { stale = true; };
  }, [projectId]);
  if (!rows || rows.length === 0) return null;
  const ok = rows.filter((r) => r.ok);
  return (
    <div className="ready-destinations">
      <span className="ready-destinations-label">
        {ok.length === rows.length ? "Ready to post anywhere:" : `Ready for ${ok.length} of ${rows.length} places:`}
      </span>
      <div className="ready-destinations-list">
        {rows.map((row) =>
          row.ok && row.web_upload && row.upload_url ? (
            <a
              key={row.platform}
              className="destination-chip ok link"
              href={row.upload_url}
              target="_blank"
              rel="noreferrer"
              title={`Opens ${row.label}'s upload page in a new tab — download the video first, then choose it there`}
            >
              <Check size={12} /> {row.label} <ExternalLink size={11} />
            </a>
          ) : (
            <span
              key={row.platform}
              className={`destination-chip ${row.ok ? "ok" : "blocked"}`}
              title={
                row.ok
                  ? `${row.label} only takes uploads from its phone app — open this clip on your phone and press Post to…`
                  : row.blocking[0]
              }
            >
              {row.ok ? <Check size={12} /> : <X size={12} />} {row.label}
              {row.ok && !row.web_upload && <small> — phone app</small>}
              {!row.ok && row.blocking[0] && <small> — {row.blocking[0]}</small>}
            </span>
          ),
        )}
      </div>
      <PostButton projectId={projectId} />
    </div>
  );
}

/**
 * "Post to…" — the phone's share sheet with the video in it, which lands the
 * clip straight in Instagram, TikTok, YouTube or wherever. Desktop browsers
 * cannot share files, so there the button explains and points at Download.
 */
function PostButton({ projectId }: { projectId: string }) {
  const [state, setState] = useState<"idle" | "busy" | "done" | "nope">("idle");
  const canShareFiles = typeof navigator !== "undefined" && "share" in navigator && "canShare" in navigator;
  if (!canShareFiles) {
    return (
      <p className="muted small post-hint">
        On a phone, this card has a <strong>Post to…</strong> button that sends the video straight into
        Instagram, TikTok or YouTube. Here, download the video and choose it on the platform's upload page.
      </p>
    );
  }
  return (
    <button
      className="primary post-button"
      disabled={state === "busy"}
      onClick={async () => {
        setState("busy");
        try {
          const blob = await (await fetch(`/api/projects/${projectId}/outputs/audiogram.mp4`, { credentials: "include" })).blob();
          const file = new File([blob], "kinder-clip.mp4", { type: "video/mp4" });
          if (navigator.canShare({ files: [file] })) {
            await navigator.share({ files: [file], title: "Clip from Kinder" });
            setState("done");
          } else {
            setState("nope");
          }
        } catch {
          setState("idle");
        }
      }}
    >
      <Share2 size={15} />{" "}
      {state === "busy" ? "Getting the video…" : state === "done" ? "Sent" : state === "nope" ? "This browser cannot share videos" : "Post to…"}
    </button>
  );
}

/** "Your video is ready" — shown once, the moment a render finishes. */
function ReadyCard({
  job,
  title,
  onClose,
  onAnother,
}: {
  job: Job;
  title: string;
  onClose: () => void;
  onAnother: () => void;
}) {
  const downloads = job.result?.downloads ?? {};
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [onClose]);
  return (
    <div className="ready-overlay" role="dialog" aria-modal="true" aria-label="Your video is ready" onClick={onClose}>
      <div className="ready-card" onClick={(e) => e.stopPropagation()}>
        <div className="ready-head">
          <span className="kicker">Done</span>
          <h2>Your video is ready</h2>
          <p className="muted">{title}</p>
        </div>
        {downloads.mp4 && (
          <video className="ready-video" src={downloads.mp4} poster={job.subject_id ? api.posterUrl(job.subject_id) : undefined} controls playsInline preload="metadata" />
        )}
        {job.subject_id && <ReadyDestinations projectId={job.subject_id} />}
        {job.subject_id && <YouTubePost projectId={job.subject_id} defaultTitle={title} />}
        {job.subject_id && <SocialPostRow projectId={job.subject_id} defaultTitle={title} />}
        <div className="ready-actions">
          {downloads.mp4 && (
            <a className="button-link" href={downloads.mp4} download>
              <Download size={15} /> Download video
            </a>
          )}
          {downloads.srt && (
            <a className="button-link quiet" href={downloads.srt} download>
              Captions file
            </a>
          )}
          {job.subject_id && <ShareButton projectId={job.subject_id} />}
          <button className="ghost" onClick={onAnother}>Make another clip</button>
          <button className="ghost" onClick={onClose}>Keep editing</button>
        </div>
        <p className="muted small">
          It is also saved under <strong>Exports</strong> in the menu, so you can come back for it any time.
        </p>
      </div>
    </div>
  );
}

function ExportNote({
  note,
  onForce,
}: {
  note: string | null;
  onForce: () => void;
}) {
  if (!note) return null;
  return (
    <span className="export-note">
      {note}
      <button onClick={onForce}>Export anyway</button>
    </span>
  );
}

function Studio({
  onNewClip,
  project,
  media,
  allMedia,
  jobs,
  onUpdate,
  onTranscriptUpdate,
  onRender,
  onMediaAdded,
  onReloadProjects,
  historyVersion,
  templates: saved,
  onSaveTemplate,
  onApplyTemplate,
}: {
  onNewClip?: () => void;
  project: Project | null;
  media: MediaAsset | null;
  allMedia: MediaAsset[];
  jobs: Job[];
  onUpdate: (u: Partial<Project>) => Promise<void>;
  onTranscriptUpdate: (mediaId: string, transcript: Transcript) => Promise<void>;
  onRender: (force?: boolean) => Promise<string | null>;
  onMediaAdded: (asset: MediaAsset) => void;
  onReloadProjects: () => Promise<void>;
  historyVersion: number;
  templates: SavedTemplate[];
  onSaveTemplate: (name: string) => Promise<void>;
  onApplyTemplate: (templateId: string) => Promise<void>;
}) {
  const [layers, setLayers] = useState<Layer[]>(getLayers(project));
  const [selectedLayer, setSelectedLayer] = useState("artwork");
  const [playhead, setPlayhead] = useState(project?.clip_start ?? 0);
  const [playing, setPlaying] = useState(false);
  const [zoom, setZoom] = useState(1);
  const [titleDraft, setTitleDraft] = useState(project?.title ?? "");
  // Set when an export was answered with an existing render rather than a new
  // one, so the button does not simply appear to do nothing.
  const [exportNote, setExportNote] = useState<string | null>(null);
  const [clipStart, setClipStart] = useState(project?.clip_start ?? 0);
  const [clipEnd, setClipEnd] = useState(project?.clip_end ?? 45);
  const [transcriptDraft, setTranscriptDraft] = useState<Transcript | null>(
    media?.transcript ?? null,
  );
  // A 93-minute episode is 1,100 lines, each a textarea. Rendering all of
  // them made Studio sluggish for a panel whose job is the clip in front of
  // you; the minute either side of it is what gets edited.
  const [showWholeTranscript, setShowWholeTranscript] = useState(false);
  const nearbySegments = useMemo(
    () =>
      (transcriptDraft?.segments ?? []).filter(
        (segment) => segment.end >= clipStart - 60 && segment.start <= clipEnd + 60,
      ),
    [transcriptDraft, clipStart, clipEnd],
  );
  const canvasRef = useRef<HTMLDivElement>(null);
  const textFieldRef = useRef<HTMLTextAreaElement>(null);
  const mediaRef = useRef<HTMLMediaElement | null>(null);
  const layersRef = useRef(layers);
  useEffect(() => { layersRef.current = layers; }, [layers]);
  // The freshest scene the app knows, for building whole-scene PATCHes.
  const sceneRef = useRef<Record<string, unknown>>(project?.scene ?? {});
  useEffect(() => { sceneRef.current = project?.scene ?? {}; }, [project?.scene]);
  // Layers can change from outside the canvas — applying a template, or the
  // caption-collision fix — and the canvas has to follow. The dependency is the
  // serialised layers rather than the scene object, which is a fresh reference
  // on every poll and would clobber a drag in progress.
  const storedLayers = JSON.stringify(project?.scene?.layers ?? null);
  useEffect(() => {
    setLayers(getLayers(project));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project?.id, storedLayers]);
  useEffect(() => {
    // Only a different project or a history restore may reset these: keyed
    // on the layers too, every debounced layer save wiped a half-typed
    // title and snapped the paused playhead back to the clip start.
    setTitleDraft(project?.title ?? "");
    setClipStart(project?.clip_start ?? 0);
    setClipEnd(project?.clip_end ?? 45);
    setPlayhead(project?.clip_start ?? 0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project?.id, historyVersion]);
  useEffect(() => setTranscriptDraft(media?.transcript ?? null), [media?.id, media?.transcript]);
  // The playhead follows the audio, not a timer.
  //
  // Both used to run at once: a 100ms interval added 0.1 to the playhead while
  // `onTimeUpdate` wrote the element's real position over the top. They
  // disagreed within a second or two, and captions — which are chosen by
  // playhead — drifted away from the words being spoken.
  //
  // Reading `currentTime` on an animation frame instead is exact and smooth.
  // `timeupdate` fires about four times a second, which is a quarter-second of
  // caption lag: visible, and precisely the thing this preview exists to check.
  useEffect(() => {
    if (!playing) return;
    const element = mediaRef.current;

    if (!element) {
      // No media loaded: a timer is the only clock available, and it is honest
      // about being an approximation because nothing is actually playing.
      const timer = window.setInterval(() => setPlayhead((current) => {
        if (current >= clipEnd) { setPlaying(false); return project?.clip_start ?? 0; }
        return current + 0.1;
      }), 100);
      return () => window.clearInterval(timer);
    }

    let frame = 0;
    const follow = () => {
      // The element plays the clip's own audio, so its clock starts at 0.
      const now = clipStart + element.currentTime;
      if (now >= clipEnd - 0.02 || element.ended) {
        element.pause();
        setPlaying(false);
        setPlayhead(clipEnd);
        return;
      }
      setPlayhead(now);
      frame = window.requestAnimationFrame(follow);
    };
    frame = window.requestAnimationFrame(follow);
    return () => window.cancelAnimationFrame(frame);
  }, [playing, clipEnd, project?.clip_start]);

  /**
   * Move the playhead, taking the audio with it.
   *
   * Scrubbing used to set React state only, so clicking the timeline mid-play
   * moved the caret and the canvas while the audio carried on from where it
   * was. The two then disagreed until playback stopped.
   */
  function seek(time: number) {
    const clamped = Math.max(clipStart, Math.min(clipEnd, time));
    setPlayhead(clamped);
    const element = mediaRef.current;
    if (element && Number.isFinite(clamped)) {
      // Guard against feeding the element a value it will reject before it has
      // metadata; it seeks on its own once loaded.
      try {
        element.currentTime = clamped - clipStart;
      } catch {
        // Not seekable yet. The state is set either way.
      }
    }
  }
  const active = layers.find((l) => l.id === selectedLayer) ?? layers[0];
  const musicBed = (project?.scene?.music as MusicBed | undefined) ?? null;
  const backdrop = (project?.scene?.backgroundImage ?? {}) as Record<string, unknown>;
  const backgroundImageUrl = backdrop.mediaId
    ? api.mediaFileUrl(String(backdrop.mediaId))
    : null;
  const backgroundBlur = Number(backdrop.blur ?? 18);
  const backgroundDim = Number(backdrop.dim ?? 0.35);
  const { peaks: clipPeaks } = usePeaks(media?.id ?? null, 160, {
    start: clipStart,
    end: clipEnd,
  });
  // The live-bar preview moves on its own clock while playing. Ten frames a
  // second is enough to read as a meter and cheap enough not to matter.
  const [liveTick, setLiveTick] = useState(0);
  useEffect(() => {
    if (!playing) return;
    const timer = window.setInterval(() => setLiveTick((tick) => tick + 1), 100);
    return () => window.clearInterval(timer);
  }, [playing]);
  // Put the player on the clip.
  //
  // The element's src is the whole episode — it has to be, the clip is a range
  // rather than a file — and a fresh element sits at 0:00. Opening a
  // six-minute-in clip in Studio therefore played the top of the episode, with
  // a scrubber over all forty-five minutes, which reads as the clip having been
  // lost somewhere between the two screens. It never was: the range was stored
  // correctly the whole time and nothing had moved the playhead to it.
  useEffect(() => {
    const element = mediaRef.current;
    if (!element) return;
    const seek = () => {
      // readyState 1 is HAVE_METADATA: before that a seek is discarded.
      if (element.readyState >= 1) {
        element.currentTime = 0;
      }
    };
    seek();
    element.addEventListener("loadedmetadata", seek);
    return () => element.removeEventListener("loadedmetadata", seek);
  }, [project?.id, clipStart]);
  const accent = String(project?.scene?.accent ?? DEFAULT_ACCENT);
  const background = String(project?.scene?.background ?? DEFAULT_BACKGROUND);
  // Cuts live on the scene, in source time, and the renderer removes them in a
  // pre-pass before anything else runs.
  const sceneCuts = useMemo(
    () => (project?.scene?.cuts as CutRange[] | undefined) ?? [],
    [project?.scene?.cuts],
  );
  // What the export will actually be, which is the clip minus whatever has
  // been cut out of it. The preview transport shows this rather than the raw
  // range, so the number on screen is the number you get.
  const clipDuration = Math.max(
    0.5,
    clipEnd - clipStart - cutDuration(sceneCuts),
  );
  const localPlayhead = Math.max(0, playhead - clipStart);
  // Split the transcript the same way the renderer will, then show whichever
  // line covers the playhead — so the preview reads like the export.
  const captionPreset = String(project?.scene?.captionPreset ?? "social");
  const captionOffset = Number(project?.scene?.captionOffset ?? 0);
  const previewCaptions = useMemo(
    () => {
      const lines = captionLines(
        transcriptDraft, clipStart, clipEnd, captionCharBudget(captionPreset),
      );
      // The same shift the renderer applies, so adjusting the offset can be
      // judged here rather than by exporting and watching. Mirrors
      // _shift_captions in backend/app/services/jobs.py.
      if (Math.abs(captionOffset) < 0.001) return lines;
      const duration = clipEnd - clipStart;
      return lines
        .map((line) => ({
          ...line,
          start: line.start + captionOffset,
          end: line.end + captionOffset,
        }))
        .filter((line) => line.end > 0 && line.start < duration)
        .map((line) => ({
          ...line,
          start: Math.max(0, line.start),
          end: Math.min(duration, line.end),
        }));
    },
    [transcriptDraft, clipStart, clipEnd, captionPreset, captionOffset],
  );
  // A small lead-in tolerance: the first line often starts a few hundredths
  // in, and a playhead parked at 0 should still show it rather than the
  // empty-state text.
  // Between two lines the band shows nothing — never the placeholder. The
  // placeholder is for a project with no transcript at all; on a real clip it
  // flashed up in every gap between sentences during playback.
  const activeCaption =
    previewCaptions.find(
      (line) => localPlayhead >= line.start - 0.25 && localPlayhead <= line.end,
    )?.text ?? (previewCaptions.length ? "" : null);
  const platform = String(project?.scene?.platform ?? "");
  const safeArea = SAFE_AREAS[platform] ?? null;
  // The clip's own audio, not the episode. Studio used to play the whole
  // file and seek into it: 90 MB through the tunnel before the first second
  // played, and a scrubber over ninety minutes the clip did not contain.
  const sourceUrl = project?.id && media
    ? api.projectPreviewUrl(project.id, clipStart, clipEnd)
    : "";
  const activeRender = jobs.find(
    (job) =>
      job.kind === "render" &&
      job.subject_id === project?.id &&
      ["queued", "running"].includes(job.status),
  );
  // The moment the render you are waiting for finishes, say so — with the
  // video right there — rather than leaving a spinner to quietly vanish.
  // Simple mode hides the panels a first clip does not need. Remembered per
  // browser; the default is simple, because the person who needs
  // "Everything" knows to look for it.
  const [simple, setSimpleState] = useState<boolean>(() => {
    try {
      return localStorage.getItem("kinder.studioSimple") !== "0";
    } catch {
      return true;
    }
  });
  const setSimple = (on: boolean) => {
    setSimpleState(on);
    try {
      localStorage.setItem("kinder.studioSimple", on ? "1" : "0");
    } catch {
      // Fine: the choice lasts for this visit.
    }
  };
  // Keyboard: Space plays, Delete removes the selected layer, arrows nudge
  // it (Shift for bigger steps), Ctrl/Cmd+D duplicates, Escape deselects.
  // Never while typing in a field.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      const typing = !!target && (
        ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName) || target.isContentEditable
      );
      if (typing || e.altKey) return;
      if (e.key === " ") {
        e.preventDefault();
        void togglePreview();
        return;
      }
      const layer = layersRef.current.find((l) => l.id === selectedLayer);
      if (!layer) return;
      if (e.key === "Escape") {
        setSelectedLayer("background");
        return;
      }
      if (layer.type === "background" || layer.locked) return;
      if (layer.type === "captions" && (e.key === "ArrowUp" || e.key === "ArrowDown")) {
        e.preventDefault();
        const band = captionBand(
          String(project?.scene?.captionPreset ?? "social"),
          project?.aspect_ratio ?? "9:16",
        );
        const step = e.shiftKey ? 5 : 1;
        const from = capDragY ?? (project?.scene?.captionY as number | undefined) ?? band.top;
        const next = Math.max(2, Math.min(88, from + (e.key === "ArrowUp" ? -step : step)));
        setCapDragY(next);
        if (capSaveTimer.current) window.clearTimeout(capSaveTimer.current);
        capSaveTimer.current = window.setTimeout(() => {
          capSaveTimer.current = null;
          void saveScene({ captionY: next });
        }, 350);
        return;
      }
      if (e.key === "Delete" || e.key === "Backspace") {
        e.preventDefault();
        deleteLayer(layer.id);
        return;
      }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "d") {
        e.preventDefault();
        duplicateLayer(layer.id);
        return;
      }
      const step = e.shiftKey ? 5 : 1;
      const nudge: Record<string, Partial<Layer>> = {
        ArrowLeft: { x: Math.max(0, layer.x - step) },
        ArrowRight: { x: Math.min(100 - layer.width, layer.x + step) },
        ArrowUp: { y: Math.max(0, layer.y - step) },
        ArrowDown: { y: Math.min(100 - layer.height, layer.y + step) },
      };
      if (nudge[e.key]) {
        e.preventDefault();
        updateLayer(layer.id, nudge[e.key]);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });
  const watchedRender = useRef<string | null>(null);
  const [readyRender, setReadyRender] = useState<Job | null>(null);
  useEffect(() => {
    if (activeRender) {
      watchedRender.current = activeRender.id;
      return;
    }
    const id = watchedRender.current;
    if (!id) return;
    const finished = jobs.find((job) => job.id === id);
    if (finished?.status === "complete") {
      watchedRender.current = null;
      setReadyRender(finished);
      playSfx("confirm");
    } else if (finished?.status === "failed" || finished?.status === "canceled") {
      watchedRender.current = null;
    }
  }, [jobs, activeRender]);
  async function save(next: Layer[]) {
    layersRef.current = next;
    setLayers(next);
    if (project) await onUpdate({ scene: { ...sceneRef.current, layers: next } });
  }
  async function saveScene(patch: Record<string, unknown>) {
    if (!project) return;
    // Latest scene + the live layers: a PATCH replaces the scene wholesale,
    // and one built from a render-time snapshot during a debounce window
    // used to overwrite whichever edit it did not know about.
    await onUpdate({ scene: { ...sceneRef.current, layers: layersRef.current, ...patch } });
  }
  async function saveCuts(next: CutRange[]) {
    if (!project) return;
    const cuts = mergeCuts(next);
    // A cut clip is a different render, so a stale export would be wrong
    // rather than merely old; the fingerprint covers the scene, so this is
    // enough to make the next export produce a new file.
    await onUpdate({ scene: { ...project.scene, cuts } });
  }
  const sceneSfx = useMemo(
    () => ((project?.scene?.sfx as SfxCue[] | undefined) ?? []),
    [project?.scene?.sfx],
  );
  async function saveSfx(next: SfxCue[]) {
    if (!project) return;
    await onUpdate({ scene: { ...project.scene, sfx: next } });
  }
  // Fire each cue in the preview as the playhead crosses it, so placing an
  // effect can be judged by ear rather than by exporting. One Audio per cue
  // per pass; a cue is armed again when playback restarts or seeks back.
  const firedRef = useRef<Set<number>>(new Set());
  useEffect(() => {
    if (!playing) {
      firedRef.current.clear();
      return;
    }
    sceneSfx.forEach((cue, index) => {
      if (firedRef.current.has(index)) return;
      if (localPlayhead >= cue.at && localPlayhead < cue.at + 0.35) {
        firedRef.current.add(index);
        const audio = new Audio(
          cue.mediaId ? `/api/media/${cue.mediaId}/file` : `/api/library/sounds/${cue.soundId}/file`,
        );
        audio.volume = Math.max(0, Math.min(1, Math.pow(10, (cue.gainDb ?? 0) / 20) * 0.8));
        void audio.play().catch(() => undefined);
      }
    });
  }, [playing, localPlayhead, sceneSfx]);
  async function saveMusicBed(next: MusicBed | null) {
    if (!project) return;
    const scene = { ...project.scene };
    if (next) scene.music = next;
    else delete scene.music;
    await onUpdate({ scene });
  }
  // Takes the values rather than reading state: a caller that has just called
  // setClipStart sees the old value here, because React has not re-rendered
  // yet, and would save the clip as it was before the edit.
  async function saveProjectMeta(start = clipStart, end = clipEnd) {
    if (!project) return;
    await onUpdate({
      title: titleDraft.trim() || project.title,
      clip_start: Math.max(0, start),
      clip_end: Math.max(start + 0.5, end),
    });
  }
  function addLayer(type: Layer["type"], name: string) {
    const next = [...layersRef.current, { id: `${type}-${Date.now()}`, name, type, x: 20, y: 20, width: 60, height: 10, visible: true, locked: false, color: accent, text: name, startTime: 0, endTime: clipDuration }];
    setSelectedLayer(next.at(-1)?.id ?? "");
    void save(next);
  }
  const nudgeSaveTimer = useRef<number | null>(null);
  const capSaveTimer = useRef<number | null>(null);
  const trimSaveTimer = useRef<number | null>(null);
  const trimPending = useRef<[number, number] | null>(null);
  const [capDragY, setCapDragY] = useState<number | null>(null);
  // The caption drag draft belongs to one project's one timeline;
  // switching projects or restoring history must not carry it over.
  useEffect(() => setCapDragY(null), [project?.id, historyVersion]);
  function updateLayer(id: string, updates: Partial<Layer>) {
    // Apply on screen immediately; persist once the burst of changes stops
    // (holding an arrow key fires dozens of repeats a second — one PATCH
    // per repeat made nudging feel broken).
    const next = layersRef.current.map((l) => (l.id === id ? { ...l, ...updates } : l));
    layersRef.current = next;
    setLayers(next);
    if (nudgeSaveTimer.current) window.clearTimeout(nudgeSaveTimer.current);
    nudgeSaveTimer.current = window.setTimeout(() => {
      nudgeSaveTimer.current = null;
      void save(layersRef.current);
    }, 350);
  }
  function moveLayer(id: string, direction: -1 | 1) {
    const index = layers.findIndex((layer) => layer.id === id);
    const target = index + direction;
    if (index < 0 || target < 0 || target >= layers.length) return;
    const next = [...layers];
    [next[index], next[target]] = [next[target], next[index]];
    void save(next);
  }
  function deleteLayer(id: string) {
    const layer = layers.find((l) => l.id === id);
    if (!layer || layer.type === "background") return;
    const next = layers.filter((l) => l.id !== id);
    setSelectedLayer(next.at(-1)?.id ?? "background");
    void save(next);
  }
  function duplicateLayer(id: string) {
    const index = layers.findIndex((l) => l.id === id);
    const layer = layers[index];
    if (!layer || layer.type === "background") return;
    // Offset a little so the copy is visibly a second thing, not a glitch.
    const copy: Layer = {
      ...layer,
      id: `${layer.type}-${Date.now()}`,
      name: `${layer.name} copy`,
      x: Math.min(90, layer.x + 3),
      y: Math.min(90, layer.y + 3),
    };
    const next = [...layers.slice(0, index + 1), copy, ...layers.slice(index + 1)];
    setSelectedLayer(copy.id);
    void save(next);
  }
  function layerMenu(layer: Layer): MenuItem[] {
    const index = layers.findIndex((l) => l.id === layer.id);
    const fixed = layer.type === "background";
    return [
      { label: "Edit", onSelect: () => setSelectedLayer(layer.id) },
      { label: layer.visible ? "Hide" : "Show", disabled: fixed, onSelect: () => updateLayer(layer.id, { visible: !layer.visible }) },
      { label: layer.locked ? "Unlock" : "Lock", disabled: fixed, onSelect: () => updateLayer(layer.id, { locked: !layer.locked }) },
      "separator",
      { label: "Bring forward", disabled: fixed || index >= layers.length - 1, onSelect: () => moveLayer(layer.id, 1) },
      { label: "Send backward", disabled: fixed || index <= 1, onSelect: () => moveLayer(layer.id, -1) },
      { label: "Duplicate", disabled: fixed, onSelect: () => duplicateLayer(layer.id) },
      "separator",
      { label: "Delete layer", danger: true, disabled: fixed, onSelect: () => deleteLayer(layer.id) },
    ];
  }
  async function saveTranscript(next = transcriptDraft) {
    if (!media || !next) return;
    await onTranscriptUpdate(media.id, next);
  }
  function updateSegmentText(id: number, text: string) {
    if (!transcriptDraft) return;
    setTranscriptDraft({
      ...transcriptDraft,
      segments: transcriptDraft.segments.map((segment) =>
        segment.id === id ? { ...segment, text } : segment,
      ),
    });
  }
  async function useSegmentAsClip(start: number, end: number) {
    setClipStart(start);
    setClipEnd(end);
    await onUpdate({ clip_start: start, clip_end: end });
    setPlayhead(start);
  }
  async function handleExport(force = false) {
    setExportNote(null);
    const note = await onRender(force);
    setExportNote(note);
    if (note) window.setTimeout(() => setExportNote(null), 8000);
  }

  async function togglePreview() {
    const element = mediaRef.current;
    if (!element) {
      setPlaying((value) => !value);
      return;
    }
    if (playing) {
      element.pause();
      setPlaying(false);
      return;
    }
    // Resume from where the playhead is, unless it is already at the end.
    const from = playhead >= clipEnd - 0.05 ? clipStart : playhead;
    element.currentTime = Math.max(0, Math.min(clipEnd, from) - clipStart);
    await element.play().catch(() => undefined);
    setPlaying(true);
  }
  function drag(e: React.PointerEvent, layer: Layer) {
    if (layer.locked || !canvasRef.current) return;
    // Text selection and native image dragging both start from the same
    // pointer gesture; either one ends the reposition after a few pixels.
    e.preventDefault();
    const rect = canvasRef.current.getBoundingClientRect();
    const sx = e.clientX;
    const sy = e.clientY;
    const ox = layer.x;
    const oy = layer.y;
    try { (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId); } catch { /* capture is best-effort */ }
    const move = (event: PointerEvent) =>
      setLayers((current) => {
        const next = current.map((l) =>
          l.id === layer.id
            ? {
                ...l,
                x: Math.max(
                  0,
                  Math.min(
                    100 - l.width,
                    ox + ((event.clientX - sx) / rect.width) * 100,
                  ),
                ),
                y: Math.max(
                  0,
                  Math.min(
                    100 - l.height,
                    oy + ((event.clientY - sy) / rect.height) * 100,
                  ),
                ),
              }
            : l,
        );
        layersRef.current = next;
        return next;
      });
    const up = async () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", up);
      await save(layersRef.current);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", up);
  }
  /**
   * Captions are positioned by the renderer from a single number — the top
   * of their band in percent — not by their layer box, so they get their own
   * vertical drag that ends in scene.captionY. Before this they could be
   * "dragged" but silently snapped back, which read as broken.
   */
  function dragCaptions(e: React.PointerEvent) {
    if (!canvasRef.current) return;
    e.preventDefault();
    const rect = canvasRef.current.getBoundingClientRect();
    const band = captionBand(
      String(project?.scene?.captionPreset ?? "social"),
      project?.aspect_ratio ?? "9:16",
    );
    const from = capDragY ?? (project?.scene?.captionY as number | undefined) ?? band.top;
    const sy = e.clientY;
    let latest = from;
    try { (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId); } catch { /* capture is best-effort */ }
    const move = (event: PointerEvent) => {
      latest = Math.max(2, Math.min(88, from + ((event.clientY - sy) / rect.height) * 100));
      setCapDragY(latest);
    };
    const up = async () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", up);
      await saveScene({ captionY: Math.round(latest * 10) / 10 });
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", up);
  }
  /**
   * Drag a corner or edge handle. The box is stored as percentages of the
   * canvas, so the maths is the same at every zoom; a minimum size keeps a
   * layer from being shrunk to nothing and lost.
   */
  function resize(e: React.PointerEvent, layer: Layer, handle: string) {
    if (layer.locked || !canvasRef.current) return;
    e.stopPropagation();
    e.preventDefault();
    const rect = canvasRef.current.getBoundingClientRect();
    const sx = e.clientX;
    const sy = e.clientY;
    const o = { x: layer.x, y: layer.y, w: layer.width, h: layer.height };
    const MIN = 4;
    try { (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId); } catch { /* capture is best-effort */ }
    const move = (event: PointerEvent) => {
      const dx = ((event.clientX - sx) / rect.width) * 100;
      const dy = ((event.clientY - sy) / rect.height) * 100;
      let { x, y, w, h } = o;
      if (handle.includes("e")) w = Math.max(MIN, Math.min(100 - o.x, o.w + dx));
      if (handle.includes("s")) h = Math.max(MIN, Math.min(100 - o.y, o.h + dy));
      if (handle.includes("w")) {
        const nx = Math.max(0, Math.min(o.x + o.w - MIN, o.x + dx));
        w = o.w + (o.x - nx);
        x = nx;
      }
      if (handle.includes("n")) {
        const ny = Math.max(0, Math.min(o.y + o.h - MIN, o.y + dy));
        h = o.h + (o.y - ny);
        y = ny;
      }
      setLayers((current) => {
        const next = current.map((l) => (l.id === layer.id ? { ...l, x, y, width: w, height: h } : l));
        layersRef.current = next;
        return next;
      });
    };
    const up = async () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", up);
      await save(layersRef.current);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", up);
  }
  return (
    <div className="studio-editor">
      <div className="studio-toolbar">
        <input
          className="project-title-input"
          aria-label="Project title"
          value={titleDraft}
          onChange={(e) => setTitleDraft(e.target.value)}
          onBlur={() => void saveProjectMeta()}
        />
        <button
          className="ghost"
          onClick={togglePreview}
        >
          <Play size={16} /> {playing ? "Pause" : "Preview"}
        </button>
        <span className="toolbar-divider" />
        <button className="icon-button" title="Zoom canvas" onClick={() => setZoom((value) => value >= 1.25 ? 0.85 : value + 0.1)}>
          <ZoomIn size={17} />
        </button>
        <span className="toolbar-spacer" />
        {activeRender && (
          <span className="render-status">
            <Loader2 className="spin" size={14} /> {activeRender.message} {activeRender.progress}%
          </span>
        )}
        <ExportNote note={exportNote} onForce={() => void handleExport(true)} />
        <button
          className="primary"
          disabled={Boolean(activeRender)}
          title={activeRender ? "Your video is being made" : "Make the video file"}
          onClick={() => {
            playSfx("confirm");
            void handleExport();
          }}
        >
          <Download size={16} /> {activeRender ? "Making your video…" : "Export"}
        </button>
      </div>
      {readyRender && (
        <ReadyCard
          job={readyRender}
          title={project?.title ?? "Your clip"}
          onClose={() => setReadyRender(null)}
          onAnother={() => {
            setReadyRender(null);
            onNewClip?.();
          }}
        />
      )}
      <div className="studio-grid">
        <aside className="studio-tools">
          <span className="sidebar-label">Add to canvas</span>
          {[
            [FileAudio, "Media", "artwork", "Podcast Artwork"],
            [LayoutTemplate, "Templates", "title", "Episode Title"],
            [Sparkles, "Captions", "captions", "Captions"],
            [AudioLines, "Waveform", "waveform", "Waveform"],
          ].map(([Icon, label, type, name]) => (
            <button key={label as string} onClick={() => addLayer(type as Layer["type"], name as string)}>
              <Icon size={17} />
              {label as string}
            </button>
          ))}
          <button
            title="Your logo, faint, in a corner of every frame"
            onClick={() => {
              // A watermark is just a small, half-transparent artwork layer
              // parked in a corner — but it must name an actual image: the
              // renderer only draws artwork layers that do.
              const logo = media?.artwork_media_id
                ?? allMedia.find((m) => m.content_type.startsWith("image/"))?.id;
              if (!logo) {
                window.alert("Add a cover picture first (the source step has a box for it), then the watermark has something to show.");
                return;
              }
              const next = [...layersRef.current, {
                id: `artwork-${Date.now()}`,
                name: "Watermark",
                type: "artwork" as const,
                mediaId: logo,
                x: 72, y: 3, width: 24, height: 8,
                visible: true, locked: false,
                opacity: 0.55, radius: 0.1,
                startTime: 0, endTime: clipDuration,
              }];
              setSelectedLayer(next.at(-1)?.id ?? "");
              void save(next);
            }}
          >
            <Droplets size={17} />
            Watermark
          </button>
        </aside>
        <section className="canvas-area">
          <div
            className={`canvas-wrap ratio-${(project?.aspect_ratio ?? "9:16").replace(":", "-")}`}
          >
            <div
              className="design-canvas"
              ref={canvasRef}
              style={{
                background,
                transform: `scale(${zoom})`,
                transformOrigin: "center",
                // The typefaces the export will use, as variables the layer
                // styles read, so LayerContent needs no new props.
                ["--title-font" as string]: familyOf(String(project?.scene?.font ?? "inter")),
                ["--caption-font" as string]: familyOf(String(project?.scene?.captionFont ?? "inter")),
              } as CSSProperties}
            >
              {media?.content_type.startsWith("video/") && project?.scene?.videoBackground !== false && (
                <VideoBackdrop
                  src={api.mediaFileUrl(media.id)}
                  time={clipStart + localPlayhead}
                  playing={playing}
                />
              )}
              {backgroundImageUrl && !(media?.content_type.startsWith("video/") && project?.scene?.videoBackground !== false) && (
                /* Mirrors the render's plate: cover, blur, darken. The blur is
                   scaled to the preview so it reads the same as the export at
                   a fraction of the pixels. */
                <div
                  className="canvas-backdrop"
                  style={{
                    backgroundImage: `url(${backgroundImageUrl})`,
                    // The render blurs a 1/6-scale copy, so its effective
                    // radius at 1080px wide is ~6x the stored value. The
                    // preview is roughly a third of that width, hence ~0.38.
                    filter: `blur(${backgroundBlur * 0.38}px) brightness(${1 - backgroundDim})`,
                  }}
                />
              )}
              <div className="safe-zone" />
              {safeArea && (
                /* Every vertical platform covers part of the frame with its own
                   interface. Anything under these bands is effectively
                   invisible, so the editor shows where they land. */
                <>
                  <div
                    className="platform-guide bottom"
                    style={{ height: `${safeArea.bottom * 100}%` }}
                  >
                    <span>{safeArea.label} UI</span>
                  </div>
                  <div
                    className="platform-guide top"
                    style={{ height: `${safeArea.top * 100}%` }}
                  />
                  <div
                    className="platform-guide right"
                    style={{ width: `${safeArea.right * 100}%` }}
                  />
                </>
              )}
              {layers
                .filter((l) => {
                  const starts = l.startTime ?? 0;
                  const ends = l.endTime ?? clipDuration;
                  return l.visible && localPlayhead >= starts && localPlayhead <= ends;
                })
                .map((layer) => (
                  <div
                    key={layer.id}
                    onContextMenu={(e) => {
                      setSelectedLayer(layer.id);
                      openMenu(e, layerMenu(layer), layer.name);
                    }}
                    className={`canvas-layer layer-${layer.type} ${selectedLayer === layer.id ? "selected" : ""}${layer.enter && layer.enter !== "none" && playing ? ` enter-${layer.enter}` : ""}`}
                    style={{
                      ["--enter-seconds" as string]: `${layer.enterSeconds ?? 0.5}s`,
                      opacity: layer.opacity ?? 1,
                      left: `${layer.x}%`,
                      // Captions are the one layer whose position the renderer
                      // does not take from the scene: it places them from the
                      // preset's margin. The preview follows the renderer, so
                      // what the editor shows is what gets burned in.
                      ...(layer.type === "captions"
                        ? (() => {
                            const band = captionBand(
                              captionPreset,
                              project?.aspect_ratio ?? "9:16",
                            );
                            const top = capDragY ?? (project?.scene?.captionY as number | undefined) ?? band.top;
                            return { top: `${top}%`, height: `${band.height}%` };
                          })()
                        : { top: `${layer.y}%`, height: `${layer.height}%` }),
                      width: `${layer.width}%`,
                      color: layer.color ?? "#fff",
                      borderColor: accent,
                    }}
                    onPointerDown={(e) => {
                      setSelectedLayer(layer.id);
                      if (layer.type === "captions") dragCaptions(e);
                      else drag(e, layer);
                    }}
                  >
                    <LayerContent
                      layer={layer}
                      waveStyle={String(project?.scene?.waveStyle ?? "pulse")}
                      captionScale={Number(project?.scene?.captionScale ?? 1)}
                      title={project?.title ?? "Episode title"}
                      media={media}
                      accent={accent}
                      peaks={clipPeaks}
                      caption={activeCaption}
                      captionPreset={captionPreset}
                      live={
                        playing && String(project?.scene?.waveStyle ?? "pulse").startsWith("pulse")
                          ? {
                              tick: liveTick,
                              bins: String(project?.scene?.waveStyle ?? "pulse") === "pulseFine" ? 52
                                : String(project?.scene?.waveStyle ?? "pulse") === "pulseChunky" ? 22 : 34,
                              // Loudness at the playhead, from the clip's own
                              // envelope, so the bars fall silent when the
                              // speaker does.
                              level: (() => {
                                if (!clipPeaks.length) return 0.5;
                                const at = Math.min(
                                  clipPeaks.length - 1,
                                  Math.max(0, Math.floor((localPlayhead / clipDuration) * clipPeaks.length)),
                                );
                                const loudest = Math.max(...clipPeaks) || 1;
                                return Math.pow(Math.max(clipPeaks[at], 0) / loudest, 0.65);
                              })(),
                            }
                          : null
                      }
                    />
                  </div>
                ))}
              {(() => {
                // Handles live in their own box above every layer: inside
                // the layer they were clipped by its overflow and covered by
                // whatever was drawn later, so the corners could not be
                // grabbed.
                const layer = layers.find((l) => l.id === selectedLayer);
                if (!layer || layer.type === "captions" || layer.type === "background" || layer.locked || !layer.visible) return null;
                return (
                  <div
                    className="layer-handles"
                    style={{ left: `${layer.x}%`, top: `${layer.y}%`, width: `${layer.width}%`, height: `${layer.height}%` }}
                  >
                    {["nw", "n", "ne", "e", "se", "s", "sw", "w"].map((handle) => (
                      <span
                        key={handle}
                        className={`resize-handle ${handle}`}
                        title="Drag to resize"
                        onPointerDown={(e) => resize(e, layer, handle)}
                      />
                    ))}
                  </div>
                );
              })()}
            </div>
          </div>
          <div className="canvas-status">
            <span>{project?.aspect_ratio ?? "9:16"}</span>
            <span>Safe zone guide</span>
            <span>{formatTime(localPlayhead)} / {formatTime(clipDuration)}</span>
          </div>
          {media && (
            <div className="source-preview">
              <strong>{media.original_name}</strong>
              <small>
                Clip audio only: {formatTime(clipStart)}–{formatTime(clipEnd)}
                of the episode, {formatTime(clipEnd - clipStart)} long.
              </small>
              {typeof project?.scene?.pickReason === "string" && project.scene.pickReason && (
                <small className="pick-reason">
                  <Sparkles size={11} /> Kinder picked this moment: {project.scene.pickReason}
                </small>
              )}
              {media.content_type.startsWith("video/") ? (
                <video
                  controls
                  preload="metadata"
                  src={sourceUrl}
                  ref={(node) => {
                    mediaRef.current = node;
                  }}
                  // Only while paused: during playback the animation frame
                  // above is the clock, and two writers is what caused the
                  // drift in the first place. This keeps the playhead honest
                  // when somebody scrubs the element's own controls.
                  onTimeUpdate={(e) => {
                    if (!playing) setPlayhead(clipStart + e.currentTarget.currentTime);
                  }}
                  onPlay={() => setPlaying(true)}
                  onPause={() => setPlaying(false)}
                />
              ) : (
                <audio
                  controls
                  preload="metadata"
                  src={sourceUrl}
                  ref={(node) => {
                    mediaRef.current = node;
                  }}
                  // Only while paused: during playback the animation frame
                  // above is the clock, and two writers is what caused the
                  // drift in the first place. This keeps the playhead honest
                  // when somebody scrubs the element's own controls.
                  onTimeUpdate={(e) => {
                    if (!playing) setPlayhead(clipStart + e.currentTarget.currentTime);
                  }}
                  onPlay={() => setPlaying(true)}
                  onPause={() => setPlaying(false)}
                />
              )}
            </div>
          )}
        </section>
        <aside className="inspector">
          <div className="studio-mode" role="group" aria-label="How much to show">
            <button className={simple ? "on" : ""} onClick={() => setSimple(true)} title="Just the basics: clip, look, words, layers">Simple</button>
            <button className={simple ? "" : "on"} onClick={() => setSimple(false)} title="Every panel: music, effects, voice-over, shapes, batches">Everything</button>
          </div>
          <div className="clip-property-block">
            <span className="sidebar-label">Clip</span>
            <div className="mini-fields">
              <TimeField
                label="Start"
                value={clipStart}
                onCommit={(value) => {
                  // Keep the clip's length when the start is moved past the
                  // end, rather than refusing the start.
                  const next = Math.max(0, value);
                  let end = clipEnd;
                  if (next >= clipEnd) {
                    end = next + Math.max(0.5, clipEnd - clipStart);
                    setClipEnd(end);
                  }
                  setClipStart(next);
                  void saveProjectMeta(next, end);
                }}
              />
              <TimeField
                label="End"
                value={clipEnd}
                min={0.5}
                onCommit={(value) => {
                  const end = Math.max(clipStart + 0.5, value);
                  setClipEnd(end);
                  void saveProjectMeta(clipStart, end);
                }}
              />
            </div>
          </div>
          {(() => {
            const layer = layers.find((l) => l.id === selectedLayer);
            if (!layer || layer.type === "background") return null;
            const num = (v: number) => Math.round(v * 10) / 10;
            const field = (
              label: string,
              value: number,
              apply: (v: number) => void,
              min = -50,
              max = 150,
            ) => (
              <label key={label} className="size-field">
                {label}
                <input
                  type="number"
                  value={num(value)}
                  min={min}
                  max={max}
                  step={1}
                  onChange={(e) => {
                    const v = Number(e.target.value);
                    if (Number.isFinite(v)) apply(Math.max(min, Math.min(max, v)));
                  }}
                />
              </label>
            );
            return (
              <div className="layer-inspector">
                <span className="sidebar-label">Selected: {layer.name || layer.type}</span>
                {layer.type !== "captions" && (
                  <div className="size-grid">
                    {field("Left", layer.x, (v) => updateLayer(layer.id, { x: v }))}
                    {field("Top", layer.y, (v) => updateLayer(layer.id, { y: v }))}
                    {field("Width", layer.width, (v) => updateLayer(layer.id, { width: v }), 1, 200)}
                    {field("Height", layer.height, (v) => updateLayer(layer.id, { height: v }), 1, 200)}
                  </div>
                )}
                {layer.type === "title" && (
                  <>
                    <label>
                      Text size
                      <md-slider
                        min={50} max={160} step={5} labeled
                        value={Math.round(((layer.fontScale as number) ?? 1) * 100)}
                        onInput={(e) => updateLayer(layer.id, { fontScale: Number((e.target as unknown as { value: number }).value) / 100 })}
                      ></md-slider>
                    </label>
                    <div className="align-row" role="group" aria-label="Text alignment">
                      {(["left", "center", "right"] as const).map((a) => (
                        <button
                          key={a}
                          className={((layer.align as string) ?? "center") === a ? "on" : ""}
                          onClick={() => updateLayer(layer.id, { align: a })}
                        >
                          {a === "left" ? "Left" : a === "center" ? "Middle" : "Right"}
                        </button>
                      ))}
                    </div>
                    <label>
                      Colour
                      <input
                        type="color"
                        value={(layer.color as string) ?? "#ffffff"}
                        onChange={(e) => updateLayer(layer.id, { color: e.target.value })}
                      />
                    </label>
                  </>
                )}
                {layer.type === "captions" && (
                  <>
                    <label>
                      Words size
                      <md-slider
                        min={60} max={160} step={5} labeled
                        value={Math.round(Number(project?.scene?.captionScale ?? 1) * 100)}
                        onInput={(e) => void saveScene({ captionScale: Number((e.target as unknown as { value: number }).value) / 100 })}
                      ></md-slider>
                    </label>
                    <p className="muted">Drag the captions up or down on the video to move them.</p>
                  </>
                )}
                {layer.type === "artwork" && (
                  <label>
                    Rounded corners
                    <md-slider
                      min={0} max={50} step={1} labeled
                      value={Math.round(((layer.radius as number) ?? 0) * 100)}
                      onInput={(e) => updateLayer(layer.id, { radius: Number((e.target as unknown as { value: number }).value) / 100 })}
                    ></md-slider>
                  </label>
                )}
                {layer.type === "waveform" && (
                  <label>
                    Colour
                    <input
                      type="color"
                      value={(layer.color as string) ?? "#759a92"}
                      onChange={(e) => updateLayer(layer.id, { color: e.target.value })}
                    />
                  </label>
                )}
                {layer.type !== "captions" && (
                  <label>
                    See-through
                    <md-slider
                      min={5} max={100} step={5} labeled
                      value={Math.round(((layer.opacity as number) ?? 1) * 100)}
                      onInput={(e) => updateLayer(layer.id, { opacity: Number((e.target as unknown as { value: number }).value) / 100 })}
                    ></md-slider>
                  </label>
                )}
              </div>
            );
          })()}
          <DesignPanel
            project={project}
            media={allMedia}
            sourceIsVideo={Boolean(media?.content_type.startsWith("video/"))}
            onScene={(patch) => saveScene(patch)}
            onMediaAdded={onMediaAdded}
          />
          {simple && (
            <p className="muted small simple-note">
              Music, sound effects, voice-over, other shapes and batch tools are
              under <button className="text-button inline" onClick={() => setSimple(false)}>Everything</button>.
            </p>
          )}
          {!simple && (<>
          <VariantsPanel project={project} onCreated={onReloadProjects} />
          <Destinations project={project} jobs={jobs} />
          <BatchClips
            mediaId={media?.id ?? null}
            templates={saved}
            onDone={onReloadProjects}
          />
          <TemplatePanel
            project={project}
            templates={saved}
            onSave={onSaveTemplate}
            onApply={onApplyTemplate}
          />
          <MusicPanel
            bed={musicBed}
            clipDuration={clipDuration}
            playhead={localPlayhead}
            onChange={(next) => void saveMusicBed(next)}
          />
          <VoiceoverPanel
            projectId={project?.id ?? null}
            playhead={localPlayhead}
            clipDuration={clipDuration}
            cues={sceneSfx}
            onChange={(next) => void saveSfx(next)}
          />
          <SfxPanel
            cues={sceneSfx}
            playhead={localPlayhead}
            clipDuration={clipDuration}
            onChange={(next) => void saveSfx(next)}
            onSeek={(at) => seek(clipStart + at)}
          />
          </>)}
          <div className="inspector-heading">
            <span className="sidebar-label">What is on the picture</span>
          </div>
          <p className="muted small">
            Front to back. Click one to change it; use the arrows to move it in
            front of or behind the others.
          </p>
          {layers
            .slice()
            .reverse()
            .map((layer) => (
              <div
                className={`layer-row ${selectedLayer === layer.id ? "selected" : ""}`}
                key={layer.id}
                onClick={() => setSelectedLayer(layer.id)}
              >
                <Move size={14} />
                <span>{layer.name}</span>
                <button
                  className="layer-action"
                  title="Move layer down"
                  onClick={(e) => {
                    e.stopPropagation();
                    moveLayer(layer.id, -1);
                  }}
                >
                  <ArrowDown size={13} />
                </button>
                <button
                  className="layer-action"
                  title="Move layer up"
                  onClick={(e) => {
                    e.stopPropagation();
                    moveLayer(layer.id, 1);
                  }}
                >
                  <ArrowUp size={13} />
                </button>
                <button
                  className="layer-action"
                  title={layer.visible ? "Hide layer" : "Show layer"}
                  onClick={(e) => {
                    e.stopPropagation();
                    void save(
                      layers.map((l) =>
                        l.id === layer.id ? { ...l, visible: !l.visible } : l,
                      ),
                    );
                  }}
                >
                  {layer.visible ? <Eye size={14} /> : <EyeOff size={14} />}
                </button>
                <button
                  className="layer-action"
                  title={layer.locked ? "Unlock layer" : "Lock layer"}
                  onClick={(e) => {
                    e.stopPropagation();
                    updateLayer(layer.id, { locked: !layer.locked });
                  }}
                >
                  {layer.locked ? <Lock size={13} /> : <Unlock size={13} />}
                </button>
              </div>
            ))}
          {active && (
            <div className="properties">
              <span className="sidebar-label">Selected item</span>
              <label>
                Name
                <input
                  value={active.name}
                  onChange={(e) => updateLayer(active.id, { name: e.target.value })}
                />
              </label>
              {["title", "captions"].includes(active.type) && (
                <>
                  <label>
                    Text
                    <textarea
                      ref={textFieldRef}
                      value={active.text ?? ""}
                      onChange={(e) => updateLayer(active.id, { text: e.target.value })}
                    />
                  </label>
                  {active.type === "title" && (
                    <div className="token-help">
                      <span className="muted small">
                        Insert something that changes with the clip:
                      </span>
                      <div className="token-chips">
                        {TOKENS.map(([name, description]) => (
                          <button
                            key={name}
                            className="token-chip"
                            title={description}
                            onClick={() => {
                              // Inserted at the caret rather than appended, so a
                              // token can go in the middle of a line somebody has
                              // already written.
                              const field = textFieldRef.current;
                              const current = active.text ?? "";
                              const at = field?.selectionStart ?? current.length;
                              const next =
                                current.slice(0, at) + `{{${name}}}` + current.slice(
                                  field?.selectionEnd ?? at,
                                );
                              updateLayer(active.id, { text: next });
                            }}
                          >
                            {name}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                </>
              )}
              <label>
                Color
                <input
                  type="color"
                  value={active.color ?? accent}
                  onChange={(e) => updateLayer(active.id, { color: e.target.value })}
                />
              </label>
              {active.type === "artwork" && (
                <div className="layer-image">
                  <label>
                    Image
                    <select
                      value={active.mediaId ?? ""}
                      onChange={(e) => updateLayer(active.id, { mediaId: e.target.value || undefined })}
                    >
                      <option value="">None</option>
                      {allMedia
                        .filter((item) => item.content_type.startsWith("image/"))
                        .map((item) => (
                          <option key={item.id} value={item.id}>{item.original_name}</option>
                        ))}
                    </select>
                  </label>
                  <label className="ghost compact upload-inline">
                    <Upload size={13} /> Upload image
                    <input
                      type="file"
                      accept="image/png,image/jpeg,image/webp"
                      hidden
                      onChange={async (e) => {
                        const file = e.target.files?.[0];
                        if (!file) return;
                        e.target.value = "";
                        try {
                          // A logo or a sponsor badge for this layer alone;
                          // the background and the other layers keep theirs.
                          const result = await api.uploadMedia(file);
                          onMediaAdded(result.media);
                          updateLayer(active.id, { mediaId: result.media.id });
                        } catch (error) {
                          setExportNote(error instanceof Error ? error.message : "Upload failed");
                        }
                      }}
                    />
                  </label>
                </div>
              )}
              {["title", "artwork"].includes(active.type) && (
                <div className="field-row">
                  <label>
                    Enters
                    <select
                      value={active.enter ?? "none"}
                      onChange={(e) =>
                        updateLayer(active.id, { enter: e.target.value as Layer["enter"] })
                      }
                    >
                      <option value="none">Appear</option>
                      <option value="fade">Fade in</option>
                      <option value="rise">Rise in</option>
                      <option value="drop">Drop in</option>
                      <option value="slide">Slide in</option>
                    </select>
                  </label>
                  <label>
                    Over
                    <input
                      type="number"
                      min={0.1}
                      max={3}
                      step={0.1}
                      value={active.enterSeconds ?? 0.5}
                      disabled={(active.enter ?? "none") === "none"}
                      onChange={(e) =>
                        updateLayer(active.id, { enterSeconds: Number(e.target.value) })
                      }
                    />
                  </label>
                </div>
              )}
              <label>
                Width
                <input
                  type="range"
                  min="10"
                  max="100"
                  value={active.width}
                  onChange={(e) =>
                    void save(
                      layers.map((l) =>
                        l.id === active.id
                          ? { ...l, width: Number(e.target.value) }
                          : l,
                      ),
                    )
                  }
                />
              </label>
              <label>
                Height
                <input
                  type="range"
                  min="5"
                  max="100"
                  value={active.height}
                  onChange={(e) =>
                    void save(
                      layers.map((l) =>
                        l.id === active.id
                          ? { ...l, height: Number(e.target.value) }
                          : l,
                      ),
                    )
                  }
                />
              </label>
              <div className="mini-fields">
                <label>
                  X
                  <input
                    type="number"
                    min="0"
                    max="100"
                    value={active.x.toFixed(1)}
                    onChange={(e) => updateLayer(active.id, { x: Number(e.target.value) })}
                  />
                </label>
                <label>
                  Y
                  <input
                    type="number"
                    min="0"
                    max="100"
                    value={active.y.toFixed(1)}
                    onChange={(e) => updateLayer(active.id, { y: Number(e.target.value) })}
                  />
                </label>
              </div>
              {active.type !== "captions" && (
                <div className="mini-fields">
                  <label>
                    Width %
                    <input
                      type="number"
                      min="4"
                      max="100"
                      value={active.width.toFixed(1)}
                      onChange={(e) => updateLayer(active.id, { width: Math.max(4, Math.min(100, Number(e.target.value))) })}
                    />
                  </label>
                  <label>
                    Height %
                    <input
                      type="number"
                      min="4"
                      max="100"
                      value={active.height.toFixed(1)}
                      onChange={(e) => updateLayer(active.id, { height: Math.max(4, Math.min(100, Number(e.target.value))) })}
                    />
                  </label>
                </div>
              )}
              <div className="mini-fields">
                <label>
                  In
                  <input
                    type="number"
                    min="0"
                    max={clipDuration}
                    step="0.1"
                    value={(active.startTime ?? 0).toFixed(1)}
                    onChange={(e) => updateLayer(active.id, { startTime: Number(e.target.value) })}
                  />
                </label>
                <label>
                  Out
                  <input
                    type="number"
                    min="0"
                    max={clipDuration}
                    step="0.1"
                    value={(active.endTime ?? clipDuration).toFixed(1)}
                    onChange={(e) => updateLayer(active.id, { endTime: Number(e.target.value) })}
                  />
                </label>
              </div>
              {active.type !== "background" && active.type !== "captions" && (
                <label>
                  See-through {Math.round((active.opacity ?? 1) * 100)}%
                  <input
                    type="range"
                    min={5}
                    max={100}
                    value={Math.round((active.opacity ?? 1) * 100)}
                    onChange={(e) => updateLayer(active.id, { opacity: Number(e.target.value) / 100 })}
                  />
                </label>
              )}
              <button
                className="ghost compact danger"
                disabled={active.type === "background"}
                onClick={() => deleteLayer(active.id)}
              >
                <Trash2 size={14} /> Delete layer
              </button>
            </div>
          )}
          <HistoryPanel
            projectId={project?.id ?? null}
            version={historyVersion}
            onRestored={onReloadProjects}
          />
          <div className="transcript-editor">
            <div className="inspector-heading">
              <span className="sidebar-label">Cut the clip</span>
            </div>
            <TranscriptCuts
              transcript={transcriptDraft}
              clipStart={clipStart}
              clipEnd={clipEnd}
              cuts={sceneCuts}
              onChange={(next) => void saveCuts(next)}
              onSeek={seek}
            />
          </div>
          {!simple && (
          <div className="transcript-editor">
            <div className="inspector-heading">
              <span className="sidebar-label">Full transcript</span>
              {transcriptDraft && (
                <button className="ghost compact" onClick={() => void saveTranscript()}>
                  Save
                </button>
              )}
            </div>
            {transcriptDraft?.segments.length ? (
              <>
                {!showWholeTranscript && transcriptDraft.segments.length > nearbySegments.length && (
                  <p className="muted small">
                    Showing the minute around the clip ({nearbySegments.length} of{" "}
                    {transcriptDraft.segments.length} lines).{" "}
                    <button className="link-button" onClick={() => setShowWholeTranscript(true)}>
                      Show the whole episode
                    </button>
                  </p>
                )}
                {(showWholeTranscript ? transcriptDraft.segments : nearbySegments).map((segment) => (
                <div className="transcript-row" key={segment.id}>
                  <button onClick={() => void useSegmentAsClip(segment.start, segment.end)}>
                    {formatTime(segment.start)}
                  </button>
                  <textarea
                    value={segment.text}
                    onChange={(e) => updateSegmentText(segment.id, e.target.value)}
                    onBlur={() => void saveTranscript()}
                  />
                </div>
                ))}
              </>
            ) : (
              <p className="muted">Upload media and wait for transcription to edit captions.</p>
            )}
          </div>
          )}
        </aside>
      </div>
      {media && (
        /* The full trimmer, not just two m:ss boxes: before this, changing a
           clip's length after creation meant typing timestamps blind, and
           people started a new clip from scratch instead. Same waveform,
           yellow handles, transcript lines, and zoom as the create step. */
        <details className="clip-length-block">
          <summary>
            Change the clip's length
            <span className="muted"> — drag the yellow handles or type times</span>
          </summary>
          <ClipSelector
            start={clipStart}
            end={clipEnd}
            duration={media.duration_seconds ?? Math.max(clipEnd, clipStart + 0.5)}
            segments={media.transcript?.segments ?? []}
            mediaId={media.id}
            transcriptReady={Boolean(media.has_transcript)}
            onChange={(s, e) => {
              // The trimmer reports every pointer move; saving each tick
              // floods the server with PATCHes whose late responses can
              // overwrite whatever edit follows. Track live, save on settle.
              setClipStart(s);
              setClipEnd(e);
              trimPending.current = [s, e];
              if (trimSaveTimer.current) window.clearTimeout(trimSaveTimer.current);
              trimSaveTimer.current = window.setTimeout(() => {
                trimSaveTimer.current = null;
                const pending = trimPending.current;
                if (pending) void saveProjectMeta(pending[0], pending[1]);
              }, 500);
            }}
          />
        </details>
      )}
      <Timeline
        project={project}
        playhead={playhead}
        setPlayhead={seek}
        layers={layers}
        jobs={jobs}
        duration={clipDuration}
        onLayerTimingChange={updateLayer}
      />
    </div>
  );
}
/**
 * Text that fills itself in from the episode.
 *
 * Keep in step with TOKENS in backend/app/services/tokens.py — the backend is
 * where they are actually resolved, and a chip offering one it does not know
 * would put literal braces on a video.
 */
const TOKENS: [string, string][] = [
  ["episode", "Episode title, from the feed"],
  ["show", "Show name, from the feed"],
  ["date", "Episode publication date"],
  ["speaker", "Who is speaking at the start of the clip"],
  ["title", "The clip's own title"],
  ["timecode", "Where the clip starts in the episode"],
  ["duration", "Clip length in seconds"],
];

/**
 * A number field for a time in seconds that can be typed into.
 *
 * The previous fields were controlled by `value.toFixed(1)` and applied on
 * every keystroke, so typing "120" went 1.0 → 1.02 → 1.0: the field
 * reformatted under the caret after each digit and a multi-digit time could
 * not be entered by keyboard at all. Automated tests never saw it because they
 * paste the whole value at once.
 *
 * This holds the text while it has focus and commits on blur or Enter.
 */
function TimeField({
  label,
  value,
  min = 0,
  onCommit,
}: {
  label: string;
  value: number;
  min?: number;
  onCommit: (value: number) => void;
}) {
  const [text, setText] = useState<string | null>(null);
  const commit = () => {
    if (text === null) return;
    const parsed = parseClock(text);
    setText(null);
    if (parsed === null) return;
    // An untouched field must not commit: the display rounds to one decimal,
    // so committing it back drifts the stored value (719.94 -> 719.9), fires
    // a pointless save, and the re-render swallows whatever the user clicked
    // next - the blur and that click are the same gesture.
    if (Math.abs(parsed - value) < 0.05) return;
    onCommit(Math.max(min, parsed));
  };
  return (
    <label>
      {label}
      <input
        type="text"
        inputMode="decimal"
        placeholder="m:ss"
        value={text ?? clockText(value)}
        onFocus={(e) => setText(e.target.value)}
        onChange={(e) => setText(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            e.currentTarget.blur();
          }
        }}
      />
    </label>
  );
}

/** What you can do to a project, wherever it is listed. */
function projectMenu(
  p: Project,
  actions: {
    open: () => void;
    rename?: (title: string) => Promise<void>;
    remove?: (p: Project) => Promise<void>;
  },
): MenuItem[] {
  const items: MenuItem[] = [{ label: "Open in Studio", onSelect: actions.open }];
  if (actions.rename) {
    const rename = actions.rename;
    items.push({
      label: "Rename…",
      onSelect: () => {
        const title = window.prompt("New name for this project", p.title);
        if (title && title.trim() && title.trim() !== p.title) void rename(title.trim());
      },
    });
  }
  if (actions.remove) {
    const remove = actions.remove;
    items.push("separator", {
      label: "Delete…",
      danger: true,
      onSelect: () => {
        if (window.confirm(`Move "${p.title}" to the trash? You can bring it back for 7 days.`)) void remove(p);
      },
    });
  }
  return items;
}

/**
 * Everything about you and your show, in one place: the cover picture, the
 * intro and outro, the accounts you post to — and, for the admin, the
 * machine-room controls that used to sit at the bottom of every page.
 */
function SettingsPage({
  user,
  media,
  onUpload,
  onRefresh,
  admin,
}: {
  user: User | null;
  media: MediaAsset[];
  onUpload: (f: File, onProgress?: (fraction: number) => void) => Promise<MediaAsset>;
  onRefresh: () => Promise<void>;
  admin: ReactNode;
}) {
  return (
    <div className="library-page settings-page">
      <div className="page-heading">
        <div>
          <span className="kicker">Account</span>
          <h2>Settings</h2>
          <p>Signed in as <strong>{user?.username}</strong>{user?.is_admin ? " · admin" : ""}.</p>
        </div>
      </div>
      <ProfileSection user={user} media={media} onUpload={onUpload} onRefresh={onRefresh} />
      <section className="settings-section">
        <h3>Your show</h3>
        <p className="muted">These apply to every clip you make.</p>
        <ShowArtwork media={media} onUpload={onUpload} onRefresh={onRefresh} />
        <BrandingClips media={media} onUpload={onUpload} onRefresh={onRefresh} />
        <YourFonts />
      </section>
      <section className="settings-section">
        <h3>Posting accounts</h3>
        <p className="muted">
          Connect your own accounts once and every finished clip gets a Post button for them.
        </p>
        <SocialConnections />
      </section>
      {admin && (
        <section className="settings-section">
          <h3>Admin</h3>
          <p className="muted">
            The machine room: everything here runs on this server, locally — models, computer
            settings, accounts, and the platform app keys. Only admins see this.
          </p>
          {admin}
        </section>
      )}
    </div>
  );
}

/** The circle in the corner: your picture, your menu. */
function UserMenu({
  user,
  onSettings,
  onSignOut,
}: {
  user: User | null;
  onSettings: () => void;
  onSignOut: () => void;
}) {
  if (!user) return null;
  const label = user.display_name || user.username;
  const items: MenuItem[] = [
    { label: "Settings", onSelect: onSettings },
    "separator",
    { label: "Sign out", onSelect: onSignOut },
  ];
  return (
    <button
      className="user-avatar"
      title={`${label} — account menu`}
      aria-haspopup="menu"
      onClick={(e) => openMenu(e, items, label)}
      onContextMenu={(e) => openMenu(e, items, label)}
    >
      {user.avatar_media_id ? (
        <img src={api.mediaFileUrl(user.avatar_media_id)} alt="" />
      ) : (
        <span>{(label[0] ?? "?").toUpperCase()}</span>
      )}
    </button>
  );
}

/** Name, picture, password — the person's own corner of Settings. */
function ProfileSection({
  user,
  media,
  onUpload,
  onRefresh,
}: {
  user: User | null;
  media: MediaAsset[];
  onUpload: (f: File, onProgress?: (fraction: number) => void) => Promise<MediaAsset>;
  onRefresh: () => Promise<void>;
}) {
  const [name, setName] = useState(user?.display_name ?? "");
  const [note, setNote] = useState<string | null>(null);
  const [pw, setPw] = useState({ current: "", next: "" });
  const [pwNote, setPwNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  if (!user) return null;
  return (
    <section className="settings-section">
      <h3>Profile</h3>
      <p className="muted">Who you are on this Kinder.</p>
      <div className="profile-row">
        <button
          className="user-avatar large"
          title="Change your picture"
          onClick={() => document.getElementById("avatar-input")?.click()}
        >
          {user.avatar_media_id ? (
            <img src={api.mediaFileUrl(user.avatar_media_id)} alt="" />
          ) : (
            <span>{((user.display_name || user.username)[0] ?? "?").toUpperCase()}</span>
          )}
        </button>
        <input
          id="avatar-input"
          type="file"
          accept="image/png,image/jpeg,image/webp"
          hidden
          onChange={async (e) => {
            const f = e.target.files?.[0];
            e.target.value = "";
            if (!f) return;
            setBusy(true);
            try {
              const asset = await onUpload(f);
              await api.updateProfile({ avatar_media_id: asset.id });
              await onRefresh();
              window.location.reload();
            } catch (err) {
              setNote(errorMessage(err));
            } finally {
              setBusy(false);
            }
          }}
        />
        <div className="profile-fields">
          <label>
            Display name
            <div className="profile-name-row">
              <input value={name} maxLength={80} placeholder={user.username} onChange={(e) => setName(e.target.value)} />
              <button
                className="primary compact"
                disabled={busy}
                onClick={async () => {
                  try {
                    await api.updateProfile({ display_name: name });
                    setNote("Saved.");
                    playSfx("confirm");
                  } catch (err) {
                    setNote(errorMessage(err));
                  }
                }}
              >
                Save
              </button>
            </div>
          </label>
          <span className="muted small">Signed in as <strong>{user.username}</strong>{user.is_admin ? " · admin" : ""}.</span>
          {user.avatar_media_id && (
            <button
              className="text-button inline"
              onClick={async () => {
                await api.updateProfile({ clear_avatar: true });
                window.location.reload();
              }}
            >
              Remove picture
            </button>
          )}
          {note && <span className="muted small">{note}</span>}
        </div>
      </div>
      <details className="password-change">
        <summary>Change password</summary>
        <form
          className="profile-fields"
          onSubmit={async (e) => {
            e.preventDefault();
            setPwNote(null);
            try {
              await api.changePassword(pw.current, pw.next);
              setPw({ current: "", next: "" });
              setPwNote("Changed. Use the new one next time you sign in.");
              playSfx("confirm");
            } catch (err) {
              setPwNote(errorMessage(err));
            }
          }}
        >
          <label>
            Current password
            <input type="password" autoComplete="current-password" value={pw.current} onChange={(e) => setPw({ ...pw, current: e.target.value })} />
          </label>
          <label>
            New password (10+ characters)
            <input type="password" autoComplete="new-password" value={pw.next} onChange={(e) => setPw({ ...pw, next: e.target.value })} />
          </label>
          <button className="primary compact" type="submit" disabled={!pw.current || pw.next.length < 10}>
            Change password
          </button>
          {pwNote && <span className="muted small">{pwNote}</span>}
        </form>
      </details>
    </section>
  );
}

/** Upload a TTF/OTF once; it appears in every font picker and in exports. */
function YourFonts() {
  const [fonts, setFonts] = useState<{ id: string; family: string }[]>([]);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const load = () => api.fonts().then((r) => setFonts(r.fonts)).catch(() => undefined);
  useEffect(() => {
    void load();
  }, []);
  return (
    <div className="branding-clips your-fonts">
      <div className="show-artwork-text">
        <strong>Your fonts</strong>
        <span className="muted">
          Upload a font file (TTF or OTF) and it appears in the Studio's font pickers and in your
          exports. Check the font's licence allows video use.
        </span>
        {note && <span className="error">{note}</span>}
      </div>
      {fonts.map((f) => (
        <div key={f.id} className="branding-slot">
          <span className="font-sample" style={{ fontFamily: `"${f.family}", Inter, sans-serif` }}>{f.family}</span>
          <button
            className="ghost compact"
            onClick={async () => {
              await api.deleteFont(f.id).catch(() => undefined);
              await load();
            }}
          >
            Remove
          </button>
        </div>
      ))}
      <label className={`ghost compact font-upload${busy ? " disabled" : ""}`}>
        {busy ? "Uploading…" : "Upload a font"}
        <input
          type="file"
          accept=".ttf,.otf,font/ttf,font/otf"
          hidden
          disabled={busy}
          onChange={async (e) => {
            const f = e.target.files?.[0];
            e.target.value = "";
            if (!f) return;
            setBusy(true);
            setNote(null);
            try {
              await api.addFont(f);
              await load();
              window.location.reload();
            } catch (err) {
              setNote(errorMessage(err));
            } finally {
              setBusy(false);
            }
          }}
        />
      </label>
    </div>
  );
}

/** Every platform: its state for this person, and Connect / Disconnect. */
function SocialConnections() {
  const [rows, setRows] = useState<
    { key: string; label: string; posts: string; configured: boolean; connected: boolean; name: string }[]
  >([]);
  const [note, setNote] = useState<string | null>(null);
  const load = () => api.socialAccounts().then((r) => setRows(r.accounts)).catch(() => undefined);
  useEffect(() => {
    void load();
  }, []);
  if (!rows.length) return null;
  async function connect(key: string) {
    try {
      const { url } = key === "youtube" ? await api.youtubeConnect() : await api.socialConnect(key);
      window.location.href = url;
    } catch (e) {
      setNote(errorMessage(e));
    }
  }
  async function drop(key: string) {
    if (key === "youtube") await api.youtubeDisconnect();
    else await api.socialDisconnect(key);
    await load();
  }
  return (
    <div className="connections">
      {rows.map((r) => (
        <div key={r.key} className="connection-row">
          <div className="connection-text">
            <strong>{r.label}</strong>
            <small className="muted">
              {r.connected
                ? `Connected${r.name ? ` as ${r.name}` : ""} — posts ${r.posts}`
                : r.configured
                  ? `Not connected — posts ${r.posts}`
                  : "Not set up yet — an admin adds this platform's app keys below"}
            </small>
          </div>
          {r.connected ? (
            <button className="ghost compact" onClick={() => void drop(r.key)}>Disconnect</button>
          ) : (
            <button className="primary compact" disabled={!r.configured} onClick={() => void connect(r.key)}>
              Connect
            </button>
          )}
        </div>
      ))}
      {note && <p className="muted small error">{note}</p>}
    </div>
  );
}

/** The templates strip on Home: try ours or save your own. */
function HomeTemplates({
  saved,
  onCreate,
  onTemplates,
}: {
  saved: SavedTemplate[];
  onCreate: () => void;
  onTemplates: () => void;
}) {
  const [tab, setTab] = useState<"all" | "saved">("all");
  return (
    <section className="home-templates">
      <div className="section-bar">
        <div>
          <span className="kicker">Templates</span>
          <h2>Try ours or save your own</h2>
        </div>
        <div className="pill-tabs" role="tablist">
          <button className={tab === "all" ? "on" : ""} onClick={() => setTab("all")}>All</button>
          <button className={tab === "saved" ? "on" : ""} onClick={() => setTab("saved")}>
            Saved{saved.length ? ` · ${saved.length}` : ""}
          </button>
        </div>
      </div>
      {tab === "all" ? (
        <div className="home-template-row">
          {templates.slice(0, 6).map((t) => (
            <button key={t.id} className="home-template-tile" title={`${t.name} — ${t.style}`} onClick={onCreate}>
              <TemplateThumb template={t} />
              <small>{t.name}</small>
            </button>
          ))}
          <button className="home-template-more" onClick={onTemplates}>
            All looks <ChevronRight size={14} />
          </button>
        </div>
      ) : saved.length ? (
        <div className="home-template-row">
          {saved.slice(0, 6).map((t) => (
            <button key={t.id} className="home-template-tile" onClick={onTemplates}>
              <span
                className="saved-swatch"
                style={{
                  background: typeof t.scene.background === "string" ? t.scene.background : "#0B0D11",
                  color: typeof t.scene.accent === "string" ? t.scene.accent : "#89CFF0",
                }}
              >
                Aa
              </span>
              <small>{t.name}</small>
            </button>
          ))}
          <button className="home-template-more" onClick={onTemplates}>
            Manage <ChevronRight size={14} />
          </button>
        </div>
      ) : (
        <p className="muted">
          Nothing saved yet. In the Studio, save the look you have made and it appears here for
          every future episode.
        </p>
      )}
    </section>
  );
}

/** Seconds as "m:ss.s" — what somebody reads off a player, not "83.5". */
function clockText(seconds: number): string {
  const total = Math.max(0, seconds);
  const minutes = Math.floor(total / 60);
  const rest = total - minutes * 60;
  return `${minutes}:${rest < 10 ? "0" : ""}${rest.toFixed(1)}`;
}

/** Reads "1:23.5", "1:23", "83.5" or "83" — whatever somebody types. */
function parseClock(text: string): number | null {
  const trimmed = text.trim();
  if (!trimmed) return null;
  const parts = trimmed.split(":");
  if (parts.length > 3 || parts.some((p) => p === "" || !/^[0-9.]+$/.test(p))) return null;
  const numbers = parts.map(Number);
  if (numbers.some((n) => !Number.isFinite(n))) return null;
  return numbers.reduce((acc, n) => acc * 60 + n, 0);
}

/** What a source row says about itself, from its jobs rather than a guess. */
function sourceState(
  m: MediaAsset,
  jobs: Job[],
): { kind: "ready" | "working" | "failed" | "waiting"; text: string } {
  if (m.has_transcript) return { kind: "ready", text: "Transcript ready" };
  const own = jobs.filter((j) => j.subject_id === m.id);
  const transcribe = own.find((j) => j.kind === "transcribe");
  if (transcribe && ["queued", "running"].includes(transcribe.status)) {
    return {
      kind: "working",
      text: transcribe.status === "running"
        ? `Transcribing… ${Math.round(transcribe.progress)}%`
        : "Waiting to transcribe",
    };
  }
  if (own.some((j) => ["queued", "running"].includes(j.status))) {
    return { kind: "working", text: "Analyzing audio…" };
  }
  if (transcribe?.status === "failed") {
    return { kind: "failed", text: "Transcription failed" };
  }
  return { kind: "failed", text: "Not transcribed yet" };
}

// Family names as the @font-face rules declare them. Mirrors FONTS in
// backend/app/services/scene.py.
// Uploaded fonts, populated by FontFaces below so previews can use them.
const customFontFamilies: Record<string, string> = {};

/** Loads the person's fonts into the page: an @font-face per font, and the
 * family map the canvas preview reads. */
function FontFaces() {
  useEffect(() => {
    let stale = false;
    api.fonts().then((r) => {
      if (stale) return;
      const css = r.fonts
        .map((f) => `@font-face { font-family: "${f.family.replace(/"/g, "")}"; src: url("${api.fontFileUrl(f.id)}"); font-display: swap; }`)
        .join("\n");
      let tag = document.getElementById("kinder-user-fonts") as HTMLStyleElement | null;
      if (!tag) {
        tag = document.createElement("style");
        tag.id = "kinder-user-fonts";
        document.head.appendChild(tag);
      }
      tag.textContent = css;
      for (const f of r.fonts) customFontFamilies[f.id] = f.family;
    }).catch(() => undefined);
    return () => {
      stale = true;
    };
  }, []);
  return null;
}

const familyOf = (fontId: string): string =>
  FONT_FAMILIES[fontId] ?? customFontFamilies[fontId] ?? "Inter";

const FONT_FAMILIES: Record<string, string> = {
  inter: "Inter",
  manrope: "Manrope",
  sora: "Sora",
  bebas: "Bebas Neue",
  dejavu: "DejaVu Sans",
};

/**
 * What a text layer will say, as far as the preview can know.
 *
 * The render resolves {{episode}}, {{show}} and friends from the feed; the
 * browser only has the project. The project's title is the episode's title
 * for anything a feed imported, which covers the default layer, and any
 * token it cannot fill is dropped the way the render drops it.
 */
function previewTokens(text: string, title: string): string {
  return text
    .replace(/\{\{\s*(episode|title)\s*\}\}/gi, title)
    .replace(/\{\{\s*[a-z_]+\s*\}\}/gi, "")
    .replace(/\s{2,}/g, " ")
    .replace(/^[\s\-–—·:|]+|[\s\-–—·:|]+$/g, "");
}

function LayerContent({
  layer,
  title,
  media,
  accent,
  peaks,
  caption,
  captionPreset,
  live = null,
  waveStyle = "pulse",
  captionScale = 1,
}: {
  layer: Layer;
  title: string;
  media: MediaAsset | null;
  accent: string;
  peaks: number[];
  caption?: string | null;
  captionPreset?: string;
  live?: LiveBars | null;
  waveStyle?: string;
  captionScale?: number;
}) {
  if (layer.type === "artwork")
    return layer.mediaId ? (
      <img
        draggable={false}
        className="artwork-image"
        style={{ borderRadius: `${(layer.radius ?? 0) * 100}%` }}
        src={api.mediaFileUrl(layer.mediaId)}
        alt=""
      />
    ) : (
      <div className="artwork-placeholder">
        <AudioLines size={34} />
      </div>
    );
  if (layer.type === "waveform")
    return waveStyle === "none" ? (
      // The export draws nothing for this style; a preview still showing
      // bars told the user the setting was broken.
      <div className="mini-wave" />
    ) : (
      <div className="mini-wave">
        <WaveformCanvas
          peaks={peaks}
          ready={peaks.length > 0}
          color={layer.color ?? accent}
          className="waveform-canvas"
          live={live}
          styleId={waveStyle}
        />
      </div>
    );
  if (layer.type === "captions")
    return (
      <span
        className="layer-caption"
        data-preset={captionPreset ?? "social"}
        style={captionScale !== 1 ? { fontSize: `${captionScale * 100}%` } : undefined}
      >
        {caption ?? layer.text ?? "Captions appear here as the clip plays."}
      </span>
    );
  if (layer.type === "title")
    return (
      <span
        className="layer-title"
        style={{
          textAlign: (layer.align as "left" | "center" | "right") ?? "center",
          width: "100%",
          display: "block",
          fontSize: `${((layer.fontScale as number) ?? 1) * 100}%`,
        }}
      >
        {previewTokens(layer.text ?? title, title)}
      </span>
    );
  return null;
}
function Timeline({
  project,
  playhead,
  setPlayhead,
  layers,
  jobs,
  duration,
  onLayerTimingChange,
}: {
  project: Project | null;
  playhead: number;
  setPlayhead: (n: number) => void;
  layers: Layer[];
  jobs: Job[];
  duration: number;
  onLayerTimingChange: (id: string, updates: Partial<Layer>) => void;
}) {
  const clipStart = project?.clip_start ?? 0;
  const localPlayhead = Math.max(0, playhead - clipStart);
  const tracksRef = useRef<HTMLDivElement>(null);

  // The visible slice of the clip. A forty-five second clip across 700px is
  // fifteen pixels a second, which is not enough to place a caption block
  // against a word. Every position below is expressed through this window
  // rather than against the whole duration, so zoom is one transform rather
  // than a special case in each of them.
  const [view, setView] = useState<{ start: number; end: number } | null>(null);
  const viewStart = view ? Math.max(0, view.start) : 0;
  const viewEnd = view ? Math.min(duration, view.end) : duration;
  const viewSpan = Math.max(0.5, viewEnd - viewStart);
  const zoomed = viewSpan < duration - 0.01;

  /** Where a time sits in the visible window, as a percentage. */
  const pct = (time: number) => ((time - viewStart) / viewSpan) * 100;
  /** What time a pointer is over. */
  const timeAt = (clientX: number, rect: DOMRect) =>
    Math.max(
      0,
      Math.min(duration, viewStart + ((clientX - rect.left) / rect.width) * viewSpan),
    );

  function zoomBy(factor: number, anchor?: number) {
    const centre = anchor ?? viewStart + viewSpan / 2;
    const span = Math.max(1, Math.min(duration, viewSpan * factor));
    if (span >= duration - 0.01) {
      setView(null);
      return;
    }
    let start = centre - span / 2;
    start = Math.max(0, Math.min(duration - span, start));
    setView({ start, end: start + span });
  }

  function pan(seconds: number) {
    if (!zoomed) return;
    const start = Math.max(0, Math.min(duration - viewSpan, viewStart + seconds));
    setView({ start, end: start + viewSpan });
  }

  // Ruler marks that follow the window rather than the whole clip.
  const marks = Array.from({ length: 5 }, (_, i) => viewStart + (viewSpan / 4) * i);

  /**
   * Drag a layer's block to retime it.
   *
   * The block used to be inert: its click handler wrote back exactly the values
   * it already had, so the timeline looked editable and changed nothing.
   */
  function beginTimingDrag(
    event: React.PointerEvent,
    layer: Layer,
    mode: "move" | "start" | "end",
  ) {
    // Otherwise the tracks container also handles this and jumps the playhead.
    event.stopPropagation();
    event.preventDefault();
    const container = tracksRef.current;
    if (!container || layer.locked) return;

    const rect = container.getBoundingClientRect();
    const at = (clientX: number) => timeAt(clientX, rect);
    const origin = at(event.clientX);
    const from = Math.max(0, layer.startTime ?? 0);
    const to = Math.min(duration, Math.max(from + 0.5, layer.endTime ?? duration));
    const target = event.currentTarget as HTMLElement;
    try { target.setPointerCapture(event.pointerId); } catch { /* best-effort */ }

    const onMove = (moveEvent: PointerEvent) => {
      const point = at(moveEvent.clientX);
      if (mode === "start") {
        onLayerTimingChange(layer.id, {
          startTime: Math.max(0, Math.min(point, to - 0.5)),
          endTime: to,
        });
      } else if (mode === "end") {
        onLayerTimingChange(layer.id, {
          startTime: from,
          endTime: Math.min(duration, Math.max(from + 0.5, point)),
        });
      } else {
        const span = to - from;
        const nextStart = Math.max(0, Math.min(duration - span, from + (point - origin)));
        onLayerTimingChange(layer.id, {
          startTime: nextStart,
          endTime: nextStart + span,
        });
      }
    };
    const onUp = () => {
      target.releasePointerCapture(event.pointerId);
      target.removeEventListener("pointermove", onMove);
      target.removeEventListener("pointerup", onUp);
      target.removeEventListener("pointercancel", onUp);
    };
    target.addEventListener("pointermove", onMove);
    target.addEventListener("pointerup", onUp);
    target.addEventListener("pointercancel", onUp);
  }

  return (
    <div className="timeline">
      <div className="timeline-head">
        <strong>Timeline</strong>
        <span>
          {formatTime(localPlayhead)} / {formatTime(duration)}
        </span>
        <span className="timeline-zoom">
          <button
            title="Zoom out"
            disabled={!zoomed}
            onClick={() => zoomBy(2, localPlayhead)}
          >
            <Minus size={13} />
          </button>
          <button title="Zoom in" onClick={() => zoomBy(0.5, localPlayhead)}>
            <ZoomIn size={13} />
          </button>
          {zoomed && (
            <>
              <button title="Scroll left" onClick={() => pan(-viewSpan / 3)}>
                <ChevronLeft size={13} />
              </button>
              <button title="Scroll right" onClick={() => pan(viewSpan / 3)}>
                <ChevronRight size={13} />
              </button>
              <button className="timeline-fit" onClick={() => setView(null)}>
                Fit
              </button>
            </>
          )}
        </span>
      </div>
      <div className="timeline-body">
        <div className="track-labels">
          {layers
            .slice()
            .reverse()
            .map((l) => (
              <span key={l.id}>{l.name}</span>
            ))}
        </div>
        <div
          className="tracks"
          ref={tracksRef}
          onClick={(e) => {
            const r = e.currentTarget.getBoundingClientRect();
            setPlayhead(clipStart + timeAt(e.clientX, r));
          }}
          onWheel={(e) => {
            // Ctrl-wheel zooms about the pointer, which is what every timeline
            // does; a plain wheel scrolls, but only once there is somewhere to
            // scroll to, so the page still scrolls when the clip fits.
            const r = e.currentTarget.getBoundingClientRect();
            if (e.ctrlKey || e.metaKey) {
              e.preventDefault();
              zoomBy(e.deltaY > 0 ? 1.25 : 0.8, timeAt(e.clientX, r));
            } else if (zoomed && Math.abs(e.deltaX) > Math.abs(e.deltaY)) {
              e.preventDefault();
              pan((e.deltaX / r.width) * viewSpan);
            }
          }}
        >
          <div className="ruler">
            {marks.map((n) => (
              <span key={n} style={{ left: `${pct(n)}%` }}>
                {formatTime(n)}
              </span>
            ))}
          </div>
          {layers
            .slice()
            .reverse()
            .map((layer) => {
              const starts = Math.max(0, layer.startTime ?? 0);
              const ends = Math.min(duration, Math.max(starts + 0.5, layer.endTime ?? duration));
              return (
                <div className="track" key={layer.id}>
                  <div
                    className="track-block"
                    style={{
                      left: `${pct(starts)}%`,
                      width: `${Math.max(0.4, ((ends - starts) / viewSpan) * 100)}%`,
                    }}
                    onPointerDown={(e) => beginTimingDrag(e, layer, "move")}
                    title={`${formatTime(starts)} – ${formatTime(ends)}`}
                  >
                    <span
                      className="track-handle start"
                      onPointerDown={(e) => beginTimingDrag(e, layer, "start")}
                    />
                    <b>{layer.name}</b>
                    <span
                      className="track-handle end"
                      onPointerDown={(e) => beginTimingDrag(e, layer, "end")}
                    />
                  </div>
                </div>
              );
            })}
          <div className="playhead" style={{ left: `${pct(localPlayhead)}%` }} />
        </div>
      </div>
    </div>
  );
}
function ProjectBrowser({
  projects,
  onOpen,
  onDelete,
  onRename,
  onRefreshAll,
}: {
  projects: Project[];
  onOpen: (p: Project) => void;
  onDelete: (p: Project) => Promise<void>;
  onRename: (p: Project, title: string) => Promise<void>;
  onRefreshAll: () => Promise<void>;
}) {
  const [trashOpen, setTrashOpen] = useState(false);
  const menuFor = (p: Project) =>
    projectMenu(p, {
      open: () => onOpen(p),
      rename: (title) => onRename(p, title),
      remove: onDelete,
    });
  // The confirmation opens exactly where the click happened — an anchored
  // menu, not a control that appears somewhere else on the card.
  const confirmDelete = (e: React.MouseEvent, p: Project) =>
    openMenu(e, [
      {
        label: "Move to trash",
        hint: "kept 7 days",
        danger: true,
        onSelect: () => void onDelete(p),
      },
      { label: "Keep it", onSelect: () => undefined },
    ], p.title);
  return (
    <div className="library-page">
      <div className="page-heading">
        <div>
          <span className="kicker">Library</span>
          <h2>Projects</h2>
          <p>Everything you create stays available on this server.</p>
        </div>
        <button
          className="ghost compact trash-link"
          onClick={() => {
            setTrashOpen(true);
            window.setTimeout(
              () => document.querySelector(".trash-section")?.scrollIntoView({ behavior: "smooth", block: "center" }),
              50,
            );
          }}
        >
          <Trash2 size={14} /> Trash
        </button>
      </div>
      <div className="library-grid">
        {projects.map((p) => (
          <div
            key={p.id}
            className="library-card"
            onContextMenu={(e) => openMenu(e, menuFor(p), p.title)}
          >
            <button onClick={() => onOpen(p)}>
              <Poster projectId={p.id} ratio={p.aspect_ratio} icon={27} rendered={p.rendered} />
              <strong>{p.title}</strong>
              <small>
                {p.aspect_ratio} · {clockText(p.clip_end - p.clip_start)} · <em>Open in Studio</em>
              </small>
              {typeof p.scene?.pickReason === "string" && p.scene.pickReason && (
                <small className="pick-reason" title="Why Kinder chose this moment">
                  <Sparkles size={11} /> {p.scene.pickReason}
                </small>
              )}
            </button>
            <div className="card-actions">
              <MenuButton items={menuFor(p)} title={p.title} />
              <button
                className="icon-button danger"
                title={`Delete ${p.title} (goes to the trash, kept 7 days)`}
                onClick={(e) => confirmDelete(e, p)}
              >
                <Trash2 size={15} />
              </button>
            </div>
          </div>
        ))}
      </div>
      <TrashSection onChanged={onRefreshAll} open={trashOpen} onToggle={setTrashOpen} />
    </div>
  );
}

/** Who opened the clips you shared. Counted by the share pages only. */
function AnalyticsPage() {
  const [data, setData] = useState<Awaited<ReturnType<typeof api.analytics>> | null>(null);
  useEffect(() => {
    api.analytics().then(setData).catch(() => undefined);
  }, []);
  const when = (iso: string | null) => {
    if (!iso) return "";
    const d = new Date(iso);
    const days = Math.floor((Date.now() - d.getTime()) / 86400000);
    if (days <= 0) return "today";
    if (days === 1) return "yesterday";
    if (days < 7) return `${days} days ago`;
    return d.toLocaleDateString(undefined, { dateStyle: "medium" });
  };
  return (
    <div className="library-page">
      <div className="page-heading">
        <div>
          <span className="kicker">Reach</span>
          <h2>Analytics</h2>
          <p>What happened to the clips you shared. Nobody is tracked — just opens and plays.</p>
        </div>
      </div>
      {!data ? (
        <p className="muted">Loading…</p>
      ) : data.clips.length === 0 ? (
        <div className="empty-state">
          <ChartColumn size={28} />
          <strong>Nothing to count yet.</strong>
          <span>
            Press Copy link on a finished clip and send it to someone — every open and every play
            of that page shows up here.
          </span>
        </div>
      ) : (
        <>
          <div className="stat-cards">
            <div className="stat-card"><strong>{data.totals.views}</strong><span>page opens</span></div>
            <div className="stat-card"><strong>{data.totals.plays}</strong><span>plays</span></div>
            <div className="stat-card"><strong>{data.totals.links_live}</strong><span>links live</span></div>
          </div>
          <div className="analytics-list">
            {data.clips.map((c) => (
              <div key={c.project_id} className="analytics-row">
                {c.rendered ? (
                  <img className="export-poster" src={api.posterUrl(c.project_id)} alt="" />
                ) : (
                  <div className="export-icon"><Film size={18} /></div>
                )}
                <div className="analytics-title">
                  <strong>{c.title}</strong>
                  <small className="muted">
                    last opened {when(c.last)}{c.link_live ? "" : " · link turned off"}
                  </small>
                </div>
                <span className="analytics-nums" title="Page opens / plays">
                  {c.views} <small>opens</small> · {c.plays} <small>plays</small>
                </span>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

/** The Trash as its own page, plain as a bin under a desk. */
function TrashPage({ onChanged }: { onChanged: () => Promise<void> }) {
  const [items, setItems] = useState<Project[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const load = () => api.trashedProjects().then((r) => setItems(r.projects)).catch(() => setItems([]));
  useEffect(() => {
    void load();
  }, []);
  return (
    <div className="library-page">
      <div className="page-heading">
        <div>
          <span className="kicker">Library</span>
          <h2>Trash</h2>
          <p>Deleted projects wait here for 7 days, then they are gone for good.</p>
        </div>
      </div>
      {items === null ? (
        <p className="muted">Loading…</p>
      ) : items.length === 0 ? (
        <div className="empty-state">
          <Trash2 size={28} />
          <strong>The trash is empty.</strong>
          <span>Delete a project anywhere and it lands here, restorable for a week.</span>
        </div>
      ) : (
        <div className="trash-list page">
          {items.map((p) => (
            <div key={p.id} className="trash-row">
              <span>{p.title}</span>
              <div className="trash-actions">
                <button
                  className="primary compact"
                  disabled={busy === p.id}
                  onClick={async () => {
                    setBusy(p.id);
                    await api.restoreFromTrash(p.id).catch(() => undefined);
                    setBusy(null);
                    await load();
                    await onChanged();
                  }}
                >
                  Put back
                </button>
                <button
                  className="ghost compact danger"
                  disabled={busy === p.id}
                  onClick={async () => {
                    if (!window.confirm(`Delete "${p.title}" forever? This one cannot be undone.`)) return;
                    setBusy(p.id);
                    await api.deleteProject(p.id).catch(() => undefined);
                    setBusy(null);
                    await load();
                  }}
                >
                  Delete forever
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/** What was deleted in the last week, and the way back. */
function TrashSection({
  onChanged,
  open,
  onToggle,
}: {
  onChanged: () => Promise<void>;
  open: boolean;
  onToggle: (open: boolean) => void;
}) {
  const [items, setItems] = useState<Project[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const load = () => api.trashedProjects().then((r) => setItems(r.projects)).catch(() => undefined);
  useEffect(() => {
    void load();
  }, []);
  // Re-check when it is opened: something may have been deleted since load.
  useEffect(() => {
    if (open) void load();
  }, [open]);
  return (
    <section className="trash-section">
      <button className="trash-head" onClick={() => onToggle(!open)}>
        <Trash2 size={14} /> Trash{items.length ? ` · ${items.length}` : ""} — deleted projects wait here for 7 days
        <small>{open ? "Hide" : "Show"}</small>
      </button>
      {open && items.length === 0 && (
        <p className="muted small">The trash is empty. Delete a project and it lands here, restorable for a week.</p>
      )}
      {open && items.length > 0 && (
        <div className="trash-list">
          {items.map((p) => (
            <div key={p.id} className="trash-row">
              <span>{p.title}</span>
              <div className="trash-actions">
                <button
                  className="ghost compact"
                  disabled={busy === p.id}
                  onClick={async () => {
                    setBusy(p.id);
                    await api.restoreFromTrash(p.id).catch(() => undefined);
                    setBusy(null);
                    await load();
                    await onChanged();
                  }}
                >
                  Put back
                </button>
                <button
                  className="ghost compact danger"
                  disabled={busy === p.id}
                  onClick={async () => {
                    if (!window.confirm(`Delete "${p.title}" forever? This one cannot be undone.`)) return;
                    setBusy(p.id);
                    await api.deleteProject(p.id).catch(() => undefined);
                    setBusy(null);
                    await load();
                  }}
                >
                  Delete forever
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
/** Save this look for later episodes, or drop a saved one onto this clip.
 *
 * Kept next to Variants because both answer the same question — "do this
 * again, somewhere else" — one across shapes and one across weeks.
 */
/** Where this clip can actually be posted.
 *
 * Every platform has its own ceiling on length and file size and its own list
 * of shapes it will take, and an export that misses one fails at the upload
 * step — after the render, after the wait, usually on a phone. This moves that
 * discovery to before the GPU time is spent.
 */
/** Turn one episode into a set of clips in a single action.
 *
 * Everything upstream exists for this: the suggestions know where the good
 * moments are, snapping keeps the cuts off the middle of words, and four render
 * lanes run at once. Doing it one clip at a time is the part of the job that
 * makes people stop bothering.
 */
/**
 * The show's cover picture. Feed episodes bring their own; an uploaded file
 * has nothing, so every clip from it came out on a flat colour. Choose one
 * picture here and every upload — past and future — gets it as a background.
 */
function ShowArtwork({
  media,
  onUpload,
  onRefresh,
}: {
  media: MediaAsset[];
  onUpload: (f: File, onProgress?: (fraction: number) => void) => Promise<MediaAsset>;
  onRefresh: () => Promise<void>;
}) {
  const [artworkId, setArtworkId] = useState<string | null | undefined>(undefined);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  useEffect(() => {
    api.showArtwork().then((r) => setArtworkId(r.media_id)).catch(() => setArtworkId(null));
  }, []);
  const images = media.filter((m) => m.content_type.startsWith("image/"));
  const current = images.find((m) => m.id === artworkId) ?? null;

  async function choose(id: string | null) {
    setBusy(true);
    setNote(null);
    try {
      const r = await api.setShowArtwork(id);
      setArtworkId(r.media_id);
      if (id) setNote(r.applied_to ? `Added to ${r.applied_to} episode${r.applied_to === 1 ? "" : "s"} you had already uploaded.` : "Every new upload will use it.");
      playSfx("confirm");
      await onRefresh();
    } catch (error) {
      setNote(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  async function pick(file: File) {
    setBusy(true);
    try {
      const asset = await onUpload(file);
      await choose(asset.id);
    } catch (error) {
      setNote(errorMessage(error));
      setBusy(false);
    }
  }

  if (artworkId === undefined) return null;
  return (
    <div className="show-artwork">
      {current ? (
        <img src={api.mediaFileUrl(current.id)} alt="" />
      ) : (
        <span className="show-artwork-empty"><ImageIcon size={18} /></span>
      )}
      <div className="show-artwork-text">
        <strong>{current ? "Your show's cover picture" : "Add your show's cover picture"}</strong>
        <span className="muted">
          {current
            ? "Every uploaded episode uses it as the blurred background."
            : "Uploaded episodes have no picture of their own — add one and every clip gets it as a background."}
        </span>
        {note && <span className="muted">{note}</span>}
      </div>
      <div className="show-artwork-actions">
        <label className={`ghost compact${busy ? " disabled" : ""}`}>
          {current ? "Change" : "Choose picture"}
          <input
            type="file"
            accept="image/png,image/jpeg,image/webp"
            hidden
            disabled={busy}
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) void pick(f);
              e.target.value = "";
            }}
          />
        </label>
        {!current && images.length > 0 && (
          <select
            disabled={busy}
            defaultValue=""
            onChange={(e) => e.target.value && void choose(e.target.value)}
          >
            <option value="">Use one I uploaded…</option>
            {images.map((m) => (
              <option key={m.id} value={m.id}>{m.original_name}</option>
            ))}
          </select>
        )}
        {current && (
          <button className="ghost compact" disabled={busy} onClick={() => void choose(null)}>
            Remove
          </button>
        )}
      </div>
    </div>
  );
}

/**
 * The show's intro and outro: short videos joined onto the front and back
 * of every export. Chosen once, per person, like the cover picture.
 */
function BrandingClips({
  media,
  onUpload,
  onRefresh,
}: {
  media: MediaAsset[];
  onUpload: (f: File, onProgress?: (fraction: number) => void) => Promise<MediaAsset>;
  onRefresh: () => Promise<void>;
}) {
  const [ids, setIds] = useState<{ intro: string | null; outro: string | null } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  useEffect(() => {
    api.branding().then(setIds).catch(() => setIds(null));
  }, []);
  if (!ids) return null;
  const videos = media.filter((m) => m.content_type.startsWith("video/"));
  const nameOf = (id: string | null) => media.find((m) => m.id === id)?.original_name ?? null;

  async function choose(role: "intro" | "outro", mediaId: string | null) {
    setBusy(role);
    setNote(null);
    try {
      setIds(await api.setBranding(role, mediaId));
      playSfx("confirm");
    } catch (error) {
      setNote(errorMessage(error));
    } finally {
      setBusy(null);
    }
  }

  async function pick(role: "intro" | "outro", file: File) {
    setBusy(role);
    setNote(null);
    try {
      const asset = await onUpload(file);
      await onRefresh();
      setIds(await api.setBranding(role, asset.id));
      playSfx("confirm");
    } catch (error) {
      setNote(errorMessage(error));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="branding-clips">
      <div className="show-artwork-text">
        <strong>Intro &amp; outro</strong>
        <span className="muted">
          Short videos (up to 15 s) joined onto the start and end of every clip you export — a
          logo sting, a “follow the show” card.
        </span>
        {note && <span className="muted error">{note}</span>}
      </div>
      {(["intro", "outro"] as const).map((role) => (
        <div key={role} className="branding-slot">
          <span className="branding-role">{role === "intro" ? "Intro" : "Outro"}</span>
          {ids[role] ? (
            <>
              <span className="muted branding-name">{nameOf(ids[role]) ?? "chosen"}</span>
              <button className="ghost compact" disabled={busy === role} onClick={() => void choose(role, null)}>
                Remove
              </button>
            </>
          ) : (
            <>
              <label className={`ghost compact${busy === role ? " disabled" : ""}`}>
                Upload video
                <input
                  type="file"
                  accept="video/mp4,video/quicktime"
                  hidden
                  disabled={busy === role}
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) void pick(role, f);
                    e.target.value = "";
                  }}
                />
              </label>
              {videos.length > 0 && (
                <select
                  disabled={busy === role}
                  defaultValue=""
                  onChange={(e) => e.target.value && void choose(role, e.target.value)}
                >
                  <option value="">Use one I uploaded…</option>
                  {videos.map((m) => (
                    <option key={m.id} value={m.id}>{m.original_name}</option>
                  ))}
                </select>
              )}
            </>
          )}
        </div>
      ))}
    </div>
  );
}

/**
 * Show notes, titles and keywords, written by the model on this box from
 * the episode's own transcript. Everything is a click away from the
 * clipboard, because the whole point is pasting it somewhere else.
 */
function ShowNotes({ mediaId }: { mediaId: string }) {
  const [state, setState] = useState<Awaited<ReturnType<typeof api.notesStatus>> | null>(null);
  const [startedAt, setStartedAt] = useState(0);
  const [copied, setCopied] = useState<string | null>(null);
  useEffect(() => {
    let stale = false;
    api.notesStatus(mediaId).then((s) => { if (!stale) setState(s); }).catch(() => undefined);
    return () => { stale = true; };
  }, [mediaId]);
  useEffect(() => {
    if (state?.status !== "working") return;
    const t = window.setInterval(() => {
      api.notesStatus(mediaId).then(setState).catch(() => undefined);
    }, 4000);
    return () => window.clearInterval(t);
  }, [state?.status, mediaId]);
  if (state === null) return null;

  async function copy(label: string, text: string) {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(label);
      window.setTimeout(() => setCopied(null), 2500);
    } catch {
      // The text is on screen; selecting it by hand still works.
    }
  }

  async function run() {
    setStartedAt(Date.now());
    try {
      await api.writeNotes(mediaId);
      setState(await api.notesStatus(mediaId));
    } catch (e) {
      setState({ status: "failed", error: errorMessage(e) });
    }
  }

  const result = state.status === "done" ? state.result : undefined;
  return (
    <section className="auto-clips show-notes">
      <div>
        <strong>Show notes &amp; keywords</strong>
        <p className="muted">
          Kinder reads the whole episode and writes title ideas, a description for your feed,
          the highlights, and keywords and hashtags for the socials.
        </p>
      </div>
      {state.status === "working" ? (
        <WorkingCard
          title="Writing your show notes"
          stage="Reading the whole episode and taking notes…"
          fraction={null}
          startedAt={startedAt || Date.now()}
          compact
        />
      ) : result ? (
        <div className="notes-result">
          <div className="notes-block">
            <span className="notes-label">Title ideas</span>
            {result.titles.map((t) => (
              <button key={t} className="notes-line" title="Copy" onClick={() => void copy(t, t)}>
                {t} {copied === t ? <Check size={12} /> : <Copy size={12} />}
              </button>
            ))}
          </div>
          <div className="notes-block">
            <span className="notes-label">Description</span>
            <button className="notes-line prose" title="Copy" onClick={() => void copy("desc", result.description)}>
              {result.description} {copied === "desc" ? <Check size={12} /> : <Copy size={12} />}
            </button>
          </div>
          <div className="notes-block">
            <span className="notes-label">Highlights</span>
            <button
              className="notes-line prose"
              title="Copy all"
              onClick={() => void copy("high", result.highlights.map((h) => `• ${h}`).join("\n"))}
            >
              <span>{result.highlights.map((h) => `• ${h}`).join(" ")}</span>{" "}
              {copied === "high" ? <Check size={12} /> : <Copy size={12} />}
            </button>
          </div>
          <div className="notes-block">
            <span className="notes-label">Keywords &amp; hashtags</span>
            <button
              className="notes-line prose"
              title="Copy all"
              onClick={() => void copy("tags", [...result.keywords, ...result.hashtags].join(" "))}
            >
              <span>{[...result.keywords, ...result.hashtags].join(" · ")}</span>{" "}
              {copied === "tags" ? <Check size={12} /> : <Copy size={12} />}
            </button>
          </div>
          <button className="ghost compact" onClick={() => void run()}>Write them again</button>
        </div>
      ) : (
        <div className="auto-clips-actions">
          <button className="primary" onClick={() => void run()}>
            <Sparkles size={15} /> Write my show notes
          </button>
          {state.status === "failed" && <small className="error">{state.error}</small>}
        </div>
      )}
    </section>
  );
}

/**
 * The "just do it for me" path: Kinder picks the best moments and renders
 * them, right from the source step. The same batch as the Studio panel,
 * with the choices already made.
 */
function AutoClips({
  mediaId,
  ratio,
  onRefresh,
  onGoToExports,
}: {
  mediaId: string;
  ratio: Ratio;
  onRefresh: () => Promise<void>;
  onGoToExports: () => void;
}) {
  const [count, setCount] = useState(6);
  const [lookId, setLookId] = useState(templates[0].id);
  const [busy, setBusy] = useState(false);
  const [startedAt, setStartedAt] = useState(0);
  const [made, setMade] = useState<number | null>(null);
  const [note, setNote] = useState<string | null>(null);

  async function run() {
    setBusy(true);
    setStartedAt(Date.now());
    setNote(null);
    setMade(null);
    try {
      const look = templates.find((t) => t.id === lookId) ?? templates[0];
      const result = await api.batchClips(mediaId, {
        count,
        aspect_ratio: ratio,
        render: true,
        template_id: null,
        look: {
          template: look.id,
          background: look.background,
          accent: look.accent,
          captionPreset: look.captionPreset,
          captionColor: look.captionColor,
          font: look.font,
          captionFont: look.captionFont,
          waveStyle: look.waveStyle,
          peakAccent: look.peakAccent,
        },
      });
      setMade(result.projects.length);
      if (result.projects.length === 0) setNote("Every good moment in this episode is already a clip.");
      playSfx("confirm");
      await onRefresh();
    } catch (error) {
      setNote(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="auto-clips">
      <div>
        <strong>Let Kinder pick the clips</strong>
        <p className="muted">
          It listens for the best moments — a strong line, a laugh, a story — cuts them, adds
          captions and your look, and makes the videos. You can still edit any of them after.
        </p>
      </div>
      {busy ? (
        <WorkingCard
          title="Finding the best moments"
          stage="Reading the whole episode and choosing what stands out…"
          fraction={null}
          startedAt={startedAt}
          compact
        />
      ) : made !== null && made > 0 ? (
        <div className="auto-clips-done">
          <strong>Making {made} clip{made === 1 ? "" : "s"}.</strong>
          <span className="muted"> They appear under Exports as each one finishes — about 30 seconds each.</span>
          <button className="primary compact" onClick={onGoToExports}>Go to Exports</button>
        </div>
      ) : (
        <div className="auto-clips-actions">
          <label>
            How many
            <select value={count} onChange={(e) => setCount(Number(e.target.value))}>
              {[3, 6, 10].map((n) => (
                <option key={n} value={n}>{n} clips</option>
              ))}
            </select>
          </label>
          <label>
            Look
            <select value={lookId} onChange={(e) => setLookId(e.target.value)}>
              {templates.map((t) => (
                <option key={t.id} value={t.id}>{t.name} — {t.style}</option>
              ))}
            </select>
          </label>
          <button className="primary" onClick={() => void run()}>
            <WandSparkles size={15} /> Make {count} clips for me
          </button>
        </div>
      )}
      {note && <p className="muted">{note}</p>}
    </section>
  );
}

function BatchClips({
  mediaId,
  templates,
  onDone,
}: {
  mediaId: string | null;
  templates: SavedTemplate[];
  onDone: () => Promise<void>;
}) {
  const [count, setCount] = useState(6);
  const [ratio, setRatio] = useState<Ratio>("9:16");
  const [templateId, setTemplateId] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  if (!mediaId) return null;

  async function run() {
    setBusy(true);
    setNote(null);
    try {
      const result = await api.batchClips(mediaId!, {
        count,
        aspect_ratio: ratio,
        render: true,
        template_id: templateId || null,
      });
      const made = result.projects.length;
      setNote(
        made === 0
          ? "Every moment worth clipping is already a project."
          : `Making ${made} clip${made === 1 ? "" : "s"}${
              result.skipped ? `, skipped ${result.skipped} already made` : ""
            }. They will appear in Exports as they finish.`,
      );
      playSfx("confirm");
      await onDone();
    } catch (error) {
      setNote(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel-block batch-panel">
      <div className="inspector-heading">
        <span className="sidebar-label">Make a set of clips</span>
      </div>
      <p className="panel-hint">
        Finds the best moments in this episode and renders them all at once.
      </p>
      <div className="batch-controls">
        <label>
          Clips
          <select value={count} onChange={(e) => setCount(Number(e.target.value))}>
            {[3, 4, 6, 8, 10, 12].map((n) => (
              <option key={n} value={n}>{n}</option>
            ))}
          </select>
        </label>
        <label>
          Shape
          <select value={ratio} onChange={(e) => setRatio(e.target.value as Ratio)}>
            {(["9:16", "4:5", "1:1", "16:9"] as Ratio[]).map((r) => (
              <option key={r} value={r}>{r}</option>
            ))}
          </select>
        </label>
        {templates.length > 0 && (
          <label>
            Look
            <select value={templateId} onChange={(e) => setTemplateId(e.target.value)}>
              <option value="">Default</option>
              {templates.map((t) => (
                <option key={t.id} value={t.id}>{t.name}</option>
              ))}
            </select>
          </label>
        )}
      </div>
      <div className="batch-actions">
        <button className="primary" disabled={busy} onClick={() => void run()}>
          <WandSparkles size={15} /> {busy ? "Working…" : `Make ${count} clips`}
        </button>
        <a className="ghost-button" href={api.batchZipUrl(mediaId)} download>
          <Download size={15} /> Download all
        </a>
      </div>
      {note && <p className="panel-note">{note}</p>}
    </section>
  );
}

function Destinations({ project, jobs }: { project: Project | null; jobs: Job[] }) {
  const [rows, setRows] = useState<Destination[] | null>(null);
  const [rendered, setRendered] = useState(false);
  const [size, setSize] = useState<number | null>(null);
  const [open, setOpen] = useState(false);

  // Re-check after a render finishes: file size is the one constraint that
  // cannot be known until the file exists.
  const renders = jobs.filter(
    (job) => job.kind === "render" && job.subject_id === project?.id,
  ).length;
  const done = jobs.filter(
    (job) =>
      job.kind === "render" &&
      job.subject_id === project?.id &&
      job.status === "complete",
  ).length;

  useEffect(() => {
    if (!project) return;
    let stale = false;
    api
      .destinations(project.id)
      .then((result) => {
        if (stale) return;
        setRows(result.destinations);
        setRendered(result.rendered);
        setSize(result.file_bytes);
      })
      .catch(() => !stale && setRows(null));
    return () => {
      stale = true;
    };
  }, [project?.id, project?.aspect_ratio, project?.clip_start, project?.clip_end, renders, done]);

  if (!project || !rows) return null;
  const ready = rows.filter((row) => row.ok);
  const blocked = rows.filter((row) => !row.ok);

  return (
    <section className="panel-block">
      <div className="inspector-heading">
        <span className="sidebar-label">Where this can go</span>
      </div>
      <button className="destinations-head" onClick={() => setOpen((v) => !v)}>
        <Share2 size={14} />
        <strong>
          {ready.length} of {rows.length} platforms
        </strong>
        <small>{open ? "Hide" : "Show"}</small>
      </button>
      <p className="panel-hint">
        {rendered
          ? `Checked against the rendered file${size ? ` (${(size / (1024 * 1024)).toFixed(1)} MB)` : ""}.`
          : "File size is checked once this has been exported."}
      </p>
      {open && (
        <div className="destination-list">
          {ready.map((row) => (
            <div key={row.platform} className="destination ok">
              <Check size={12} />
              <span>{row.label}</span>
              {row.warnings.length > 0 && <small>{row.warnings[0]}</small>}
            </div>
          ))}
          {blocked.map((row) => (
            <div key={row.platform} className="destination blocked">
              <X size={12} />
              <span>{row.label}</span>
              <small>{row.blocking[0]}</small>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function TemplatePanel({
  project,
  templates: saved,
  onSave,
  onApply,
}: {
  project: Project | null;
  templates: SavedTemplate[];
  onSave: (name: string) => Promise<void>;
  onApply: (templateId: string) => Promise<void>;
}) {
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  if (!project) return null;

  async function run(action: () => Promise<void>, done: string) {
    setBusy(true);
    setNote(null);
    try {
      await action();
      playSfx("confirm");
      setNote(done);
    } catch (error) {
      setNote(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel-block">
      <div className="inspector-heading">
        <span className="sidebar-label">Template</span>
      </div>
      <p className="panel-hint">
        Saves the look — colours, wave style, captions and layout. Your audio and
        artwork stay with the episode.
      </p>
      <div className="template-save">
        <input
          value={name}
          maxLength={128}
          placeholder="Name this look"
          onChange={(e) => setName(e.target.value)}
        />
        <button
          className="ghost-button"
          disabled={busy || name.trim().length === 0}
          onClick={() =>
            void run(async () => {
              await onSave(name.trim());
              setName("");
            }, "Saved")
          }
        >
          <Save size={15} /> Save
        </button>
      </div>
      {saved.length > 0 && (
        <div className="template-apply">
          {saved.map((t) => (
            <button
              key={t.id}
              className="chip"
              disabled={busy}
              title={`Apply ${t.name} to this project`}
              onClick={() => void run(() => onApply(t.id), `Applied ${t.name}`)}
            >
              {t.name}
            </button>
          ))}
        </div>
      )}
      {note && <p className="panel-note">{note}</p>}
    </section>
  );
}

function TemplateThumb({ template }: { template: (typeof templates)[number] }) {
  // A tile has to show the *look*, not two colour blocks: the typeface, the
  // caption plate and the bars are what differ between Frost and Neon.
  const family = FONT_FAMILIES[template.font ?? "inter"] ?? "Inter";
  const captionFamily = FONT_FAMILIES[template.captionFont ?? template.font ?? "inter"] ?? "Inter";
  const bars = [0.35, 0.7, 1, 0.55, 0.8, 0.45, 0.9, 0.6, 0.3, 0.75, 0.5, 0.65];
  return (
    <div
      className="template-thumb look"
      style={{ background: template.background, ["--accent-500" as string]: template.accent, ["--n-950" as string]: template.background } as CSSProperties}
    >
      <span className="look-title" style={{ fontFamily: family, color: template.captionColor ?? "#ffffff" }}>
        Episode Title
      </span>
      <span
        className="layer-caption look-caption"
        data-preset={template.captionPreset ?? "social"}
        style={{ fontFamily: captionFamily, color: template.captionColor ?? "#ffffff" }}
      >
        the spoken <em style={{ color: typeof template.peakAccent === "string" ? template.peakAccent : "#D4AF37" }}>word</em>
      </span>
      <span className="look-bars" aria-hidden="true">
        {bars.map((h, i) => (
          <i key={i} style={{ height: `${h * 100}%`, background: template.accent }} />
        ))}
      </span>
    </div>
  );
}
/** A thumbnail drawn from a saved scene rather than from a starter preset. */
function SavedThumb({ scene }: { scene: Record<string, unknown> }) {
  const layers = Array.isArray(scene.layers)
    ? (scene.layers as Record<string, unknown>[])
    : [];
  const accent =
    (layers.find((l) => typeof l.color === "string")?.color as string) ??
    DEFAULT_ACCENT;
  const background =
    (scene.backgroundColor as string) ??
    ((scene.backgroundImage as Record<string, unknown>)?.color as string) ??
    DEFAULT_BACKGROUND;
  return (
    <div className="template-thumb" style={{ background }}>
      <div style={{ background: accent }} />
      <span>
        Episode
        <br />
        Title
      </span>
    </div>
  );
}

function TemplateGallery({
  onUse,
  saved,
  onUseSaved,
  onDeleteSaved,
}: {
  onUse: (t: (typeof templates)[number]) => Promise<void>;
  saved: SavedTemplate[];
  onUseSaved: (t: SavedTemplate) => Promise<void>;
  onDeleteSaved: (t: SavedTemplate) => Promise<void>;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  return (
    <div className="library-page">
      <div className="page-heading">
        <span className="kicker">Template library</span>
        <h2>Templates</h2>
        <p>Scene compositions you can adapt in Studio.</p>
      </div>

      {saved.length > 0 && (
        <>
          <h3 className="section-label">Your saved looks</h3>
          <div className="template-grid gallery">
            {saved.map((t) => (
              <div key={t.id} className="saved-template">
                <button
                  disabled={busy !== null}
                  onClick={async () => {
                    setBusy(t.id);
                    playSfx("select");
                    try {
                      await onUseSaved(t);
                    } finally {
                      setBusy(null);
                    }
                  }}
                >
                  <SavedThumb scene={t.scene} />
                  <strong>{t.name}</strong>
                  <small>{t.aspect_ratio} · saved look</small>
                </button>
                <button
                  className="icon-button danger"
                  title={`Delete ${t.name}`}
                  disabled={busy !== null}
                  onClick={() => void onDeleteSaved(t)}
                >
                  <Trash2 size={15} />
                </button>
              </div>
            ))}
          </div>
        </>
      )}

      <h3 className="section-label">Starters</h3>
      <div className="template-grid gallery">
        {templates.map((t) => (
          <button key={t.id} onClick={() => onUse(t)}>
            <TemplateThumb template={t} />
            <strong>{t.name}</strong>
            <small>{t.style}</small>
          </button>
        ))}
      </div>
    </div>
  );
}
/** Podcast feeds watched for new episodes.
 *
 * The loop closing: an episode publishes and, by the time anybody looks, it is
 * transcribed and — if this feed asks for it — cut into clips waiting to be
 * approved. Nothing is ever posted automatically, and rendering is opt-in per
 * feed, because software publishing a badly cut clip to somebody's brand
 * account is the thing creators are actually frightened of.
 */
/** Clips a watched feed cut while nobody was looking.
 *
 * The machine proposes and a person disposes. Without somewhere for these to be
 * seen they land in the library and have to be hunted for, which is the
 * difference between automation somebody trusts and automation they stop using.
 */
function ReviewInbox({
  onReload,
  onCount,
}: {
  onReload: () => Promise<void>;
  onCount: (count: number) => void;
}) {
  const [clips, setClips] = useState<InboxClip[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  async function refresh() {
    try {
      const result = await api.inbox();
      setClips(result.clips);
      onCount(result.count);
    } finally {
      setLoaded(true);
    }
  }
  useEffect(() => {
    void refresh();
  }, []);

  async function act(id: string, approve: boolean) {
    setBusy(id);
    try {
      if (approve) {
        await api.approveClip(id);
        playSfx("confirm");
      } else {
        await api.rejectClip(id);
        playSfx("cursor");
      }
      // Drop it locally rather than refetching: the list should not jump.
      setClips((current) => {
        const next = current.filter((clip) => clip.id !== id);
        onCount(next.length);
        return next;
      });
      await onReload();
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="library-page">
      <div className="page-heading">
        <span className="kicker">Waiting for you</span>
        <h2>Inbox</h2>
        <p>
          Clips your watched feeds cut automatically. Nothing here has been
          posted anywhere — approve what you want and throw away the rest.
        </p>
      </div>

      {!loaded ? (
        <p className="muted">Loading…</p>
      ) : clips.length === 0 ? (
        <p className="muted">
          Nothing waiting. Clips from a watched feed will appear here.
        </p>
      ) : (
        <div className="inbox-list">
          {groupInbox(clips).map((group) => (
            <div key={group.episode} className="inbox-group">
              <div className="inbox-group-head">
                <div>
                  <strong>{group.episode}</strong>
                  <small className="muted">
                    {" "}· {group.clips.length} clip{group.clips.length === 1 ? "" : "s"} arrived {group.when}
                  </small>
                </div>
                <div className="inbox-group-actions">
                  <button
                    className="ghost compact"
                    disabled={busy !== null}
                    onClick={async () => {
                      for (const clip of group.clips) await act(clip.id, true);
                    }}
                  >
                    Keep all
                  </button>
                  <button
                    className="ghost compact"
                    disabled={busy !== null}
                    onClick={async () => {
                      if (!window.confirm(`Discard all ${group.clips.length} clips from “${group.episode}”?`)) return;
                      for (const clip of group.clips) await act(clip.id, false);
                    }}
                  >
                    Discard all
                  </button>
                </div>
              </div>
          {group.clips.map((clip) => (
            <div key={clip.id} className="inbox-card">
              <div className="inbox-top">
                <strong>{clip.title}</strong>
                <small>
                  {clockText(clip.clip_start).replace(/\.\d$/, "")} into the episode ·{" "}
                  {(clip.clip_end - clip.clip_start).toFixed(0)}s · {clip.aspect_ratio}
                </small>
                {typeof clip.scene?.pickReason === "string" && clip.scene.pickReason && (
                  <small className="pick-reason"><Sparkles size={11} /> {clip.scene.pickReason}</small>
                )}
              </div>
              <div className="inbox-actions">
                <button
                  className="primary"
                  disabled={busy === clip.id}
                  onClick={() => void act(clip.id, true)}
                >
                  <Check size={15} /> Keep
                </button>
                <button
                  className="ghost-button"
                  disabled={busy === clip.id}
                  onClick={() => void act(clip.id, false)}
                >
                  <X size={15} /> Discard
                </button>
              </div>
            </div>
          ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/** Inbox clips by episode, newest episode first, with a friendly arrival date. */
function groupInbox(clips: InboxClip[]): { episode: string; when: string; clips: InboxClip[] }[] {
  const groups = new Map<string, InboxClip[]>();
  for (const clip of clips) {
    const list = groups.get(clip.episode) ?? [];
    list.push(clip);
    groups.set(clip.episode, list);
  }
  const when = (iso: string) => {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "";
    const days = Math.floor((Date.now() - d.getTime()) / 86400000);
    if (days <= 0) return "today";
    if (days === 1) return "yesterday";
    if (days < 7) return `${days} days ago`;
    return d.toLocaleDateString(undefined, { dateStyle: "medium" });
  };
  return Array.from(groups.entries())
    .map(([episode, list]) => ({
      episode,
      when: when(list.map((c) => c.created_at).sort().at(-1) ?? ""),
      latest: list.map((c) => c.created_at).sort().at(-1) ?? "",
      clips: list.slice().sort((a, b) => a.clip_start - b.clip_start),
    }))
    .sort((a, b) => (a.latest < b.latest ? 1 : -1));
}

function Feeds({
  templates,
  jobs,
  onReload,
}: {
  templates: SavedTemplate[];
  jobs: Job[];
  onReload: () => Promise<void>;
}) {
  const [olderCount, setOlderCount] = useState<Record<string, number>>({});
  const [pulling, setPulling] = useState<string | null>(null);
  const [importNote, setImportNote] = useState<string | null>(null);
  const [feeds, setFeeds] = useState<Feed[]>([]);
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [episodes, setEpisodes] = useState<Record<string, FeedEpisode[]>>({});

  const checking = jobs.some(
    (job) => job.kind === "check_feeds" && ACTIVE.includes(job.status),
  );
  const importing = jobs.filter(
    (job) => job.kind === "import_episode" && ACTIVE.includes(job.status),
  );

  async function refresh() {
    try {
      setFeeds((await api.feeds()).feeds);
    } catch {
      // Leave what is on screen; the next poll corrects it.
    }
  }
  useEffect(() => {
    void refresh();
  }, [checking, importing.length]);

  async function add() {
    if (!url.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await api.addFeed(url.trim());
      setUrl("");
      await refresh();
      playSfx("confirm");
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="library-page">
      <div className="page-heading">
        <span className="kicker">Automation</span>
        <h2>Feeds</h2>
        <p>
          Watch a podcast feed. New episodes download and transcribe on their
          own, and clips are prepared for you to approve. Nothing is posted
          automatically.
        </p>
      </div>

      <div className="feed-add">
        <input
          value={url}
          placeholder="https://example.com/feed.xml"
          onChange={(event) => setUrl(event.target.value)}
          onKeyDown={(event) => event.key === "Enter" && void add()}
        />
        <button className="primary" disabled={busy || !url.trim()} onClick={() => void add()}>
          <Plus size={15} /> Watch
        </button>
        <button
          className="ghost-button"
          disabled={checking}
          onClick={async () => {
            await api.checkFeeds();
            await onReload();
          }}
        >
          <RefreshCw size={15} /> {checking ? "Checking…" : "Check now"}
        </button>
      </div>
      {error && <p className="panel-note">{error}</p>}

      {feeds.length === 0 ? (
        <p className="muted">No feeds yet. Paste an RSS URL above.</p>
      ) : (
        <div className="feed-list">
          {feeds.map((feed) => (
            <div key={feed.id} className="feed-card">
              <div className="feed-top">
                <strong>{feed.title}</strong>
                <small>{feed.episodes} seen</small>
                <button
                  className="icon-button danger"
                  title={`Stop watching ${feed.title}`}
                  onClick={async () => {
                    await api.deleteFeed(feed.id);
                    await refresh();
                  }}
                >
                  <Trash2 size={14} />
                </button>
              </div>
              <small className="feed-url">{feed.url}</small>
              {feed.last_error && <p className="panel-note">{feed.last_error}</p>}

              <div className="feed-controls">
                <label>
                  Clips per episode
                  <select
                    value={feed.clip_count}
                    onChange={async (event) => {
                      await api.updateFeed(feed.id, {
                        clip_count: Number(event.target.value),
                      });
                      await refresh();
                    }}
                  >
                    <option value={0}>None — just transcribe</option>
                    {[3, 4, 6, 8, 10].map((n) => (
                      <option key={n} value={n}>{n}</option>
                    ))}
                  </select>
                </label>
                {feed.clip_count > 0 && templates.length > 0 && (
                  <label>
                    Look
                    <select
                      value={feed.template_id ?? ""}
                      onChange={async (event) => {
                        await api.updateFeed(feed.id, {
                          template_id: event.target.value || null,
                        });
                        await refresh();
                      }}
                    >
                      <option value="">Default</option>
                      {templates.map((t) => (
                        <option key={t.id} value={t.id}>{t.name}</option>
                      ))}
                    </select>
                  </label>
                )}
                {feed.clip_count > 0 && (
                  <label className="checkbox-row feed-render">
                    <input
                      type="checkbox"
                      checked={feed.auto_render}
                      onChange={async (event) => {
                        await api.updateFeed(feed.id, {
                          auto_render: event.target.checked,
                        });
                        await refresh();
                      }}
                    />
                    <span>
                      Render them too
                      <small>
                        Off means clips are prepared but not exported until you say so.
                      </small>
                    </span>
                  </label>
                )}
              </div>

              <div className="feed-import-older">
                <select
                  value={olderCount[feed.id] ?? 5}
                  onChange={(e) =>
                    setOlderCount((current) => ({ ...current, [feed.id]: Number(e.target.value) }))
                  }
                >
                  {[1, 3, 5, 10, 25].map((n) => (
                    <option key={n} value={n}>{n}</option>
                  ))}
                </select>
                <button
                  className="ghost-button"
                  disabled={pulling === feed.id}
                  onClick={async () => {
                    // First sight of a feed takes the newest episode only, on
                    // purpose. This is the deliberate way to reach back.
                    setPulling(feed.id);
                    setImportNote(null);
                    try {
                      const result = await api.importOlder(feed.id, olderCount[feed.id] ?? 5);
                      setImportNote(
                        result.queued.length
                          ? `Queued ${result.queued.length} older episode${result.queued.length === 1 ? "" : "s"}` +
                            (result.remaining ? ` — ${result.remaining} more available` : "") +
                            ". Each downloads, transcribes and cuts itself into clips."
                          : "Nothing older left to import.",
                      );
                      if (episodes[feed.id]) {
                        const fresh = await api.feedEpisodes(feed.id);
                        setEpisodes((current) => ({ ...current, [feed.id]: fresh.episodes }));
                      }
                    } catch (error) {
                      setImportNote(error instanceof Error ? error.message : "Could not import.");
                    } finally {
                      setPulling(null);
                    }
                  }}
                >
                  {pulling === feed.id ? "Reading feed…" : "Import older episodes"}
                </button>
              </div>
              {importNote && <small className="muted">{importNote}</small>}
              <button
                className="ghost-button feed-episodes-toggle"
                onClick={async () => {
                  if (episodes[feed.id]) {
                    setEpisodes((current) => {
                      const next = { ...current };
                      delete next[feed.id];
                      return next;
                    });
                    return;
                  }
                  const result = await api.feedEpisodes(feed.id);
                  setEpisodes((current) => ({ ...current, [feed.id]: result.episodes }));
                }}
              >
                {episodes[feed.id] ? "Hide episodes" : "Show episodes"}
              </button>
              {episodes[feed.id] && (
                <div className="feed-episodes">
                  {episodes[feed.id].length === 0 ? (
                    <small className="muted">Nothing seen yet.</small>
                  ) : (
                    episodes[feed.id].map((episode) => (
                      <div key={episode.id} className="feed-episode">
                        <span className={`pill ${episode.status}`}>{episode.status}</span>
                        <span>{episode.title}</span>
                      </div>
                    ))
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function Exports({
  jobs,
  projects,
  onReload,
  onOpen,
}: {
  jobs: Job[];
  projects: Project[];
  onReload: () => Promise<void>;
  onOpen: (p: Project) => void;
}) {
  const done = jobs.filter(
    (j) => j.kind === "render" && j.status === "complete",
  );
  const byId = new Map(projects.map((p) => [p.id, p]));
  // Renders run several at a time now, so the queue is worth showing — and
  // worth being able to stop.
  const active = jobs.filter(
    (j) => j.kind === "render" && ACTIVE.includes(j.status),
  );
  return (
    <div className="library-page">
      <div className="page-heading">
        <span className="kicker">Output library</span>
        <h2>Exports</h2>
        <p>Finished renders and caption files from your local queue.</p>
      </div>
      {active.length > 0 && (
        <div className="export-list">
          <JobProgressPanel
            title={`Rendering now (${active.length})`}
            jobs={active}
            fallback=""
            onCancelled={onReload}
          />
        </div>
      )}
      {done.length ? (
        <div className="export-list">
          {done.map((j) => {
            const project = j.subject_id ? byId.get(j.subject_id) : undefined;
            const when = new Date(j.updated_at);
            const downloads = j.result?.downloads ?? {};
            const go = (url?: string) => {
              if (!url) return;
              const a = document.createElement("a");
              a.href = url;
              a.download = "";
              a.click();
            };
            const exportMenu: MenuItem[] = [
              { label: "Download video", disabled: !downloads.mp4, onSelect: () => go(downloads.mp4) },
              { label: "Download captions (SRT)", disabled: !downloads.srt, onSelect: () => go(downloads.srt) },
              { label: "Download captions (VTT)", disabled: !downloads.vtt, onSelect: () => go(downloads.vtt) },
              "separator",
              { label: "Copy link to send to someone", disabled: !project, onSelect: () => project && void copyShareLink(project.id) },
              { label: "Turn the link off", disabled: !project, onSelect: () => project && void api.unshare(project.id) },
              "separator",
              { label: "Open project in Studio", disabled: !project, onSelect: () => project && onOpen(project) },
            ];
            return (
            <div key={j.id} onContextMenu={(e) => openMenu(e, exportMenu, project?.title)}>
              {project ? (
                <Poster projectId={project.id} ratio={project.aspect_ratio} icon={18} rendered={project.rendered} compact />
              ) : (
                <div className="export-icon">
                  <Film size={18} />
                </div>
              )}
              <div>
                <strong>{project?.title ?? "Deleted project"}</strong>
                <small>
                  {project ? `${project.aspect_ratio} · ${clockText(project.clip_end - project.clip_start)} · ` : ""}
                  {Number.isNaN(when.getTime()) ? "" : when.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}
                </small>
              </div>
              {j.result?.downloads?.mp4 && (
                <a className="button-link" href={j.result.downloads.mp4} download>
                  <Download size={15} /> Video
                </a>
              )}
              {j.result?.downloads?.srt && (
                <a className="button-link quiet" href={j.result.downloads.srt} download>
                  Captions
                </a>
              )}
              {project && <ShareButton projectId={project.id} />}
              <MenuButton items={exportMenu} title={project?.title} />
              {project && <PostedBadges project={project} />}
              {project && (
                <div className="export-post">
                  <YouTubePost projectId={project.id} defaultTitle={project.title} />
                  <SocialPostRow projectId={project.id} defaultTitle={project.title} />
                </div>
              )}
            </div>
            );
          })}
        </div>
      ) : (
        <div className="empty-state">
          <Download size={28} />
          <strong>No exports yet.</strong>
          <span>Render a project from Studio to see it here.</span>
        </div>
      )}
    </div>
  );
}
function AdminStrip({
  users,
  gpus,
  values,
  isAdmin,
  onSave,
  onReload,
}: {
  users: User[];
  gpus: Gpu[];
  values: Record<string, string>;
  isAdmin: boolean;
  onSave: (v: Record<string, string>) => Promise<void>;
  onReload: () => Promise<void>;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [signupsOpen, setSignupsOpen] = useState(true);
  const [adminError, setAdminError] = useState<string | null>(null);
  const [invite, setInvite] = useState<{ code: string | null; link: string | null } | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    api
      .signupState()
      .then((state) => setSignupsOpen(state.open))
      .catch(() => undefined);
    if (isAdmin) {
      api.inviteLink().then(setInvite).catch(() => setInvite(null));
    }
  }, [isAdmin]);

  return (
    <details className="admin-strip">
      <summary>
        <Users size={16} /> Admin · {users.length} users · {gpus.length} GPUs
      </summary>
      <TranscriptionPanel isAdmin={isAdmin} />
      <YouTubeAdmin />
      <SocialAdmin />
      <div className="admin-content">
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            setAdminError(null);
            try {
              await api.createUser({ username, password, is_admin: false });
              setUsername("");
              setPassword("");
              await onReload();
            } catch (cause) {
              setAdminError((cause as Error).message);
            }
          }}
        >
          <input
            aria-label="New username"
            placeholder="username"
            autoCapitalize="none"
            spellCheck={false}
            minLength={3}
            maxLength={32}
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
          />
          <input
            aria-label="New user password"
            type="password"
            placeholder="Password"
            minLength={10}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
          <button className="ghost compact">
            <UserPlus size={15} /> Add user
          </button>
        </form>
        {adminError && <p className="error">{adminError}</p>}
        <label className="signup-toggle">
          <input
            type="checkbox"
            checked={signupsOpen}
            onChange={async (event) => {
              const next = event.target.checked;
              setSignupsOpen(next);
              try {
                await api.setSignups(next);
              } catch (cause) {
                setSignupsOpen(!next);
                setAdminError((cause as Error).message);
              }
            }}
          />
          Anyone can create an account
        </label>
        <div className="admin-users">
          <span className="sidebar-label">Accounts</span>
          <ul className="account-list">
            {users.map((account) => (
              <li key={account.id}>
                <span>
                  {account.username}
                  {account.is_admin ? <small> admin</small> : null}
                </span>
                {!account.is_admin && (
                  <button
                    className="layer-action"
                    title={`Remove ${account.username} and everything they made`}
                    onClick={async () => {
                      if (
                        !window.confirm(
                          `Remove ${account.username}? Their uploads, clips and renders are deleted with the account.`,
                        )
                      )
                        return;
                      try {
                        await api.deleteUser(account.id);
                        await onReload();
                      } catch (cause) {
                        setAdminError((cause as Error).message);
                      }
                    }}
                  >
                    <Trash2 size={13} />
                  </button>
                )}
              </li>
            ))}
          </ul>
        </div>
        {typeof Notification !== "undefined" && Notification.permission === "default" && (
          <button
            className="ghost compact"
            onClick={() => void Notification.requestPermission()}
          >
            Notify me when a feed cuts new clips
          </button>
        )}
        {invite?.link && (
          <div className="invite-link">
            <span className="sidebar-label">Invite link</span>
            <p className="muted">
              Anyone with this link can create an account, even while sign-ups
              are closed. Change the code in the container settings to revoke it.
            </p>
            <div className="mini-fields">
              <input readOnly value={invite.link} onFocus={(e) => e.currentTarget.select()} />
              <button
                className="ghost compact"
                onClick={async () => {
                  try {
                    await navigator.clipboard.writeText(invite.link ?? "");
                    setCopied(true);
                    window.setTimeout(() => setCopied(false), 2000);
                  } catch {
                    // Clipboard is blocked outside secure contexts; the field
                    // above selects on focus for a manual copy.
                  }
                }}
              >
                {copied ? "Copied" : "Copy"}
              </button>
            </div>
          </div>
        )}
        <label>
          Transcription GPU
          <select
            value={values.transcription_gpu_uuid ?? ""}
            onChange={(e) =>
              void onSave({ ...values, transcription_gpu_uuid: e.target.value })
            }
          >
            <option value="">CPU / automatic</option>
            {gpus.map((g) => (
              <option key={g.uuid} value={g.uuid}>
                {g.name}
              </option>
            ))}
          </select>
        </label>
      </div>
    </details>
  );
}
function AuthScreen({
  mode,
  onDone,
  error,
  onError,
}: {
  mode: AuthView;
  onDone: (u: User) => Promise<void>;
  error: string | null;
  onError: (e: string | null) => void;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  // Only offered when the instance allows it, so a closed box does not show a
  // tab that always fails.
  const [signupsOpen, setSignupsOpen] = useState(false);
  const [codeRequired, setCodeRequired] = useState(false);
  const [code, setCode] = useState("");
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    if (mode !== "login") return;
    api
      .signupState()
      .then((state) => {
        setSignupsOpen(state.open);
        setCodeRequired(state.code_required);
      })
      .catch(() => setSignupsOpen(false));
    // A shared link carries the code: kinder.example.com/?invite=CODE opens
    // straight onto the sign-up form with it filled in. Nobody has to be
    // told where to type it.
    const invite = new URLSearchParams(window.location.search).get("invite");
    if (invite) {
      setCode(invite.trim());
      setCreating(true);
      window.history.replaceState(null, "", window.location.pathname);
    }
  }, [mode]);

  const first = mode === "bootstrap";
  const registering = creating && !first;

  return (
    <div className="auth-screen">
      <form
        className="auth-form"
        onSubmit={async (event) => {
          event.preventDefault();
          setBusy(true);
          onError(null);
          try {
            const result = first
              ? await api.bootstrap(username, password)
              : registering
                ? await api.register(username, password, code)
                : await api.login(username, password);
            await onDone(result.user);
          } catch (cause) {
            onError(cause instanceof Error ? cause.message : "Authentication failed");
          } finally {
            setBusy(false);
          }
        }}
      >
        <img
          className="auth-mark"
          src="/brand/kinder-logo-stacked.svg"
          alt="Kinder"
          width={104}
          height={104}
        />
        <h1>
          {first
            ? "Create your administrator"
            : registering
              ? "Create an account"
              : "Welcome back"}
        </h1>
        <p>
          {first
            ? "Set up the first local account to begin."
            : registering
              ? "Pick a username and password. Nothing else is asked for."
              : "Sign in to your private creator workspace."}
        </p>
        <label>
          Username
          <input
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            autoComplete="username"
            autoCapitalize="none"
            spellCheck={false}
            placeholder="mujin"
            required
          />
        </label>
        <label>
          Password
          <input
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            type="password"
            autoComplete={first || registering ? "new-password" : "current-password"}
            required
          />
        </label>
        {registering && codeRequired && (
          <label>
            Invite code
            <input
              value={code}
              onChange={(event) => setCode(event.target.value)}
              autoComplete="off"
              autoCapitalize="none"
              spellCheck={false}
              placeholder="From whoever invited you"
              required
            />
          </label>
        )}
        {(first || registering) && (
          <p className="muted auth-hint">
            At least 10 characters. Usernames are 3–32 characters: letters,
            digits, dot, dash, or underscore.
          </p>
        )}
        {error && <p className="error">{error}</p>}
        <button className="primary large" disabled={busy}>
          {busy ? <Loader2 className="spin" size={17} /> : <LogIn size={17} />}
          {first ? "Initialize studio" : registering ? "Create account" : "Sign in"}
        </button>
        {!first && (signupsOpen || codeRequired) && (
          <button
            type="button"
            className="text-button"
            onClick={() => {
              onError(null);
              setCreating((value) => !value);
            }}
          >
            {registering
              ? "I already have an account"
              : codeRequired && !signupsOpen
                ? "I have an invite code"
                : "Create an account"}
          </button>
        )}
      </form>
    </div>
  );
}
/** Wrap the matched substring so the reason a line matched is obvious. */
function highlight(text: string, query: string) {
  const needle = query.trim();
  if (!needle) return text;
  const at = text.toLowerCase().indexOf(needle.toLowerCase());
  if (at < 0) return text;
  return (
    <>
      {text.slice(0, at)}
      <mark>{text.slice(at, at + needle.length)}</mark>
      {text.slice(at + needle.length)}
    </>
  );
}

function formatTime(s: number) {
  const m = Math.floor(Math.max(0, s) / 60);
  return `${m}:${Math.floor(Math.max(0, s) % 60)
    .toString()
    .padStart(2, "0")}`;
}
