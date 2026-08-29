import { useMemo } from "react";
import { Scissors, Undo2 } from "lucide-react";
import { Transcript, TranscriptWord } from "./api";

/**
 * Editing the clip by editing its words.
 *
 * Click a word and it is struck out; what is left is what the clip says. A
 * tangent, a phone ringing, forty seconds of throat-clearing before the point
 * — gone, with what came after moved up to meet what came before.
 *
 * The cuts are stored on the scene as ranges in *source* time — the same
 * coordinates the transcript uses — and the renderer removes them in a pre-pass
 * before anything else runs. See backend/app/services/cuts.py, which is where
 * the mapping between source time and output time actually lives; this panel
 * only decides which ranges exist.
 *
 * Only the clip's own words are shown. The panel next to this one lists all
 * 1123 segments of a 93-minute episode, which is the right scope for finding a
 * moment and the wrong one for editing the moment you already found.
 */

export type CutRange = { start: number; end: number };

/**
 * How much of the silence after a word belongs to it.
 *
 * Cutting exactly from a word's start to its end leaves the pause that
 * followed it, so removing three consecutive words leaves three little gaps
 * where the words were — an audible stutter rather than a clean edit. Taking
 * the gap up to the next word makes consecutive cuts join into one continuous
 * range, which is what the ear expects.
 *
 * Capped, because the gap to the next word can be a ten-second pause, and
 * swallowing that is an edit nobody asked for.
 */
const TRAILING_SILENCE = 0.4;

export function cutDuration(cuts: CutRange[]): number {
  return merge(cuts).reduce((total, cut) => total + (cut.end - cut.start), 0);
}

/** Sort and join overlapping ranges, matching what the backend does on read. */
export function merge(cuts: CutRange[]): CutRange[] {
  const sorted = [...cuts]
    .map((cut) =>
      cut.end < cut.start ? { start: cut.end, end: cut.start } : cut,
    )
    .filter((cut) => cut.end - cut.start > 0.001)
    .sort((a, b) => a.start - b.start);
  const out: CutRange[] = [];
  for (const cut of sorted) {
    const last = out[out.length - 1];
    if (last && cut.start <= last.end + 0.02) {
      last.end = Math.max(last.end, cut.end);
    } else {
      out.push({ ...cut });
    }
  }
  return out;
}

/** Remove a span from a set of ranges, splitting any range it lands inside. */
function subtract(cuts: CutRange[], start: number, end: number): CutRange[] {
  const out: CutRange[] = [];
  for (const cut of merge(cuts)) {
    if (cut.end <= start || cut.start >= end) {
      out.push(cut);
      continue;
    }
    if (cut.start < start) out.push({ start: cut.start, end: start });
    if (cut.end > end) out.push({ start: end, end: cut.end });
  }
  return out;
}

function isCut(time: number, cuts: CutRange[]): boolean {
  return cuts.some((cut) => time >= cut.start && time <= cut.end);
}

export function TranscriptCuts({
  transcript,
  clipStart,
  clipEnd,
  cuts,
  onChange,
  onSeek,
}: {
  transcript: Transcript | null;
  clipStart: number;
  clipEnd: number;
  cuts: CutRange[];
  onChange: (next: CutRange[]) => void;
  onSeek?: (time: number) => void;
}) {
  // Only the words inside the clip, and only once: a segment that straddles an
  // edge contributes the part that is actually in the clip.
  const rows = useMemo(() => {
    const segments = (transcript?.segments ?? []).filter(
      (segment) => segment.end > clipStart && segment.start < clipEnd,
    );
    return segments
      .map((segment) => ({
        id: segment.id,
        speaker: segment.speaker,
        words: (segment.words ?? []).filter(
          (word) => word.end > clipStart && word.start < clipEnd,
        ),
        text: segment.text,
      }))
      .filter((row) => row.words.length > 0 || row.text);
  }, [transcript, clipStart, clipEnd]);

  const merged = merge(cuts);
  const removed = cutDuration(merged);
  const remaining = Math.max(0, clipEnd - clipStart - removed);

  /** The span a word owns: itself, plus the short pause that follows it. */
  function spanOf(words: TranscriptWord[], index: number): CutRange {
    const word = words[index];
    const next = words[index + 1];
    const end =
      next && next.start - word.end <= TRAILING_SILENCE ? next.start : word.end;
    return { start: word.start, end: Math.max(end, word.start + 0.02) };
  }

  function toggle(words: TranscriptWord[], index: number) {
    const span = spanOf(words, index);
    const word = words[index];
    if (isCut((word.start + word.end) / 2, merged)) {
      onChange(subtract(merged, span.start, span.end));
    } else {
      onChange(merge([...merged, span]));
    }
  }

  if (!transcript) {
    return (
      <p className="muted">
        Cutting needs a transcript. It appears here once transcription finishes.
      </p>
    );
  }

  if (!rows.some((row) => row.words.length)) {
    return (
      <p className="muted">
        This transcript has no word timings, so words cannot be cut
        individually. Re-transcribing the media produces them.
      </p>
    );
  }

  return (
    <div className="transcript-cuts">
      <p className="muted">
        Click a word to cut it. What is left is what the clip says.
      </p>
      <div className="cuts-summary">
        <span>
          {merged.length === 0
            ? "Nothing cut"
            : `${merged.length} cut${merged.length === 1 ? "" : "s"} · ${removed.toFixed(1)}s removed`}
        </span>
        <span className="cuts-runtime">
          Clip runs {remaining.toFixed(1)}s
        </span>
        {merged.length > 0 && (
          <button className="ghost compact" onClick={() => onChange([])}>
            <Undo2 size={13} /> Restore all
          </button>
        )}
      </div>
      {remaining < 0.5 && merged.length > 0 && (
        <p className="form-error">
          There is almost nothing left. Restore some words before exporting.
        </p>
      )}
      <div className="cuts-body">
        {rows.map((row) => (
          <p className="cuts-line" key={row.id}>
            <b>{row.speaker}</b>
            {row.words.length ? (
              row.words.map((word, index) => {
                const cut = isCut((word.start + word.end) / 2, merged);
                return (
                  <span
                    key={`${row.id}-${index}`}
                    className={cut ? "word cut" : "word"}
                    role="button"
                    tabIndex={0}
                    title={`${word.start.toFixed(2)}s — click to ${cut ? "restore" : "cut"}`}
                    onClick={(event) => {
                      // Alt-click auditions the word instead of cutting it:
                      // deciding whether a sentence survives usually means
                      // hearing it first.
                      if (event.altKey) {
                        onSeek?.(word.start);
                        return;
                      }
                      toggle(row.words, index);
                    }}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        toggle(row.words, index);
                      }
                    }}
                  >
                    {word.text}
                  </span>
                );
              })
            ) : (
              <span className="word-plain">{row.text}</span>
            )}
          </p>
        ))}
      </div>
      <p className="muted small">
        <Scissors size={12} /> Alt-click a word to hear it instead of cutting
        it. Cuts apply on export.
      </p>
    </div>
  );
}
