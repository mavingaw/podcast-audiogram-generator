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
 * Look and feel for the exported clip, in three pieces the Studio places
 * where they belong: the pictures at the top of the panel, the text controls
 * together in one place, and the rest (colours, sound bars, the dials only
 * an "Everything" user wants) under Design.
 *
 * Everything here writes into the project scene, which is the single document
 * the renderer reads — so what these panels change is exactly what the export
 * changes. Nothing in them is preview-only.
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

/**
 * The pictures: the cover art and the blurred backdrop, with the upload
 * button right there. At the top of the Studio panel, because adding your
 * own picture is the first thing most people come to do.
 */
export function PicturesPanel({
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
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  // Which picture an upload is for: the cover art or the backdrop.
  const uploadTarget = useRef<"artwork" | "background">("artwork");

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

  function setArtwork(mediaId: string | null) {
    const layers = [...((scene.layers ?? []) as Record<string, unknown>[])];
    const index = layers.findIndex((layer) => layer.type === "artwork");
    if (index >= 0) {
      layers[index] = { ...layers[index], mediaId };
    }
    void onScene({ layers });
  }

  function patchBackground(patch: Record<string, unknown>) {
    void onScene({ backgroundImage: { ...background, ...patch } });
  }

  async function upload(file: File) {
    setUploading(true);
    setError(null);
    try {
      const result = await api.uploadMedia(file);
      onMediaAdded(result.media);
      // A freshly uploaded picture is almost always the one you meant to use.
      if (uploadTarget.current === "background" || !artwork) {
        await onScene({ backgroundImage: { ...background, mediaId: result.media.id } });
      } else {
        setArtwork(result.media.id);
      }
      playSfx("confirm");
    } catch (cause) {
      setError((cause as Error).message);
      playSfx("error");
    } finally {
      setUploading(false);
    }
  }

  function pick(target: "artwork" | "background") {
    uploadTarget.current = target;
    fileRef.current?.click();
  }

  const blur = Number(background.blur ?? 18);
  const dim = Number(background.dim ?? 0.35);
  const backgroundId = (background.mediaId as string) ?? "";

  return (
    <div className="design-panel pictures-panel">
      <div className="inspector-heading">
        <span className="sidebar-label">
          <ImageIcon size={12} /> Pictures
        </span>
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

      {artwork && (
        <div className="design-group cover-art">
          <span className="sidebar-label">Cover art</span>
          <p className="muted">
            Pick a picture or add a new one. On the video, drag it to move it
            and drag its corners to crop it.
          </p>
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
            <button
              className="image-chip upload"
              title="Add a picture"
              onClick={() => pick("artwork")}
              disabled={uploading}
            >
              {uploading ? <Loader2 className="spin" size={14} /> : <Upload size={14} />}
            </button>
          </div>
          <button className="ghost compact upload-button" onClick={() => pick("artwork")} disabled={uploading}>
            <Upload size={13} /> Add a picture
          </button>
          {images.length === 0 && (
            <p className="muted">No pictures yet — add your show's cover.</p>
          )}
        </div>
      )}

      <details className="design-group design-fold">
        <summary className="sidebar-label">Background picture</summary>
        {sourceIsVideo && (
          <label className="checkbox-row">
            <md-switch
              selected={scene.videoBackground !== false}
              onInput={(event) => void onScene({ videoBackground: (event.target as unknown as { selected: boolean }).selected })}
            ></md-switch>
            Use the video's own picture as the background
          </label>
        )}
        <p className="muted">
          A soft, blurred version of a picture fills the whole video behind
          everything else. Turn the blur down to see it clearly.
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
            onClick={() => pick("background")}
            disabled={uploading}
          >
            {uploading ? <Loader2 className="spin" size={14} /> : <Upload size={14} />}
          </button>
        </div>
        {backgroundId && (
          <>
            <label>
              Blur {blur.toFixed(0)}
              <md-slider
                min={0}
                max={60}
                step={1}
                labeled
                value={blur}
                onInput={(event) => patchBackground({ blur: Number((event.target as unknown as { value: number }).value) })}
              />
            </label>
            <label>
              Darken {Math.round(dim * 100)}%
              <md-slider
                min={0}
                max={95}
                step={1}
                labeled
                value={Math.round(dim * 100)}
                onInput={(event) =>
                  patchBackground({ dim: Number((event.target as unknown as { value: number }).value) / 100 })
                }
              />
            </label>
          </>
        )}
      </details>
      {error && <p className="error">{error}</p>}
    </div>
  );
}

/**
 * The text controls that belong to the whole clip: the style the captions
 * are drawn in, whether the spoken word lights up, the font, the colour and
 * the size. The Studio puts these beside the selected text layer's own words
 * so everything about text is in one place.
 */
export function TextStylePanel({
  project,
  onScene,
}: {
  project: Project | null;
  onScene: (patch: Scene) => Promise<void>;
}) {
  const customFonts = useCustomFonts();
  const scene = (project?.scene ?? {}) as Scene;
  const font = String(scene.captionFont ?? scene.font ?? "inter");
  return (
    <div className="text-style">
      <label>
        Text style
        <md-outlined-select
          value={String(scene.captionPreset ?? "social")}
          onInput={(event) => void onScene({ captionPreset: (event.target as unknown as { value: string }).value })}
        >
          {CAPTION_PRESETS.map(([value, label]) => (
            <md-select-option key={value} value={value} selected={String(scene.captionPreset ?? "social") === value}>
              <div slot="headline">{label}</div>
            </md-select-option>
          ))}
        </md-outlined-select>
      </label>
      <label className="checkbox-row plain">
        <input
          type="checkbox"
          checked={scene.wordHighlight !== false}
          onChange={(event) => void onScene({ wordHighlight: event.target.checked })}
        />
        <span>Highlight each word as it is spoken</span>
      </label>
      <label>
        Font
        <md-outlined-select
          value={font}
          onInput={(event) => {
            // One font for everything on the video: the captions and any
            // text you add. Two pickers was one too many.
            const value = (event.target as unknown as { value: string }).value;
            void onScene({ font: value, captionFont: value });
          }}
        >
          {FONTS.map(([id, label]) => (
            <md-select-option key={id} value={id} selected={font === id}><div slot="headline">{label}</div></md-select-option>
          ))}
          {customFonts.map((f) => (
            <md-select-option key={f.id} value={f.id} selected={font === f.id}><div slot="headline">{f.family} (yours)</div></md-select-option>
          ))}
        </md-outlined-select>
      </label>
      <label>
        Caption colour
        <input
          type="color"
          value={String(scene.captionColor ?? "#ffffff")}
          onChange={(event) => void onScene({ captionColor: event.target.value })}
        />
      </label>
      <label>
        Caption size
        <md-slider
          min={60} max={160} step={5} labeled
          value={Math.round(Number(scene.captionScale ?? 1) * 100)}
          onInput={(event) => void onScene({ captionScale: Number((event.target as unknown as { value: number }).value) / 100 })}
        ></md-slider>
      </label>
      <CaptionCollisionNotice
        scene={scene}
        aspectRatio={project?.aspect_ratio ?? "9:16"}
        onScene={onScene}
      />
    </div>
  );
}

/** Colours, sound bars, and — under "Everything" — the finer dials. */
export function DesignPanel({
  project,
  onScene,
  advanced = false,
}: {
  project: Project | null;
  onScene: (patch: Scene) => Promise<void>;
  /** Show the dials most people never need (platform guide, timing, volume). */
  advanced?: boolean;
}) {
  const scene = (project?.scene ?? {}) as Scene;

  return (
    <div className="design-panel">
      <div className="inspector-heading">
        <span className="sidebar-label">
          <Palette size={12} /> Colours
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

      <details className="design-group design-fold">
        <summary className="sidebar-label">Sound bars</summary>
        <p className="muted">
          The bars that move with the voice. Choose a shape, or turn them off.
        </p>
        <label>
          Style
          <md-outlined-select
            value={String(scene.waveStyle ?? "envelope")}
            onInput={(event) => void onScene({ waveStyle: (event.target as unknown as { value: string }).value })}
          >
            {WAVE_STYLES.map(([value, label]) => (
              <md-select-option key={value} value={value} selected={String(scene.waveStyle ?? "envelope") === value}>
                <div slot="headline">{label}</div>
              </md-select-option>
            ))}
          </md-outlined-select>
        </label>
      </details>

      {advanced && (
        <>
          <details className="design-group design-fold">
            <summary className="sidebar-label">Where is this going?</summary>
            <p className="muted">
              Pick the app you will post to and Kinder shades the parts of the
              screen that app covers with its own buttons, so nothing important
              ends up hidden.
            </p>
            <md-outlined-select
              value={String(scene.platform ?? "")}
              onInput={(event) => void onScene({ platform: (event.target as unknown as { value: string }).value })}
            >
              <md-select-option value=""><div slot="headline">No guide</div></md-select-option>
              {PLATFORMS.map(([value, label]) => (
                <md-select-option key={value} value={value} selected={String(scene.platform ?? "") === value}>
                  <div slot="headline">{label}</div>
                </md-select-option>
              ))}
            </md-outlined-select>
          </details>

          <details className="design-group design-fold">
            <summary className="sidebar-label">Are the words early or late?</summary>
            <p className="muted">
              If the captions light up a moment after the word is said, slide
              this to bring them earlier. Watch the preview to check.
            </p>
            <label>
              {Number(scene.captionOffset ?? 0) === 0
                ? "In step with the audio"
                : `${Number(scene.captionOffset ?? 0) > 0 ? "Later" : "Earlier"} by ${Math.abs(Number(scene.captionOffset ?? 0)).toFixed(2)}s`}
              <md-slider
                min={-1}
                max={1}
                step={0.05}
                labeled
                value={Number(scene.captionOffset ?? 0)}
                onInput={(event) =>
                  void onScene({ captionOffset: Number((event.target as unknown as { value: number }).value) })
                }
              />
            </label>
          </details>

          <details className="design-group design-fold">
            <summary className="sidebar-label">Voice volume</summary>
            <p className="muted">
              The finished video is always set to the volume the apps expect, so
              you rarely need this. Fade in and fade out soften the very start
              and end of the clip.
            </p>
            <label>
              Level {Number(scene.voiceGainDb ?? 0) >= 0 ? "+" : ""}
              {Number(scene.voiceGainDb ?? 0).toFixed(1)} dB
              <md-slider
                min={-12}
                max={12}
                step={0.5}
                labeled
                value={Number(scene.voiceGainDb ?? 0)}
                onInput={(event) =>
                  void onScene({ voiceGainDb: Number((event.target as unknown as { value: number }).value) })
                }
              />
            </label>
            <div className="fade-row">
              <label>
                Fade in {Number(scene.fadeIn ?? 0).toFixed(1)}s
                <md-slider
                  min={0}
                  max={3}
                  step={0.1}
                  labeled
                  value={Number(scene.fadeIn ?? 0)}
                  onInput={(event) => void onScene({ fadeIn: Number((event.target as unknown as { value: number }).value) })}
                />
              </label>
              <label>
                Fade out {Number(scene.fadeOut ?? 0).toFixed(1)}s
                <md-slider
                  min={0}
                  max={3}
                  step={0.1}
                  labeled
                  value={Number(scene.fadeOut ?? 0)}
                  onInput={(event) => void onScene({ fadeOut: Number((event.target as unknown as { value: number }).value) })}
                />
              </label>
            </div>
          </details>
        </>
      )}
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
