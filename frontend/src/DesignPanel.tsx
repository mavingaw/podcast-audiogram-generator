import { useEffect, useMemo, useRef, useState } from "react";
import { Image as ImageIcon, Loader2, Palette, Upload, X } from "lucide-react";
import {
  captionBand,
  DEFAULT_ACCENT,
  DEFAULT_BACKGROUND,
  MediaAsset,
  Project,
  api,
} from "./api";
import { play as playSfx } from "./sfx";

/**
 * Look and feel for the exported clip: background, artwork, waveform, captions.
 *
 * Everything here writes into the project scene, which is the single document
 * the renderer reads — so what this panel changes is exactly what the export
 * changes. Nothing in the panel is preview-only.
 */

// Keep in step with FONT_LABELS in backend/app/services/scene.py.
/** Uploaded fonts, loaded once per page and shared by both pickers. */
export function useCustomFonts(): { id: string; family: string }[] {
  const [fonts, setFonts] = useState<{ id: string; family: string }[]>([]);
  useEffect(() => {
    api.fonts().then((r) => setFonts(r.fonts)).catch(() => undefined);
  }, []);
  return fonts;
}

const FONTS: [string, string][] = [
  ["inter", "Inter"],
  ["manrope", "Manrope"],
  ["sora", "Sora"],
  ["bebas", "Bebas Neue (condensed, loud)"],
  ["dejavu", "DejaVu Sans (system)"],
];

// Keep in step with WAVE_STYLE_LABELS in backend/app/services/scene.py.
// Every entry here has a real renderer and a matching preview; the old
// showwaves styles ("line", "edge", "points") drew a thin ribbon whatever
// the audio did and now map to these.
const WAVE_STYLES: [string, string][] = [
  ["pulse", "Live bars — bounce as they speak"],
  ["pulseFine", "Live bars, fine"],
  ["pulseChunky", "Live bars, chunky"],
  ["envelope", "Still bars — the whole clip's shape"],
  ["envelopeFine", "Still bars, fine"],
  ["envelopeChunky", "Still bars, chunky"],
  ["solid", "Solid shape"],
  ["none", "No sound bars"],
];

// Mirrors PLATFORM_SAFE_AREAS in backend/app/services/scene.py.
const PLATFORMS: [string, string][] = [
  ["tiktok", "TikTok"],
  ["reels", "Instagram Reels"],
  ["shorts", "YouTube Shorts"],
  ["feed", "Feed / square"],
];

const CAPTION_PRESETS: [string, string][] = [
  ["social", "Social — big and bold"],
  ["shout", "Shout — uppercase"],
  ["frost", "Frost — frosted glass plate"],
  ["smoke", "Smoke — dark glass plate"],
  ["card", "Card — solid light plate"],
  ["boxed", "Boxed — solid dark plate"],
  ["kinder", "Kinder — baby blue plate"],
  ["pill", "Pill — the spoken word in a box"],
  ["outline", "Outline — thin, no plate"],
  ["clean", "Clean — understated"],
];

/**
 * Palettes chosen for feed legibility: a dark ground so burned-in white
 * captions hold contrast, and an accent bright enough to read at thumbnail
 * size. Each is background / accent / caption.
 */
const THEMES: { name: string; background: string; accent: string; caption: string }[] = [
  { name: "Obsidian", background: "#0B0D11", accent: "#89CFF0", caption: "#F8FAFC" },
  { name: "Midnight", background: "#0b1020", accent: "#5fe9c9", caption: "#ffffff" },
  { name: "Ember", background: "#14100f", accent: "#ffb454", caption: "#ffffff" },
  { name: "Grape", background: "#160f24", accent: "#c792ea", caption: "#ffffff" },
  { name: "Signal", background: "#0d1117", accent: "#ff6b6b", caption: "#ffffff" },
  { name: "Forest", background: "#0e1a14", accent: "#7bd88f", caption: "#ffffff" },
  { name: "Ocean", background: "#061a2b", accent: "#3fb8ff", caption: "#eaf6ff" },
  { name: "Neon", background: "#0a0612", accent: "#ff3fd1", caption: "#f6f0ff" },
  { name: "Lime", background: "#101a0a", accent: "#c8f542", caption: "#f7ffe8" },
  { name: "Rose", background: "#1c0f14", accent: "#ff8fa3", caption: "#fff3f5" },
  { name: "Slate", background: "#1e242c", accent: "#a9b7c6", caption: "#f2f5f8" },
  { name: "Paper", background: "#f5f0e8", accent: "#9a3b3b", caption: "#1a1512" },
  { name: "Frost", background: "#e9eef5", accent: "#2f6fed", caption: "#0f1a2b" },
  { name: "Sand", background: "#f3e9d8", accent: "#c2743a", caption: "#2a1f14" },
  { name: "Mint", background: "#e6f4ee", accent: "#1f7a5c", caption: "#0f2a20" },
  { name: "Lavender", background: "#efeafa", accent: "#6a4fd8", caption: "#1f1740" },
];

type Scene = Record<string, unknown>;

/** Layers the burned-in captions will be drawn over.
 *
 * The renderer positions captions from the caption preset, not from the scene,
 * so a layer can sit in their way without anything in the editor saying so.
 * Projects laid out before the default stack was fixed are the common case —
 * their captions cover the waveform. Rather than silently moving someone's
 * design, the panel points it out and offers to move it.
 */
function captionCollisions(
  scene: Scene,
  aspectRatio: string,
): { layers: Record<string, unknown>[]; band: { top: number; height: number } } {
  const band = captionBand(String(scene.captionPreset ?? "social"), aspectRatio);
  const bottom = band.top + band.height;
  const layers = Array.isArray(scene.layers)
    ? (scene.layers as Record<string, unknown>[])
    : [];
  const hit = layers.filter((layer) => {
    if (layer.visible === false) return false;
    if (layer.type === "captions" || layer.type === "background") return false;
    const top = Number(layer.y ?? 0);
    const height = Number(layer.height ?? 0);
    // Overlap, allowing a hair of rounding.
    return top < bottom - 0.5 && top + height > band.top + 0.5;
  });
  return { layers: hit, band };
}

export function DesignPanel({
  project,
  media,
  onScene,
  onMediaAdded,
  sourceIsVideo = false,
}: {
  project: Project | null;
  media: MediaAsset[];
  onScene: (patch: Scene) => Promise<void>;
  onMediaAdded: (asset: MediaAsset) => void;
  sourceIsVideo?: boolean;
}) {
  const customFonts = useCustomFonts();
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const scene = (project?.scene ?? {}) as Scene;
  const background = (scene.backgroundImage ?? {}) as Record<string, unknown>;
  const images = useMemo(
    () => media.filter((asset) => asset.content_type.startsWith("image/")),
    [media],
  );
  const artwork = useMemo(() => {
    const layers = (scene.layers ?? []) as Record<string, unknown>[];
    return layers.find((layer) => layer.type === "artwork") ?? null;
  }, [scene.layers]);

  async function upload(file: File) {
    setUploading(true);
    setError(null);
    try {
      const result = await api.uploadMedia(file);
      onMediaAdded(result.media);
      // A freshly uploaded image is almost always the one you meant to use.
      await onScene({
        backgroundImage: { ...background, mediaId: result.media.id },
      });
      playSfx("confirm");
    } catch (cause) {
      setError((cause as Error).message);
      playSfx("error");
    } finally {
      setUploading(false);
    }
  }

  function patchBackground(patch: Record<string, unknown>) {
    void onScene({ backgroundImage: { ...background, ...patch } });
  }

  function setArtwork(mediaId: string | null) {
    const layers = [...((scene.layers ?? []) as Record<string, unknown>[])];
    const index = layers.findIndex((layer) => layer.type === "artwork");
    if (index >= 0) {
      layers[index] = { ...layers[index], mediaId };
    }
    void onScene({ layers });
  }

  const blur = Number(background.blur ?? 18);
  const dim = Number(background.dim ?? 0.35);
  const backgroundId = (background.mediaId as string) ?? "";

  return (
    <div className="design-panel">
      <div className="inspector-heading">
        <span className="sidebar-label">
          <Palette size={12} /> Design
        </span>
      </div>

      <div className="theme-row">
        {THEMES.map((theme) => (
          <button
            key={theme.name}
            className={`theme-chip ${scene.accent === theme.accent && scene.background === theme.background ? "selected" : ""}`}
            title={theme.name}
            style={{ background: theme.background, borderColor: theme.accent }}
            onClick={() => {
              playSfx("select");
              void onScene({
                background: theme.background,
                accent: theme.accent,
                captionColor: theme.caption,
              });
            }}
          >
            <i style={{ background: theme.accent }} />
          </button>
        ))}
      </div>

      <label>
        Background colour
        <input
          type="color"
          value={String(scene.background ?? DEFAULT_BACKGROUND)}
          onChange={(event) => void onScene({ background: event.target.value })}
        />
      </label>
      <label>
        Highlight colour
        <input
          type="color"
          value={String(scene.accent ?? DEFAULT_ACCENT)}
          onChange={(event) => void onScene({ accent: event.target.value })}
        />
      </label>

      <div className="design-group">
        <span className="sidebar-label">Background image</span>
        {sourceIsVideo && (
          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={scene.videoBackground !== false}
              onChange={(event) => void onScene({ videoBackground: event.target.checked })}
            />
            Use the video's own picture as the background
          </label>
        )}
        <p className="muted">
          A soft, blurred version of your cover picture fills the whole video
          behind everything else. Turn the blur down to see it clearly.
        </p>
        <div className="image-picker">
          <button
            className={`image-chip none ${backgroundId ? "" : "selected"}`}
            title="No background image"
            onClick={() => patchBackground({ mediaId: null })}
          >
            <X size={13} />
          </button>
          {images.map((asset) => (
            <button
              key={asset.id}
              className={`image-chip ${backgroundId === asset.id ? "selected" : ""}`}
              title={asset.original_name}
              onClick={() => {
                playSfx("select");
                patchBackground({ mediaId: asset.id });
              }}
            >
              <img src={api.mediaFileUrl(asset.id)} alt="" loading="lazy" />
            </button>
          ))}
          <button
            className="image-chip upload"
            title="Upload an image"
            onClick={() => fileRef.current?.click()}
            disabled={uploading}
          >
            {uploading ? <Loader2 className="spin" size={14} /> : <Upload size={14} />}
          </button>
        </div>
        <input
          ref={fileRef}
          type="file"
          accept="image/png,image/jpeg,image/webp"
          hidden
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) void upload(file);
            event.target.value = "";
          }}
        />
        {error && <p className="error">{error}</p>}

        {backgroundId && (
          <>
            <label>
              Blur {blur.toFixed(0)}
              <input
                type="range"
                min="0"
                max="60"
                step="1"
                value={blur}
                onChange={(event) => patchBackground({ blur: Number(event.target.value) })}
              />
            </label>
            <label>
              Darken {Math.round(dim * 100)}%
              <input
                type="range"
                min="0"
                max="95"
                step="1"
                value={Math.round(dim * 100)}
                onChange={(event) =>
                  patchBackground({ dim: Number(event.target.value) / 100 })
                }
              />
            </label>
          </>
        )}
      </div>

      {artwork && (
        <div className="design-group">
          <span className="sidebar-label">
            <ImageIcon size={12} /> Cover art
          </span>
          <div className="image-picker">
            <button
              className={`image-chip none ${artwork.mediaId ? "" : "selected"}`}
              title="No cover art"
              onClick={() => setArtwork(null)}
            >
              <X size={13} />
            </button>
            {images.map((asset) => (
              <button
                key={asset.id}
                className={`image-chip ${artwork.mediaId === asset.id ? "selected" : ""}`}
                title={asset.original_name}
                onClick={() => {
                  playSfx("select");
                  setArtwork(asset.id);
                }}
              >
                <img src={api.mediaFileUrl(asset.id)} alt="" loading="lazy" />
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="design-group">
        <span className="sidebar-label">Where is this going?</span>
        <p className="muted">
          Pick the app you will post to and Kinder shades the parts of the
          screen that app covers with its own buttons, so nothing important
          ends up hidden.
        </p>
        <select
          value={String(scene.platform ?? "")}
          onChange={(event) => void onScene({ platform: event.target.value })}
        >
          <option value="">No guide</option>
          {PLATFORMS.map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </div>

      <div className="design-group">
        <span className="sidebar-label">Sound bars</span>
        <p className="muted">
          The bars that move with the voice. Choose a shape, or turn them off.
        </p>
        <label>
          Style
          <select
            value={String(scene.waveStyle ?? "envelope")}
            onChange={(event) => void onScene({ waveStyle: event.target.value })}
          >
            {WAVE_STYLES.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="design-group">
        <span className="sidebar-label">Font</span>
        <p className="muted">
          One font for the title, one for the words on screen. Bebas Neue is
          tall and loud — great for a title, hard to read as captions.
        </p>
        <label>
          Titles
          <select
            value={String(scene.font ?? "inter")}
            onChange={(event) => void onScene({ font: event.target.value })}
          >
            {FONTS.map(([id, label]) => (
              <option key={id} value={id}>{label}</option>
            ))}
            {customFonts.map((f) => (
              <option key={f.id} value={f.id}>{f.family} (yours)</option>
            ))}
          </select>
        </label>
        <label>
          Captions
          <select
            value={String(scene.captionFont ?? "inter")}
            onChange={(event) => void onScene({ captionFont: event.target.value })}
          >
            {FONTS.map(([id, label]) => (
              <option key={id} value={id}>{label}</option>
            ))}
            {customFonts.map((f) => (
              <option key={f.id} value={f.id}>{f.family} (yours)</option>
            ))}
          </select>
        </label>
      </div>

      <div className="design-group">
        <span className="sidebar-label">Are the words early or late?</span>
        <p className="muted">
          If the captions light up a moment after the word is said, slide
          this to bring them earlier. Watch the preview to check.
        </p>
        <label>
          {Number(scene.captionOffset ?? 0) === 0
            ? "In step with the audio"
            : `${Number(scene.captionOffset ?? 0) > 0 ? "Later" : "Earlier"} by ${Math.abs(Number(scene.captionOffset ?? 0)).toFixed(2)}s`}
          <input
            type="range"
            min={-1}
            max={1}
            step={0.05}
            value={Number(scene.captionOffset ?? 0)}
            onChange={(event) =>
              void onScene({ captionOffset: Number(event.target.value) })
            }
          />
        </label>
      </div>

      <div className="design-group">
        <span className="sidebar-label">Voice volume</span>
        <p className="muted">
          The finished video is always set to the volume the apps expect, so
          you rarely need this. Fade in and fade out soften the very start
          and end of the clip.
        </p>
        <label>
          Level {Number(scene.voiceGainDb ?? 0) >= 0 ? "+" : ""}
          {Number(scene.voiceGainDb ?? 0).toFixed(1)} dB
          <input
            type="range"
            min={-12}
            max={12}
            step={0.5}
            value={Number(scene.voiceGainDb ?? 0)}
            onChange={(event) =>
              void onScene({ voiceGainDb: Number(event.target.value) })
            }
          />
        </label>
        <div className="fade-row">
          <label>
            Fade in {Number(scene.fadeIn ?? 0).toFixed(1)}s
            <input
              type="range"
              min={0}
              max={3}
              step={0.1}
              value={Number(scene.fadeIn ?? 0)}
              onChange={(event) => void onScene({ fadeIn: Number(event.target.value) })}
            />
          </label>
          <label>
            Fade out {Number(scene.fadeOut ?? 0).toFixed(1)}s
            <input
              type="range"
              min={0}
              max={3}
              step={0.1}
              value={Number(scene.fadeOut ?? 0)}
              onChange={(event) => void onScene({ fadeOut: Number(event.target.value) })}
            />
          </label>
        </div>
      </div>

      <div className="design-group">
        <span className="sidebar-label">Captions</span>
        <p className="muted">
          Most people watch with the sound off, so the words on screen are
          what they actually get. Pick a style below.
        </p>
        <label className="checkbox-row">
          <input
            type="checkbox"
            checked={scene.wordHighlight !== false}
            onChange={(event) =>
              void onScene({ wordHighlight: event.target.checked })
            }
          />
          <span>
            Highlight each word as it is spoken
            <small>Uses the transcript's word timings. Off burns whole lines.</small>
          </span>
        </label>
        <CaptionCollisionNotice
          scene={scene}
          aspectRatio={project?.aspect_ratio ?? "9:16"}
          onScene={onScene}
        />
        <label>
          Style
          <select
            value={String(scene.captionPreset ?? "social")}
            onChange={(event) => void onScene({ captionPreset: event.target.value })}
          >
            {CAPTION_PRESETS.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Colour
          <input
            type="color"
            value={String(scene.captionColor ?? "#ffffff")}
            onChange={(event) => void onScene({ captionColor: event.target.value })}
          />
        </label>
      </div>
    </div>
  );
}

/** Warns when something sits where the captions will be burned in, and offers
 *  to move it below them. */
function CaptionCollisionNotice({
  scene,
  aspectRatio,
  onScene,
}: {
  scene: Scene;
  aspectRatio: string;
  onScene: (patch: Scene) => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const { layers: hit, band } = captionCollisions(scene, aspectRatio);
  if (!hit.length) return null;

  // Vertical shapes lose their bottom fifth to the platform's own interface.
  const floor = aspectRatio === "16:9" ? 94 : 80;
  const names = hit
    .map((layer) => String(layer.name ?? layer.type ?? "a layer"))
    .join(", ");

  async function moveClear() {
    setBusy(true);
    try {
      const bottom = band.top + band.height + 1;
      const moved = (scene.layers as Record<string, unknown>[]).map((layer) => {
        if (!hit.includes(layer)) return layer;
        const height = Math.max(5, Math.min(Number(layer.height ?? 9), floor - bottom));
        return { ...layer, y: Number(bottom.toFixed(2)), height };
      });
      await onScene({ layers: moved });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="collision-notice">
      <p>
        Captions will be drawn over <strong>{names}</strong>. The renderer places
        them from the caption style, not from the canvas.
      </p>
      <button className="ghost-button" disabled={busy} onClick={() => void moveClear()}>
        Move below captions
      </button>
    </div>
  );
}
