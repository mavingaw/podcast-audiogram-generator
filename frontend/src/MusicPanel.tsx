import { useEffect, useMemo, useRef, useState } from "react";
import { Music, Pause, Play, Search, X } from "lucide-react";
import { api, MusicBed, Sound, defaultMusicBed } from "./api";
import { play as playSfx } from "./sfx";

/**
 * Browse the licensed music library and set the project's music bed.
 *
 * Preview playback happens here rather than in the render pipeline, so picking
 * a track costs nothing until the project is exported. The bed itself is one
 * scene field, not a canvas layer: it has no position and always spans the clip.
 */
export function MusicPanel({
  bed,
  clipDuration,
  onChange,
}: {
  bed: MusicBed | null;
  clipDuration: number;
  onChange: (next: MusicBed | null) => void;
}) {
  const [sounds, setSounds] = useState<Sound[]>([]);
  const [genres, setGenres] = useState<string[]>([]);
  const [genre, setGenre] = useState("");
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [previewId, setPreviewId] = useState<string | null>(null);
  const previewRef = useRef<HTMLAudioElement | null>(null);

  useEffect(() => {
    api
      .soundGenres()
      .then((payload) => setGenres(payload.genres))
      .catch(() => setGenres([]));
  }, []);

  // Debounced so typing does not fire a request per keystroke.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    const timer = window.setTimeout(() => {
      api
        .sounds({ kind: "music", genre, search, limit: 120 })
        .then((payload) => {
          if (!cancelled) {
            setSounds(payload.sounds);
            setError(null);
          }
        })
        .catch((cause: Error) => {
          if (!cancelled) setError(cause.message);
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });
    }, 220);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [genre, search]);

  useEffect(() => () => previewRef.current?.pause(), []);

  const selected = useMemo(
    () => sounds.find((sound) => sound.id === bed?.soundId) ?? null,
    [sounds, bed?.soundId],
  );

  function togglePreview(sound: Sound) {
    const current = previewRef.current;
    if (previewId === sound.id && current) {
      current.pause();
      setPreviewId(null);
      return;
    }
    current?.pause();
    const audio = new Audio(sound.preview_url);
    audio.volume = 0.6;
    audio.onended = () => setPreviewId(null);
    previewRef.current = audio;
    setPreviewId(sound.id);
    void audio.play().catch(() => setPreviewId(null));
  }

  function choose(sound: Sound) {
    playSfx("confirm");
    onChange({ ...defaultMusicBed(sound.id), loop: sound.seamless_loop });
  }

  function patch(updates: Partial<MusicBed>) {
    if (!bed) return;
    onChange({ ...bed, ...updates });
  }

  return (
    <div className="music-panel">
      <div className="inspector-heading">
        <span className="sidebar-label">Music bed</span>
        {bed && (
          <button
            className="layer-action"
            title="Remove music bed"
            onClick={() => {
              playSfx("cancel");
              onChange(null);
            }}
          >
            <X size={14} />
          </button>
        )}
      </div>

      {bed && (
        <div className="music-selected">
          <strong>{selected?.title ?? "Selected track"}</strong>
          <small>
            {selected ? `${selected.genre} · ${selected.attribution}` : "Loading track details"}
          </small>
          <label>
            Level {bed.gainDb.toFixed(0)} dB
            <input
              type="range"
              min="-40"
              max="0"
              step="1"
              value={bed.gainDb}
              onChange={(event) => patch({ gainDb: Number(event.target.value) })}
            />
          </label>
          <label>
            Dip under speech {bed.duckDb.toFixed(0)} dB
            <input
              type="range"
              min="-30"
              max="0"
              step="1"
              value={bed.duckDb}
              onChange={(event) => patch({ duckDb: Number(event.target.value) })}
            />
          </label>
          <div className="mini-fields">
            <label>
              Fade in
              <input
                type="number"
                min="0"
                max={clipDuration / 2}
                step="0.5"
                value={bed.fadeInSeconds}
                onChange={(event) => patch({ fadeInSeconds: Number(event.target.value) })}
              />
            </label>
            <label>
              Fade out
              <input
                type="number"
                min="0"
                max={clipDuration / 2}
                step="0.5"
                value={bed.fadeOutSeconds}
                onChange={(event) => patch({ fadeOutSeconds: Number(event.target.value) })}
              />
            </label>
          </div>
          <label className="music-toggle">
            <input
              type="checkbox"
              checked={bed.loop}
              onChange={(event) => patch({ loop: event.target.checked })}
            />
            Loop to fill the clip
          </label>
        </div>
      )}

      <div className="music-filters">
        <label className="music-search">
          <Search size={13} />
          <input
            placeholder="Search title, genre, or mood"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </label>
        <select value={genre} onChange={(event) => setGenre(event.target.value)}>
          <option value="">All genres</option>
          {genres.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>
      </div>

      {error && <p className="error">{error}</p>}
      {!error && !loading && sounds.length === 0 && (
        <p className="muted">
          No tracks yet. Import a licensed pack with
          <code> python -m app.cli.import_library</code>.
        </p>
      )}

      <div className="music-results">
        {sounds.map((sound) => (
          <div
            className={`music-row ${bed?.soundId === sound.id ? "selected" : ""}`}
            key={sound.id}
          >
            <button
              className="layer-action"
              title={previewId === sound.id ? "Stop preview" : "Preview track"}
              onClick={() => togglePreview(sound)}
            >
              {previewId === sound.id ? <Pause size={13} /> : <Play size={13} />}
            </button>
            <button className="music-pick" onClick={() => choose(sound)}>
              <strong>{sound.title}</strong>
              <small>
                {sound.genre}
                {sound.duration_seconds ? ` · ${formatClock(sound.duration_seconds)}` : ""}
                {sound.seamless_loop ? " · loops" : ""}
              </small>
            </button>
            {bed?.soundId === sound.id && <Music size={13} />}
          </div>
        ))}
      </div>
    </div>
  );
}

function formatClock(seconds: number) {
  const whole = Math.round(seconds);
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}
