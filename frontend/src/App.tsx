import { useEffect, useMemo, useRef, useState } from "react";
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
import { DesignPanel } from "./DesignPanel";
import { HistoryPanel } from "./HistoryPanel";
import { CutRange, TranscriptCuts, cutDuration, merge as mergeCuts } from "./TranscriptCuts";
import { VariantsPanel } from "./VariantsPanel";
import { MusicPanel } from "./MusicPanel";
import { TranscriptionPanel } from "./TranscriptionSettings";
import { usePeaks, WaveformCanvas } from "./Waveform";
import { loadSfx, play as playSfx, setSfxEnabled, sfxEnabled } from "./sfx";

type View = "home" | "quick" | "studio" | "projects" | "templates" | "feeds" | "inbox" | "exports";
type AuthView = "loading" | "bootstrap" | "login" | "app";
type Ratio = "9:16" | "1:1" | "4:5" | "16:9";
type Layer = {
  mediaId?: string;
  radius?: number;
  id: string;
  name: string;
  type: "title" | "artwork" | "waveform" | "captions" | "background";
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
};
// Mirrors PLATFORM_SAFE_AREAS in backend/app/services/scene.py.
const SAFE_AREAS: Record<string, { label: string; bottom: number; top: number; right: number }> = {
  tiktok: { label: "TikTok", bottom: 0.22, top: 0.1, right: 0.16 },
  reels: { label: "Reels", bottom: 0.2, top: 0.1, right: 0.14 },
  shorts: { label: "Shorts", bottom: 0.16, top: 0.08, right: 0.14 },
  feed: { label: "Feed", bottom: 0.06, top: 0.06, right: 0.06 },
};

// Keep in step with WAVE_STYLES in backend/app/services/scene.py.
const waveStyles: [string, string][] = [
  ["line", "Centred line"],
  ["bars", "Bars"],
  ["wideBars", "Wide bars"],
  ["edge", "Edge"],
  ["points", "Points"],
  ["none", "No waveform"],
];
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
const templates = [
  {
    id: "kinder",
    name: "Kinder",
    style: "Obsidian / baby blue",
    background: BRAND.obsidian,
    accent: BRAND.blue,
  },
  {
    id: "paper",
    name: "Paper Cut",
    style: "Editorial / clean",
    background: "#f3eee5",
    accent: "#8d3f35",
  },
  {
    id: "midnight",
    name: "Midnight Gold",
    style: "Podcast / contrast",
    background: BRAND.surface,
    accent: BRAND.gold,
  },
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
      text: "Episode Title",
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
  const [navOpen, setNavOpen] = useState(false);
  const [saved, setSaved] = useState<SavedTemplate[]>([]);
  const [inboxCount, setInboxCount] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [soundOn, setSoundOn] = useState(sfxEnabled());
  const selected =
    projects.find((p) => p.id === selectedId) ?? projects[0] ?? null;
  const selectedMedia = selected
    ? (media.find((m) => m.id === selected.media_id) ?? null)
    : null;
  async function loadData(active = user) {
    const [m, p, j, g, s, u, t] = await Promise.all([
      api.media(),
      api.projects(),
      api.jobs(),
      api.gpus(),
      api.gpuSettings(),
      active?.is_admin ? api.users() : Promise.resolve({ users: [] }),
      api.templates(),
    ]);
    setMedia(m.media);
    setProjects(p.projects);
    setJobs(j.jobs);
    setSaved(t.templates);
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
  useEffect(() => {
    let ignore = false;
    (async () => {
      try {
        const state = await api.bootstrapState();
        if (!state.initialized) {
          if (!ignore) setAuth("bootstrap");
          return;
        }
        const me = await api.me();
        if (!ignore) {
          setUser(me.user);
          setAuth("app");
          await loadData(me.user);
        }
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
        const [jobResult, mediaResult] = await Promise.all([api.jobs(), api.media()]);
        if (stopped) return;
        setJobs(jobResult.jobs);
        setMedia(mediaResult.media);
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
      layers: defaultLayers(ratio).map((layer) =>
        layer.type === "artwork" && artwork ? { ...layer, mediaId: artwork } : layer,
      ),
      ...(artwork
        ? { backgroundImage: { mediaId: artwork, blur: 22, dim: 0.45 } }
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
    const result = await api.updateProject(selected, updates);
    setProjects(
      projects.map((p) => (p.id === result.project.id ? result.project : p)),
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
            <span className="saved-dot">Local workspace</span>
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
            onStudio={() => setView("studio")}
            projects={projects}
            onOpen={(p) => {
              setSelectedId(p.id);
              setView("studio");
            }}
          />
        )}
        {view === "quick" && (
          <QuickCreate
            onRefresh={() => loadData()}
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
        {view === "exports" && <Exports jobs={jobs} onReload={() => loadData()} />}
        {user?.is_admin && (
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
        <small>{user?.username}</small>
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
          >
            <Film size={14} />
            <span>{p.title}</span>
          </button>
        ))}
      </div>
      <div className="sidebar-footer">
        <span className="status-light" /> Local processing
      </div>
    </aside>
  );
}
function Home({
  onCreate,
  onStudio,
  projects,
  onOpen,
}: {
  onCreate: () => void;
  onStudio: () => void;
  projects: Project[];
  onOpen: (p: Project) => void;
}) {
  const actions = [
    [
      "Create Audiogram",
      "Turn a podcast into a social video",
      Sparkles,
      onCreate,
    ],
    [
      "Clip & Caption",
      "Start from a transcript moment",
      WandSparkles,
      onCreate,
    ],
    ["Full Episode Video", "Build a long-form visual episode", Film, onCreate],
    ["Studio Editor", "Open a blank creative canvas", Settings2, onStudio],
  ] as const;
  return (
    <div className="home">
      <section className="welcome">
        <span className="kicker">Creator workspace</span>
        <h2>
          Make the moment
          <br />
          worth sharing.
        </h2>
        <p>
          Choose a starting point. Your projects, captions, and exports stay on
          your server.
        </p>
      </section>
      <div className="creation-grid">
        {actions.map(([title, detail, Icon, action]) => (
          <button className="creation-tile" key={title} onClick={action}>
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
                <div
                  className={`project-thumb ratio-${p.aspect_ratio.replace(":", "-")}`}
                >
                  <AudioLines size={26} />
                </div>
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
function QuickCreate({
  onRefresh,
  media,
  jobs,
  selectedMedia,
  onUpload,
  onCreate,
}: {
  onRefresh: () => Promise<void>;
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
  const [upload, setUpload] = useState<{ name: string; fraction: number } | null>(
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
  const sourceJobs = source
    ? jobs.filter(
        (job) =>
          job.subject_id === source.id &&
          ["analyze_media", "transcribe"].includes(job.kind),
      )
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
              <>
                <strong>{upload.name}</strong>
                <span>
                  {upload.fraction >= 1
                    ? "Finishing…"
                    : `Uploading ${Math.round(upload.fraction * 100)}%`}
                </span>
                <div
                  className="upload-bar"
                  role="progressbar"
                  aria-valuenow={Math.round(upload.fraction * 100)}
                >
                  <i style={{ width: `${Math.max(2, upload.fraction * 100)}%` }} />
                </div>
              </>
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
                setUpload({ name: f.name, fraction: 0 });
                try {
                  const uploaded = await onUpload(f, (fraction) =>
                    setUpload({ name: f.name, fraction }),
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
          {media.length > 0 && (
            <div className="source-list">
              {media.map((m) => (
                <div
                  className={`source-row${source?.id === m.id ? " selected" : ""}`}
                  key={m.id}
                >
                  <button onClick={() => setSourceId(m.id)}>
                    <FileAudio size={18} />
                    <span>
                      {m.original_name}
                      <small>
                        {m.has_transcript
                          ? "Transcript ready"
                          : "Analyzing media"}
                      </small>
                    </span>
                    {source?.id === m.id && <b>✓</b>}
                  </button>
                  <button
                    className="source-remove"
                    title={`Remove ${m.original_name}`}
                    aria-label={`Remove ${m.original_name}`}
                    onClick={async () => {
                      // Deleting a source does not delete the clips made from
                      // it, so this is not the destructive act it looks like;
                      // it is still worth confirming, because the file itself
                      // is gone and would have to be uploaded again.
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
                        setUploadError(
                          error instanceof Error
                            ? error.message
                            : "That file could not be removed.",
                        );
                      }
                    }}
                  >
                    <Trash2 size={15} />
                  </button>
                </div>
              ))}
            </div>
          )}
          {source && (
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

  return (
    <div className="job-progress-panel">
      <span className="sidebar-label">{title}</span>
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
        <strong>Who is talking?</strong>
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
  if (busy) return <p className="muted suggestion-note">Looking for clips…</p>;
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
    (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
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
  const canvasRef = useRef<HTMLDivElement>(null);
  const textFieldRef = useRef<HTMLTextAreaElement>(null);
  const mediaRef = useRef<HTMLMediaElement | null>(null);
  const layersRef = useRef(layers);
  useEffect(() => { layersRef.current = layers; }, [layers]);
  // Layers can change from outside the canvas — applying a template, or the
  // caption-collision fix — and the canvas has to follow. The dependency is the
  // serialised layers rather than the scene object, which is a fresh reference
  // on every poll and would clobber a drag in progress.
  const storedLayers = JSON.stringify(project?.scene?.layers ?? null);
  useEffect(() => {
    setLayers(getLayers(project));
    setTitleDraft(project?.title ?? "");
    setClipStart(project?.clip_start ?? 0);
    setClipEnd(project?.clip_end ?? 45);
    setPlayhead(project?.clip_start ?? 0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project?.id, project?.clip_start, project?.clip_end, project?.title, storedLayers]);
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
      const now = element.currentTime;
      if (now >= clipEnd) {
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
        element.currentTime = clamped;
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
        element.currentTime = clipStart;
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
  const activeCaption =
    previewCaptions.find(
      (line) => localPlayhead >= line.start - 0.25 && localPlayhead <= line.end,
    )?.text ?? null;
  const platform = String(project?.scene?.platform ?? "");
  const safeArea = SAFE_AREAS[platform] ?? null;
  const sourceUrl = media ? api.mediaFileUrl(media.id) : "";
  const activeRender = jobs.find(
    (job) =>
      job.kind === "render" &&
      job.subject_id === project?.id &&
      ["queued", "running"].includes(job.status),
  );
  async function save(next: Layer[]) {
    layersRef.current = next;
    setLayers(next);
    if (project) await onUpdate({ scene: { ...project.scene, layers: next } });
  }
  async function saveScene(patch: Record<string, unknown>) {
    if (!project) return;
    await onUpdate({ scene: { ...project.scene, ...patch } });
  }
  async function saveCuts(next: CutRange[]) {
    if (!project) return;
    const cuts = mergeCuts(next);
    // A cut clip is a different render, so a stale export would be wrong
    // rather than merely old; the fingerprint covers the scene, so this is
    // enough to make the next export produce a new file.
    await onUpdate({ scene: { ...project.scene, cuts } });
  }
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
  function updateLayer(id: string, updates: Partial<Layer>) {
    void save(layers.map((l) => (l.id === id ? { ...l, ...updates } : l)));
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
    element.currentTime = Math.max(clipStart, Math.min(clipEnd, from));
    await element.play().catch(() => undefined);
    setPlaying(true);
  }
  function drag(e: React.PointerEvent, layer: Layer) {
    if (layer.locked || !canvasRef.current) return;
    const rect = canvasRef.current.getBoundingClientRect();
    const sx = e.clientX;
    const sy = e.clientY;
    const ox = layer.x;
    const oy = layer.y;
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
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
      await save(layersRef.current);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
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
          className="ghost"
          onClick={() => {
            playSfx("confirm");
            void handleExport();
          }}
        >
          <Download size={16} /> Export
        </button>
      </div>
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
        </aside>
        <section className="canvas-area">
          <div
            className={`canvas-wrap ratio-${(project?.aspect_ratio ?? "9:16").replace(":", "-")}`}
          >
            <div
              className="design-canvas"
              ref={canvasRef}
              style={{ background, transform: `scale(${zoom})`, transformOrigin: "center" }}
            >
              {backgroundImageUrl && (
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
                    className={`canvas-layer layer-${layer.type} ${selectedLayer === layer.id ? "selected" : ""}`}
                    style={{
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
                            return { top: `${band.top}%`, height: `${band.height}%` };
                          })()
                        : { top: `${layer.y}%`, height: `${layer.height}%` }),
                      width: `${layer.width}%`,
                      color: layer.color ?? "#fff",
                      borderColor: accent,
                    }}
                    onPointerDown={(e) => {
                      setSelectedLayer(layer.id);
                      drag(e, layer);
                    }}
                  >
                    <LayerContent
                      layer={layer}
                      title={project?.title ?? "Episode title"}
                      media={media}
                      accent={accent}
                      peaks={clipPeaks}
                      caption={activeCaption}
                      captionPreset={captionPreset}
                    />
                  </div>
                ))}
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
                Full episode. Your clip is {formatTime(clipStart)}–
                {formatTime(clipEnd)}; the player starts there.
              </small>
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
                    if (!playing) setPlayhead(e.currentTarget.currentTime);
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
                    if (!playing) setPlayhead(e.currentTarget.currentTime);
                  }}
                  onPlay={() => setPlaying(true)}
                  onPause={() => setPlaying(false)}
                />
              )}
            </div>
          )}
        </section>
        <aside className="inspector">
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
          <DesignPanel
            project={project}
            media={allMedia}
            onScene={(patch) => saveScene(patch)}
            onMediaAdded={onMediaAdded}
          />
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
            onChange={(next) => void saveMusicBed(next)}
          />
          <div className="inspector-heading">
            <span className="sidebar-label">Layers</span>
          </div>
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
              <span className="sidebar-label">Properties</span>
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
              transcriptDraft.segments.map((segment) => (
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
              ))
            ) : (
              <p className="muted">Upload media and wait for transcription to edit captions.</p>
            )}
          </div>
        </aside>
      </div>
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
    const parsed = Number(text);
    if (Number.isFinite(parsed)) onCommit(Math.max(min, parsed));
    setText(null);
  };
  return (
    <label>
      {label}
      <input
        type="number"
        min={min}
        step="0.1"
        value={text ?? value.toFixed(1)}
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

function LayerContent({
  layer,
  title,
  media,
  accent,
  peaks,
  caption,
  captionPreset,
}: {
  layer: Layer;
  title: string;
  media: MediaAsset | null;
  accent: string;
  peaks: number[];
  caption?: string | null;
  captionPreset?: string;
}) {
  if (layer.type === "artwork")
    return layer.mediaId ? (
      <img
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
    return (
      <div className="mini-wave">
        <WaveformCanvas
          peaks={peaks}
          ready={peaks.length > 0}
          color={layer.color ?? accent}
          className="waveform-canvas"
        />
      </div>
    );
  if (layer.type === "captions")
    return (
      <span className="layer-caption" data-preset={captionPreset ?? "social"}>
        {caption ?? layer.text ?? "Captions appear here as the clip plays."}
      </span>
    );
  if (layer.type === "title")
    return <span className="layer-title">{layer.text ?? title}</span>;
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
    target.setPointerCapture(event.pointerId);

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
}: {
  projects: Project[];
  onOpen: (p: Project) => void;
  onDelete: (p: Project) => Promise<void>;
}) {
  // Deleting a project throws away a render, so it asks once. Confirming in
  // place rather than through a modal keeps the answer next to the question.
  const [confirming, setConfirming] = useState<string | null>(null);
  return (
    <div className="library-page">
      <div className="page-heading">
        <span className="kicker">Library</span>
        <h2>Projects</h2>
        <p>Everything you create stays available on this server.</p>
      </div>
      <div className="library-grid">
        {projects.map((p) => (
          <div key={p.id} className="library-card">
            <button onClick={() => onOpen(p)}>
              <div
                className={`project-thumb ratio-${p.aspect_ratio.replace(":", "-")}`}
              >
                <AudioLines size={27} />
              </div>
              <strong>{p.title}</strong>
              <small>
                {p.aspect_ratio} · {(p.clip_end - p.clip_start).toFixed(1)}s
              </small>
            </button>
            {confirming === p.id ? (
              <div className="card-confirm">
                <button
                  className="danger-button"
                  onClick={async () => {
                    setConfirming(null);
                    await onDelete(p);
                  }}
                >
                  Delete
                </button>
                <button onClick={() => setConfirming(null)}>Keep</button>
              </div>
            ) : (
              <button
                className="icon-button danger"
                title={`Delete ${p.title}`}
                onClick={() => setConfirming(p.id)}
              >
                <Trash2 size={15} />
              </button>
            )}
          </div>
        ))}
      </div>
    </div>
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
  return (
    <div className="template-thumb" style={{ background: template.background }}>
      <div style={{ background: template.accent }} />
      <span>
        Episode
        <br />
        Title
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
          {clips.map((clip) => (
            <div key={clip.id} className="inbox-card">
              <div className="inbox-top">
                <strong>{clip.title}</strong>
                <small>
                  {clip.episode} · {(clip.clip_end - clip.clip_start).toFixed(0)}s ·{" "}
                  {clip.aspect_ratio}
                </small>
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
      )}
    </div>
  );
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

function Exports({ jobs, onReload }: { jobs: Job[]; onReload: () => Promise<void> }) {
  const done = jobs.filter(
    (j) => j.kind === "render" && j.status === "complete",
  );
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
          {done.map((j) => (
            <div key={j.id}>
              <div className="export-icon">
                <Film size={18} />
              </div>
              <div>
                <strong>{j.message}</strong>
                <small>Ready to download</small>
              </div>
              {j.result?.downloads?.mp4 && (
                <a href={j.result.downloads.mp4} title="Download MP4">
                  <Download size={17} />
                </a>
              )}
            </div>
          ))}
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

  useEffect(() => {
    api
      .signupState()
      .then((state) => setSignupsOpen(state.open))
      .catch(() => undefined);
  }, []);

  return (
    <details className="admin-strip">
      <summary>
        <Users size={16} /> Admin · {users.length} users · {gpus.length} GPUs
      </summary>
      <TranscriptionPanel isAdmin={isAdmin} />
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
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    if (mode !== "login") return;
    api
      .signupState()
      .then((state) => setSignupsOpen(state.open))
      .catch(() => setSignupsOpen(false));
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
                ? await api.register(username, password)
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
        {!first && signupsOpen && (
          <button
            type="button"
            className="text-button"
            onClick={() => {
              onError(null);
              setCreating((value) => !value);
            }}
          >
            {registering ? "I already have an account" : "Create an account"}
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
