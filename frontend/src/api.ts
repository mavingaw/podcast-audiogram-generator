export type User = {
  id: string;
  email: string;
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
  transcript: Transcript | null;
};

export type Transcript = {
  language: string;
  duration: number;
  segments: TranscriptSegment[];
};

export type TranscriptSegment = {
  id: number;
  speaker: string;
  start: number;
  end: number;
  text: string;
};

export type Project = {
  id: string;
  media_id: string | null;
  title: string;
  clip_start: number;
  clip_end: number;
  aspect_ratio: "9:16" | "1:1" | "16:9";
  scene: Record<string, unknown>;
};

export type Job = {
  id: string;
  kind: string;
  status: "queued" | "running" | "complete" | "failed" | "canceled";
  progress: number;
  subject_id: string | null;
  message: string;
  error: string | null;
  result: {
    downloads?: Record<string, string>;
    files?: Record<string, string>;
  } | null;
};

export type Gpu = {
  index: string;
  uuid: string;
  name: string;
  memory: string;
  driver: string;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    credentials: "include",
    headers: init?.body instanceof FormData ? undefined : { "Content-Type": "application/json" },
    ...init
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(payload.detail ?? response.statusText);
  }
  return response.json() as Promise<T>;
}

export const api = {
  bootstrapState: () => request<{ initialized: boolean }>("/api/bootstrap"),
  bootstrap: (email: string, password: string) =>
    request<{ user: User }>("/api/bootstrap", { method: "POST", body: JSON.stringify({ email, password }) }),
  me: () => request<{ user: User }>("/api/me"),
  login: (email: string, password: string) =>
    request<{ user: User }>("/api/auth/login", { method: "POST", body: JSON.stringify({ email, password }) }),
  users: () => request<{ users: User[] }>("/api/users"),
  createUser: (payload: { email: string; password: string; is_admin: boolean }) =>
    request<{ user: User }>("/api/users", { method: "POST", body: JSON.stringify(payload) }),
  updateUser: (userId: string, payload: { is_admin?: boolean; disabled?: boolean; password?: string }) =>
    request<{ user: User }>(`/api/users/${userId}`, { method: "PATCH", body: JSON.stringify(payload) }),
  gpus: () => request<{ gpus: Gpu[] }>("/api/gpus"),
  gpuSettings: () => request<Record<string, string>>("/api/settings/gpu"),
  saveGpuSettings: (payload: { transcription_gpu_uuid?: string; encoding_gpu_uuid?: string }) =>
    request<{ ok: boolean }>("/api/settings/gpu", { method: "PUT", body: JSON.stringify(payload) }),
  media: () => request<{ media: MediaAsset[] }>("/api/media"),
  uploadMedia: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<{ media: MediaAsset; jobs: Job[] }>("/api/media/upload", { method: "POST", body: form });
  },
  projects: () => request<{ projects: Project[] }>("/api/projects"),
  createProject: (title: string, media_id?: string) =>
    request<{ project: Project }>("/api/projects", { method: "POST", body: JSON.stringify({ title, media_id }) }),
  updateProject: (project: Project, updates: Partial<Project>) =>
    request<{ project: Project }>(`/api/projects/${project.id}`, { method: "PATCH", body: JSON.stringify(updates) }),
  renderProject: (project: Project) => request<{ job: Job }>(`/api/projects/${project.id}/render`, { method: "POST" }),
  jobs: () => request<{ jobs: Job[] }>("/api/jobs")
};

