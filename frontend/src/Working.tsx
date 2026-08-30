/**
 * The "something is happening" card.
 *
 * A thin bar in a corner is not enough reassurance for a wait of several
 * minutes. This says what the machine is doing in plain words, how far
 * along it is, how long it has been, and puts a line of trivia underneath
 * that changes every few seconds — proof that the page is alive, and
 * something to read.
 */
import { useEffect, useRef, useState } from "react";
import { api } from "./api";

/** What the worker's own messages mean, for people. */
export function plainStage(message: string | null | undefined, kind?: string): string {
  const m = (message ?? "").toLowerCase();
  if (m.includes("queued") || m.includes("waiting")) return "Waiting for its turn…";
  if (m.includes("loading") && m.includes("model")) return "Warming up the listener…";
  if (m.includes("identifying speakers")) return "Working out who is talking…";
  if (m.includes("downloading")) return "Downloading the episode…";
  if (m.includes("decoding")) return "Reading the audio…";
  if (m.includes("ffprobe")) return "Checking the file…";
  if (m.includes("reducing peaks") || m.includes("waveform")) return "Drawing the sound bars…";
  if (m.includes("transcrib")) return "Writing down every word…";
  if (m.includes("render plan") || m.includes("building")) return "Getting everything ready…";
  if (m.includes("rendering")) return "Painting the video, frame by frame…";
  if (m.includes("cuts")) return "Trimming out the words you cut…";
  if (kind === "transcribe") return "Writing down every word…";
  if (kind === "render") return "Making your video…";
  return message || "Working…";
}

function clock(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

// One shared pool per page, so five cards do not make five requests.
let factPool: string[] = [];
let factPromise: Promise<void> | null = null;
function loadFacts(): Promise<void> {
  if (factPool.length > 6) return Promise.resolve();
  if (!factPromise) {
    factPromise = api
      .facts(20)
      .then((r) => {
        factPool = r.facts;
      })
      .catch(() => {
        // The card still works without a fact line.
      })
      .finally(() => {
        factPromise = null;
      });
  }
  return factPromise;
}

export function WorkingCard({
  title,
  stage,
  fraction,
  startedAt,
  compact = false,
}: {
  title: string;
  /** The worker's message, translated by plainStage. */
  stage: string;
  /** 0..1, or null when nobody knows. */
  fraction: number | null;
  /** Epoch ms, for the elapsed clock. */
  startedAt: number;
  compact?: boolean;
}) {
  const [now, setNow] = useState(Date.now());
  const [fact, setFact] = useState<string | null>(null);
  const seen = useRef(0);

  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, []);

  useEffect(() => {
    let stopped = false;
    const next = async () => {
      await loadFacts();
      if (stopped || !factPool.length) return;
      setFact(factPool[seen.current % factPool.length]);
      seen.current += 1;
    };
    void next();
    const t = window.setInterval(() => void next(), 9000);
    return () => {
      stopped = true;
      window.clearInterval(t);
    };
  }, []);

  const elapsed = (now - startedAt) / 1000;
  const percent = fraction === null ? null : Math.round(Math.max(0, Math.min(1, fraction)) * 100);
  // A rough "about a minute left", only once there is enough to go on.
  let remaining: string | null = null;
  if (fraction !== null && fraction > 0.08 && fraction < 0.98 && elapsed > 5) {
    const total = elapsed / fraction;
    const left = total - elapsed;
    remaining = left < 50 ? "under a minute left" : `about ${Math.round(left / 60)} min left`;
  }

  return (
    <div className={`working-card${compact ? " compact" : ""}`} role="status" aria-live="polite">
      <div className="working-head">
        <strong>{title}</strong>
        <span className="working-clock" title="How long this has been running">
          {clock(elapsed)}
          {remaining ? ` · ${remaining}` : ""}
        </span>
      </div>
      <div className="working-stage">{stage}</div>
      <div
        className={`working-bar${percent === null ? " indeterminate" : ""}`}
        role="progressbar"
        aria-valuenow={percent ?? undefined}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <i style={percent === null ? undefined : { width: `${Math.max(2, percent)}%` }} />
      </div>
      <div className="working-foot">
        <span className="working-percent">{percent === null ? "" : `${percent}%`}</span>
        {fact && (
          <span className="working-fact" key={fact}>
            <em>While you wait:</em> {fact}
          </span>
        )}
      </div>
    </div>
  );
}
