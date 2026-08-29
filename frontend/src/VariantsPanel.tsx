import { useEffect, useState } from "react";
import { Check, Copy, Loader2 } from "lucide-react";
import { Project, RatioPreset, api } from "./api";
import { play as playSfx } from "./sfx";

/**
 * Produce the same clip in the other shapes it needs to be in.
 *
 * A variant is a real project rather than a render setting, because the layouts
 * genuinely differ — a title that spans a vertical frame is lost in a wide one.
 * Renders run in parallel lanes, so asking for three finishes in about the time
 * one used to.
 */
export function VariantsPanel({
  project,
  onCreated,
}: {
  project: Project | null;
  onCreated: () => Promise<void>;
}) {
  const [ratios, setRatios] = useState<RatioPreset[]>([]);
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .ratios()
      .then((payload) => setRatios(payload.ratios))
      .catch(() => setRatios([]));
  }, []);

  // Offering the shape it already is would just make a duplicate.
  const options = ratios.filter((preset) => preset.ratio !== project?.aspect_ratio);

  async function create() {
    if (!project || chosen.size === 0) return;
    setBusy(true);
    setError(null);
    setDone(null);
    try {
      const result = await api.createVariants(project.id, [...chosen]);
      setDone(`${result.projects.length} queued`);
      setChosen(new Set());
      playSfx("confirm");
      await onCreated();
    } catch (cause) {
      setError((cause as Error).message);
      playSfx("error");
    } finally {
      setBusy(false);
    }
  }

  if (!project) return null;

  return (
    <div className="design-group variants-panel">
      <span className="sidebar-label">
        <Copy size={12} /> Also make it for
      </span>
      <p className="muted">
        Copies this clip into another shape and renders it. The layout is
        rescaled so nothing changes size on screen.
      </p>

      <div className="ratio-options">
        {options.map((preset) => {
          const on = chosen.has(preset.ratio);
          return (
            <button
              key={preset.ratio}
              className={`ratio-option ${on ? "selected" : ""}`}
              onClick={() => {
                playSfx("select");
                setChosen((current) => {
                  const next = new Set(current);
                  if (on) next.delete(preset.ratio);
                  else next.add(preset.ratio);
                  return next;
                });
              }}
            >
              <span className={`ratio-shape ratio-${preset.ratio.replace(":", "-")}`} />
              <span className="ratio-meta">
                <strong>{preset.label}</strong>
                <small>{preset.for}</small>
              </span>
              {on && <Check size={13} />}
            </button>
          );
        })}
      </div>

      {error && <p className="error">{error}</p>}
      {done && <p className="muted">{done} — see Exports when they finish.</p>}

      <button
        className="ghost compact"
        disabled={busy || chosen.size === 0}
        onClick={() => void create()}
      >
        {busy ? <Loader2 className="spin" size={14} /> : <Copy size={14} />}
        {chosen.size ? `Make ${chosen.size} more` : "Pick a shape"}
      </button>
    </div>
  );
}
