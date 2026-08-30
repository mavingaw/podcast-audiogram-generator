import { useCallback, useEffect, useState } from "react";
import { History, RotateCcw } from "lucide-react";
import { api } from "./api";

/**
 * How this clip was before.
 *
 * Applying a template rewrites every layer, cutting words rewrites the audio,
 * and a batch rewrites the lot — each in one click. The history is what makes
 * those comfortable to press.
 *
 * Entries are coarse on purpose. The server keeps the state from before a
 * change and only when the newest entry is already a minute or two old, so
 * dragging a colour slider leaves one line rather than one per frame. A
 * history nobody can read is the same as no history.
 */

type Revision = { id: string; label: string; created_at: string };

function when(iso: string): string {
  const then = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : `${iso}Z`);
  const seconds = Math.max(0, (Date.now() - then.getTime()) / 1000);
  if (seconds < 90) return "just now";
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 86400) {
    const hours = Math.round(seconds / 3600);
    return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  }
  return then.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export function HistoryPanel({
  projectId,
  version,
  onRestored,
}: {
  projectId: string | null;
  /** Bumped by the parent after any save, so the list follows the edits. */
  version: number;
  onRestored: () => Promise<void> | void;
}) {
  const [revisions, setRevisions] = useState<Revision[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!projectId) {
      setRevisions([]);
      return;
    }
    try {
      setRevisions((await api.revisions(projectId)).revisions);
    } catch {
      // A history that cannot be listed is not worth an error banner over the
      // editor; the clip itself is fine.
      setRevisions([]);
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load, version]);

  if (!projectId) return null;

  return (
    <div className="design-group history-panel">
      <span className="sidebar-label">
        <History size={13} /> History
      </span>
      {revisions.length === 0 ? (
        <p className="muted">
          Nothing to undo yet. Every change you make is saved here, so you can
          always go back to an earlier version.
        </p>
      ) : (
        <ul className="history-list">
          {revisions.map((revision) => (
            <li key={revision.id}>
              <span>
                <b>{revision.label}</b>
                <small>{when(revision.created_at)}</small>
              </span>
              <button
                className="ghost compact"
                disabled={busy !== null}
                onClick={async () => {
                  // Restoring is itself recorded, so this is not the one-way
                  // door it looks like — but say so, because it does not look
                  // that way from here.
                  if (
                    !window.confirm(
                      `Go back to how this clip was before "${revision.label}"? The current state is kept, so you can come back.`,
                    )
                  )
                    return;
                  setBusy(revision.id);
                  setError(null);
                  try {
                    await api.restoreRevision(projectId, revision.id);
                    await onRestored();
                    await load();
                  } catch (problem) {
                    setError(
                      problem instanceof Error
                        ? problem.message
                        : "That version could not be restored.",
                    );
                  } finally {
                    setBusy(null);
                  }
                }}
              >
                <RotateCcw size={13} />
                {busy === revision.id ? "Restoring…" : "Restore"}
              </button>
            </li>
          ))}
        </ul>
      )}
      {error && <p className="form-error">{error}</p>}
    </div>
  );
}
