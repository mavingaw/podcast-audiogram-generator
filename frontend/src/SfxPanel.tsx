import { useEffect, useRef, useState } from "react";
import { Pause, Play, Plus, Search, Sparkles, X } from "lucide-react";
import { Sound, api } from "./api";
import { play as playUi } from "./sfx";

/**
 * One-shot sound effects placed at points in the clip.
 *
 * A riser under the first sentence, a record scratch on the punchline, a
 * stinger at the end. Search the library, press the plus and the effect lands
 * at the playhead; the export delays it to that moment and mixes it under
 * the voice and the bed.
 *
 * Stored on the scene as `sfx: [{soundId, at, gainDb, title}]` in clip
 * seconds. The title is kept so the list can name the cue without a lookup,
 * and so a cue whose sound was later removed from the library still says
 * what it was.
 */
export type SfxCue = { soundId: string; at: number; gainDb: number; title?: string; mediaId?: string };

export function SfxPanel({
  cues,
  playhead,
  clipDuration,
  onChange,
  onSeek,
}: {
  cues: SfxCue[];
  /** Clip-relative seconds. */
  playhead: number;
  clipDuration: number;
  onChange: (next: SfxCue[]) => void;
  onSeek?: (clipSeconds: number) => void;
}) {
  const [sounds, setSounds] = useState<Sound[]>([]);
  const [search, setSearch] = useState("whoosh");
  const [loading, setLoading] = useState(false);
  const [previewId, setPreviewId] = useState<string | null>(null);
  const previewRef = useRef<HTMLAudioElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    const timer = window.setTimeout(() => {
      api
        .sounds({ kind: "sfx", search, limit: 60 })
        .then((payload) => {
          if (!cancelled) setSounds(payload.sounds);
        })
        .catch(() => {
          if (!cancelled) setSounds([]);
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });
    }, 220);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [search]);

  useEffect(() => () => previewRef.current?.pause(), []);

  function togglePreview(sound: Sound) {
    const current = previewRef.current;
    if (previewId === sound.id && current) {
      current.pause();
      setPreviewId(null);
      return;
    }
    current?.pause();
    const audio = new Audio(sound.preview_url);
    audio.volume = 0.7;
    audio.onended = () => setPreviewId(null);
    previewRef.current = audio;
    setPreviewId(sound.id);
    void audio.play().catch(() => setPreviewId(null));
  }

  function add(sound: Sound) {
    playUi("confirm");
    const at = Math.max(0, Math.min(clipDuration - 0.1, playhead));
    onChange(
      [...cues, { soundId: sound.id, at: Math.round(at * 100) / 100, gainDb: 0, title: sound.title }]
        .sort((a, b) => a.at - b.at)
        .slice(0, 16),
    );
  }

  function patch(index: number, updates: Partial<SfxCue>) {
    onChange(cues.map((cue, i) => (i === index ? { ...cue, ...updates } : cue)));
  }

  const stamp = (seconds: number) =>
    `${Math.floor(seconds / 60)}:${(seconds % 60).toFixed(1).padStart(4, "0")}`;

  return (
    <div className="design-group sfx-panel">
      <span className="sidebar-label">
        <Sparkles size={13} /> Sound effects
      </span>
      {cues.length > 0 ? (
        <ul className="sfx-cues">
          {cues.map((cue, index) => (
            <li key={`${cue.soundId}-${index}`}>
              <button
                className="sfx-time"
                title="Jump to this moment"
                onClick={() => onSeek?.(cue.at)}
              >
                {stamp(cue.at)}
              </button>
              <span className="sfx-name">{cue.title ?? "Effect"}</span>
              <input
                type="range"
                min={-24}
                max={12}
                step={1}
                value={cue.gainDb}
                title={`${cue.gainDb > 0 ? "+" : ""}${cue.gainDb} dB`}
                onChange={(event) => patch(index, { gainDb: Number(event.target.value) })}
              />
              <button
                className="layer-action"
                title="Remove"
                onClick={() => {
                  playUi("cancel");
                  onChange(cues.filter((_, i) => i !== index));
                }}
              >
                <X size={13} />
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">
          Park the playhead where the effect should hit, then press + on one
          below.
        </p>
      )}
      <label className="search-field">
        <Search size={14} />
        <input
          value={search}
          placeholder="whoosh, riser, stinger, applause…"
          onChange={(event) => setSearch(event.target.value)}
        />
      </label>
      <div className="sfx-results">
        {loading && sounds.length === 0 && <p className="muted">Searching…</p>}
        {!loading && sounds.length === 0 && (
          <p className="muted">Nothing in the library matches that.</p>
        )}
        {sounds.map((sound) => (
          <div className="sfx-result" key={sound.id}>
            <button
              className="icon-button"
              title={previewId === sound.id ? "Stop" : "Preview"}
              onClick={() => togglePreview(sound)}
            >
              {previewId === sound.id ? <Pause size={13} /> : <Play size={13} />}
            </button>
            <span className="sfx-name" title={`${sound.title} — ${sound.author}`}>
              {sound.title}
              {sound.duration_seconds ? (
                <small> {sound.duration_seconds.toFixed(1)}s</small>
              ) : null}
            </span>
            <button
              className="icon-button"
              title={`Add at ${stamp(playhead)}`}
              onClick={() => add(sound)}
            >
              <Plus size={14} />
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
