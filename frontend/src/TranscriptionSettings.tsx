import { useEffect, useState } from "react";
import { Cpu, Loader2, Mic } from "lucide-react";
import { TranscriptionSettings as Settings, api } from "./api";

/**
 * Install-wide transcription and encoding settings.
 *
 * These belong to the machine, not to a project: which Whisper model this box
 * can afford to run, whether a GPU is doing the work, and what language to
 * assume. Showing the resolved device alongside the choice is the point — it is
 * the difference between "GPU transcription is configured" and "GPU
 * transcription is actually happening".
 */
const LANGUAGES: [string, string][] = [
  ["", "Detect automatically"],
  ["en", "English"],
  ["es", "Spanish"],
  ["fr", "French"],
  ["de", "German"],
  ["pt", "Portuguese"],
  ["it", "Italian"],
  ["nl", "Dutch"],
  ["sw", "Swahili"],
  ["ar", "Arabic"],
  ["hi", "Hindi"],
  ["zh", "Chinese"],
  ["ja", "Japanese"],
];

export function TranscriptionPanel({ isAdmin }: { isAdmin: boolean }) {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .transcriptionSettings()
      .then(setSettings)
      .catch((cause: Error) => setError(cause.message));
  }, []);

  async function save(patch: { model?: string; language?: string; enabled?: boolean }) {
    setSaving(true);
    setError(null);
    try {
      setSettings(await api.saveTranscriptionSettings(patch));
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setSaving(false);
    }
  }

  if (error && !settings) return <p className="error">{error}</p>;
  if (!settings) return <Loader2 className="spin" size={16} />;

  return (
    <div className="transcription-panel">
      <span className="sidebar-label">
        <Mic size={12} /> Transcription
      </span>

      {!settings.installed && (
        <p className="muted">
          faster-whisper is not installed, so captions fall back to a
          placeholder. Install it to caption with real speech.
        </p>
      )}

      <div className="runtime-badges">
        <span className={`runtime-badge ${settings.device === "cuda" ? "hot" : ""}`}>
          <Cpu size={11} />
          Transcribe: {settings.device === "cuda" ? "GPU" : "CPU"} · {settings.compute_type}
        </span>
        <span className={`runtime-badge ${settings.encoder.hardware ? "hot" : ""}`}>
          <Cpu size={11} />
          Encode: {settings.encoder.hardware ? "GPU" : "CPU"} ·{" "}
          {settings.encoder.ffmpeg_encoder}
        </span>
      </div>

      <label>
        Model
        <md-outlined-select
          value={settings.model}
          disabled={!isAdmin || saving}
          onInput={(event) => void save({ model: event.target.value })}
        >
          {settings.models.map((name) => (
            <md-select-option key={name} value={name}><div slot="headline">{name}</div></md-select-option>
          ))}
        </md-outlined-select>
      </label>

      <label>
        Language
        <md-outlined-select
          value={settings.language}
          disabled={!isAdmin || saving}
          onInput={(event) => void save({ language: event.target.value })}
        >
          {LANGUAGES.map(([value, label]) => (
            <md-select-option key={value} value={value}><div slot="headline">{label}</div></md-select-option>
          ))}
        </md-outlined-select>
      </label>

      <label className="music-toggle">
        <md-switch selected={settings.enabled}
          disabled={!isAdmin || saving}
          onInput={(event) => void save({ enabled: (event.target as unknown as { selected: boolean }).selected })}></md-switch>
        Transcribe uploads automatically
      </label>

      {error && <p className="error">{error}</p>}
      {!isAdmin && <p className="muted">Only an admin can change these.</p>}
    </div>
  );
}
