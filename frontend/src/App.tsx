import { useEffect, useMemo, useState } from "react";
import {
  AudioLines,
  Check,
  Cpu,
  Download,
  Film,
  FolderPlus,
  Loader2,
  LogIn,
  Monitor,
  Play,
  RefreshCw,
  Scissors,
  Settings,
  Upload,
  UserPlus,
  Users
} from "lucide-react";
import { api, Gpu, Job, MediaAsset, Project, User } from "./api";

type ViewState = "loading" | "bootstrap" | "login" | "app";

const aspectRatios: Project["aspect_ratio"][] = ["9:16", "1:1", "16:9"];

export function App() {
  const [view, setView] = useState<ViewState>("loading");
  const [user, setUser] = useState<User | null>(null);
  const [media, setMedia] = useState<MediaAsset[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [gpus, setGpus] = useState<Gpu[]>([]);
  const [users, setUsers] = useState<User[]>([]);
  const [gpuSettings, setGpuSettings] = useState<Record<string, string>>({});
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const selectedProject = projects.find((project) => project.id === selectedProjectId) ?? projects[0] ?? null;
  const selectedMedia = selectedProject ? media.find((item) => item.id === selectedProject.media_id) ?? null : null;

  async function loadAppData(activeUser = user) {
    const [mediaResponse, projectResponse, jobResponse, gpuResponse, settingsResponse, usersResponse] = await Promise.all([
      api.media(),
      api.projects(),
      api.jobs(),
      api.gpus(),
      api.gpuSettings(),
      activeUser?.is_admin ? api.users() : Promise.resolve({ users: [] })
    ]);
    setMedia(mediaResponse.media);
    setProjects(projectResponse.projects);
    setJobs(jobResponse.jobs);
    setGpus(gpuResponse.gpus);
    setGpuSettings(settingsResponse);
    setUsers(usersResponse.users);
    if (!selectedProjectId && projectResponse.projects.length > 0) {
      setSelectedProjectId(projectResponse.projects[0].id);
    }
  }

  useEffect(() => {
    let ignore = false;
    async function boot() {
      try {
        const state = await api.bootstrapState();
        if (!state.initialized) {
          if (!ignore) setView("bootstrap");
          return;
        }
        const me = await api.me();
        if (!ignore) {
          setUser(me.user);
          setView("app");
          await loadAppData(me.user);
        }
      } catch {
        if (!ignore) setView("login");
      }
    }
    void boot();
    return () => {
      ignore = true;
    };
  }, []);

  useEffect(() => {
    if (view !== "app") return;
    const handle = window.setInterval(() => {
      api.jobs().then((response) => setJobs(response.jobs)).catch(() => undefined);
      api.media().then((response) => setMedia(response.media)).catch(() => undefined);
    }, 2500);
    return () => window.clearInterval(handle);
  }, [view]);

  async function handleAuthenticated(nextUser: User) {
    setUser(nextUser);
    setView("app");
    await loadAppData(nextUser);
  }

  async function createProjectFromMedia(item?: MediaAsset) {
    const title = item ? item.original_name.replace(/\.[^.]+$/, "") : "Untitled audiogram";
    const response = await api.createProject(title, item?.id);
    setProjects([response.project, ...projects]);
    setSelectedProjectId(response.project.id);
  }

  async function updateSelectedProject(updates: Partial<Project>) {
    if (!selectedProject) return;
    const response = await api.updateProject(selectedProject, updates);
    setProjects(projects.map((project) => (project.id === response.project.id ? response.project : project)));
  }

  if (view === "loading") {
    return <LoadingScreen />;
  }

  if (view === "bootstrap") {
    return <AuthScreen mode="bootstrap" onDone={handleAuthenticated} onError={setError} error={error} />;
  }

  if (view === "login") {
    return <AuthScreen mode="login" onDone={handleAuthenticated} onError={setError} error={error} />;
  }

  return (
    <main className="shell">
      <aside className="sidebar">
        <div className="brand">
          <AudioLines size={28} />
          <div>
            <strong>Podcast Audiogram Studio</strong>
            <span>{user?.email}</span>
          </div>
        </div>
        <button className="primary" onClick={() => createProjectFromMedia()}>
          <FolderPlus size={17} /> New project
        </button>
        <nav className="project-list" aria-label="Projects">
          {projects.map((project) => (
            <button
              key={project.id}
              className={project.id === selectedProject?.id ? "selected" : ""}
              onClick={() => setSelectedProjectId(project.id)}
            >
              <Film size={16} />
              <span>{project.title}</span>
            </button>
          ))}
          {projects.length === 0 && <p className="muted">Create or upload media to start.</p>}
        </nav>
      </aside>

      <section className="workspace">
        <header className="topbar">
          <div>
            <p className="eyebrow">Local creator workflow</p>
            <h1>{selectedProject?.title ?? "No project selected"}</h1>
          </div>
          <button className="ghost" onClick={() => loadAppData()}>
            <RefreshCw size={16} /> Refresh
          </button>
        </header>

        <div className="workgrid">
          <section className="editor-panel">
            <Uploader
              media={media}
              onUpload={async (file) => {
                await api.uploadMedia(file);
                await loadAppData();
              }}
              onCreateProject={createProjectFromMedia}
            />
            <TranscriptEditor media={selectedMedia} project={selectedProject} onUpdate={updateSelectedProject} />
          </section>

          <section className="preview-panel">
            <Preview project={selectedProject} media={selectedMedia} onUpdate={updateSelectedProject} />
            <RenderQueue
              jobs={jobs}
              onRender={async () => {
                if (!selectedProject) return;
                await api.renderProject(selectedProject);
                await loadAppData();
              }}
              canRender={Boolean(selectedProject)}
            />
            <GpuSettings gpus={gpus} values={gpuSettings} onSave={async (values) => {
              await api.saveGpuSettings(values);
              setGpuSettings(values);
            }} />
            {user?.is_admin && (
              <UserAdmin
                users={users}
                currentUserId={user.id}
                onReload={async () => {
                  const response = await api.users();
                  setUsers(response.users);
                }}
              />
            )}
          </section>
        </div>
      </section>
    </main>
  );
}

function LoadingScreen() {
  return (
    <div className="auth-screen">
      <Loader2 className="spin" size={28} />
    </div>
  );
}

function AuthScreen({
  mode,
  onDone,
  onError,
  error
}: {
  mode: "bootstrap" | "login";
  onDone: (user: User) => void;
  onError: (message: string | null) => void;
  error: string | null;
}) {
  const [email, setEmail] = useState("admin@example.com");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    onError(null);
    try {
      const response = mode === "bootstrap" ? await api.bootstrap(email, password) : await api.login(email, password);
      onDone(response.user);
    } catch (err) {
      onError(err instanceof Error ? err.message : "Authentication failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth-screen">
      <form className="auth-form" onSubmit={submit}>
        <AudioLines size={34} />
        <h1>{mode === "bootstrap" ? "Create administrator" : "Sign in"}</h1>
        <label>
          Email
          <input value={email} onChange={(event) => setEmail(event.target.value)} type="email" autoComplete="email" />
        </label>
        <label>
          Password
          <input
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            type="password"
            autoComplete={mode === "bootstrap" ? "new-password" : "current-password"}
          />
        </label>
        {error && <p className="error">{error}</p>}
        <button className="primary" disabled={busy}>
          {busy ? <Loader2 className="spin" size={17} /> : <LogIn size={17} />}
          {mode === "bootstrap" ? "Initialize" : "Sign in"}
        </button>
      </form>
    </div>
  );
}

function Uploader({
  media,
  onUpload,
  onCreateProject
}: {
  media: MediaAsset[];
  onUpload: (file: File) => Promise<void>;
  onCreateProject: (media?: MediaAsset) => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);

  return (
    <section className="tool-section">
      <div className="section-heading">
        <h2>Source media</h2>
        <label className="icon-button" title="Upload media">
          <Upload size={17} />
          <input
            type="file"
            accept="audio/*,video/*"
            onChange={async (event) => {
              const file = event.target.files?.[0];
              if (!file) return;
              setBusy(true);
              await onUpload(file);
              setBusy(false);
            }}
          />
        </label>
      </div>
      {busy && <p className="muted">Uploading and queueing analysis.</p>}
      <div className="media-list">
        {media.map((item) => (
          <button key={item.id} onClick={() => onCreateProject(item)}>
            <AudioLines size={15} />
            <span>{item.original_name}</span>
            <small>{item.duration_seconds ? `${Math.round(item.duration_seconds)}s` : "analyzing"}</small>
          </button>
        ))}
      </div>
    </section>
  );
}

function TranscriptEditor({
  media,
  project,
  onUpdate
}: {
  media: MediaAsset | null;
  project: Project | null;
  onUpdate: (updates: Partial<Project>) => Promise<void>;
}) {
  const segments = media?.transcript?.segments ?? [];
  return (
    <section className="tool-section transcript">
      <div className="section-heading">
        <h2>Transcript</h2>
        <Scissors size={17} />
      </div>
      {segments.length === 0 && <p className="muted">Upload media and wait for transcription to populate this editor.</p>}
      {segments.map((segment) => (
        <button
          className="segment"
          key={segment.id}
          onClick={() => onUpdate({ clip_start: segment.start, clip_end: segment.end })}
          disabled={!project}
        >
          <span>{formatTime(segment.start)}</span>
          <p>{segment.text}</p>
        </button>
      ))}
    </section>
  );
}

function Preview({
  project,
  media,
  onUpdate
}: {
  project: Project | null;
  media: MediaAsset | null;
  onUpdate: (updates: Partial<Project>) => Promise<void>;
}) {
  const ratioClass = project?.aspect_ratio === "16:9" ? "wide" : project?.aspect_ratio === "1:1" ? "square" : "vertical";
  const duration = useMemo(() => {
    if (!project) return 0;
    return Math.max(0, project.clip_end - project.clip_start);
  }, [project]);

  return (
    <section className="preview">
      <div className={`stage ${ratioClass}`}>
        <div className="stage-copy">
          <span>{project?.aspect_ratio ?? "9:16"}</span>
          <strong>{project?.title ?? "Audiogram preview"}</strong>
          <p>{media?.original_name ?? "Choose source media to link transcript and waveform data."}</p>
        </div>
        <div className="waveform" aria-hidden="true">
          {Array.from({ length: 42 }).map((_, index) => (
            <i key={index} style={{ height: `${18 + ((index * 17) % 54)}%` }} />
          ))}
        </div>
        <div className="caption">Local render preview</div>
      </div>
      {project && (
        <div className="controls">
          <label>
            Format
            <select value={project.aspect_ratio} onChange={(event) => onUpdate({ aspect_ratio: event.target.value as Project["aspect_ratio"] })}>
              {aspectRatios.map((ratio) => (
                <option key={ratio}>{ratio}</option>
              ))}
            </select>
          </label>
          <label>
            Start
            <input
              type="number"
              min="0"
              step="0.1"
              value={project.clip_start}
              onChange={(event) => onUpdate({ clip_start: Number(event.target.value) })}
            />
          </label>
          <label>
            End
            <input
              type="number"
              min="0"
              step="0.1"
              value={project.clip_end}
              onChange={(event) => onUpdate({ clip_end: Number(event.target.value) })}
            />
          </label>
          <div className="duration">
            <Monitor size={16} />
            {duration.toFixed(1)}s
          </div>
        </div>
      )}
    </section>
  );
}

function RenderQueue({ jobs, onRender, canRender }: { jobs: Job[]; onRender: () => Promise<void>; canRender: boolean }) {
  const renderJobs = jobs.filter((job) => job.kind === "render").slice(0, 4);
  return (
    <section className="tool-section">
      <div className="section-heading">
        <h2>Render queue</h2>
        <button className="primary compact" disabled={!canRender} onClick={onRender}>
          <Play size={16} /> Render
        </button>
      </div>
      {renderJobs.length === 0 && <p className="muted">Rendered outputs will appear here.</p>}
      {renderJobs.map((job) => (
        <div className="job" key={job.id}>
          <div>
            <strong>{job.message || job.kind}</strong>
            <span>{job.status}</span>
          </div>
          <progress value={job.progress} max={100} />
          {job.status === "complete" && job.result?.downloads?.mp4 ? (
            <a className="download-link" href={job.result.downloads.mp4} title="Download MP4">
              <Download size={16} />
            </a>
          ) : (
            <span />
          )}
          {job.status === "complete" && job.result?.downloads && (
            <div className="download-row">
              <a href={job.result.downloads.mp4}>MP4</a>
              <a href={job.result.downloads.srt}>SRT</a>
              <a href={job.result.downloads.vtt}>VTT</a>
              <a href={job.result.downloads.manifest}>JSON</a>
            </div>
          )}
        </div>
      ))}
    </section>
  );
}

function UserAdmin({
  users,
  currentUserId,
  onReload
}: {
  users: User[];
  currentUserId: string;
  onReload: () => Promise<void>;
}) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [isAdmin, setIsAdmin] = useState(false);
  const [busy, setBusy] = useState(false);

  async function create(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      await api.createUser({ email, password, is_admin: isAdmin });
      setEmail("");
      setPassword("");
      setIsAdmin(false);
      await onReload();
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="tool-section">
      <div className="section-heading">
        <h2>User database</h2>
        <Users size={17} />
      </div>
      <form className="user-form" onSubmit={create}>
        <label>
          Email
          <input value={email} onChange={(event) => setEmail(event.target.value)} type="email" required />
        </label>
        <label>
          Password
          <input
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            type="password"
            minLength={10}
            required
          />
        </label>
        <label className="checkbox-line">
          <input type="checkbox" checked={isAdmin} onChange={(event) => setIsAdmin(event.target.checked)} />
          Admin
        </label>
        <button className="primary compact" disabled={busy}>
          <UserPlus size={16} /> Add user
        </button>
      </form>
      <div className="user-list">
        {users.map((item) => (
          <div className="user-row" key={item.id}>
            <div>
              <strong>{item.email}</strong>
              <span>{item.is_admin ? "Admin" : "User"} · {item.disabled ? "Disabled" : "Active"}</span>
            </div>
            <button
              className="ghost compact"
              disabled={item.id === currentUserId}
              onClick={async () => {
                await api.updateUser(item.id, { disabled: !item.disabled });
                await onReload();
              }}
            >
              {item.disabled ? "Enable" : "Disable"}
            </button>
          </div>
        ))}
      </div>
    </section>
  );
}

function GpuSettings({
  gpus,
  values,
  onSave
}: {
  gpus: Gpu[];
  values: Record<string, string>;
  onSave: (values: Record<string, string>) => Promise<void>;
}) {
  const [local, setLocal] = useState(values);
  useEffect(() => setLocal(values), [values]);

  return (
    <section className="tool-section">
      <div className="section-heading">
        <h2>GPU assignment</h2>
        <Settings size={17} />
      </div>
      {gpus.length === 0 && <p className="muted">No NVIDIA GPUs were visible. CPU fallback remains available.</p>}
      <label>
        Transcription
        <select
          value={local.transcription_gpu_uuid ?? ""}
          onChange={(event) => setLocal({ ...local, transcription_gpu_uuid: event.target.value })}
        >
          <option value="">CPU / automatic</option>
          {gpus.map((gpu) => (
            <option value={gpu.uuid} key={gpu.uuid}>{`${gpu.name} (${gpu.memory})`}</option>
          ))}
        </select>
      </label>
      <label>
        Encoding
        <select
          value={local.encoding_gpu_uuid ?? ""}
          onChange={(event) => setLocal({ ...local, encoding_gpu_uuid: event.target.value })}
        >
          <option value="">CPU / automatic</option>
          {gpus.map((gpu) => (
            <option value={gpu.uuid} key={gpu.uuid}>{`${gpu.name} (${gpu.memory})`}</option>
          ))}
        </select>
      </label>
      <button className="ghost" onClick={() => onSave(local)}>
        <Check size={16} /> Save assignment
      </button>
      <div className="hardware-note">
        <Cpu size={16} />
        UUIDs are stored for stable device selection across restarts.
      </div>
    </section>
  );
}

function formatTime(seconds: number) {
  const minutes = Math.floor(seconds / 60);
  const rest = Math.floor(seconds % 60).toString().padStart(2, "0");
  return `${minutes}:${rest}`;
}

