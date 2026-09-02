import { useMemo, useRef, useState } from "react";
import { Pencil, Scissors, Undo2 } from "lucide-react";
import { editedWords, Transcript, TranscriptWord } from "./api";

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
 * "Fix a word" is the other edit people reach for here: the computer heard
 * "Afia" and the captions will say so unless somebody can retype it. In that
 * mode a click opens the word for typing instead of cutting it.
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

type Editing = { segment: number; index: number; draft: string };

export function TranscriptCuts({
  transcript,
  clipStart,
  clipEnd,
  cuts,
  onChange,
  onSeek,
  onEditWord,
}: {
  transcript: Transcript | null;
  clipStart: number;
  clipEnd: number;
  cuts: CutRange[];
  onChange: (next: CutRange[]) => void;
  onSeek?: (time: number) => void;
  /** Retype one word; `index` is its position in the segment's own words. */
  onEditWord?: (segmentId: number, index: number, text: string) => void;
}) {
  const [fixing, setFixing] = useState(false);
  const [editing, setEditing] = useState<Editing | null>(null);
  // Enter commits and blurs; the blur must not commit a second time.
  const editingRef = useRef<Editing | null>(null);
  editingRef.current = editing;

  // Only the words inside the clip, and only once: a segment that straddles an
  // edge contributes the part that is actually in the clip. Each word keeps
  // its index in the segment, which is what a correction is addressed to.
  const rows = useMemo(() => {
    const segments = (transcript?.segments ?? []).filter(
      (segment) => segment.end > clipStart && segment.start < clipEnd,
    );
    return segments
      .map((segment) => ({
        id: segment.id,
        speaker: segment.speaker,
        words: editedWords(segment)
          .map((word, index) => ({ word, index }))
          .filter(({ word }) => word.end > clipStart && word.start < clipEnd),
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

  function commit(original: string) {
    const current = editingRef.current;
    if (!current) return;
    editingRef.current = null;
    setEditing(null);
    const text = current.draft.trim();
    if (text && text !== original.trim()) {
      onEditWord?.(current.segment, current.index, text);
    }
  }

  if (!transcript) {
    return (
      <p className="muted">
        The words of the clip will appear here once the computer has finished
        listening to the episode. Then you can click any word to cut it out.
      </p>
    );
  }

  if (!rows.some((row) => row.words.length)) {
    return (
      <p className="muted">
        These words were written down without timing for each one, so single
        words cannot be cut. Transcribing the episode again fixes that.
      </p>
    );
  }

  return (
    <div className={`transcript-cuts ${fixing ? "fixing" : ""}`}>
      <p className="muted">
        {fixing
          ? "Click a word to retype it — a name the computer misheard, a wrong spelling. Press Enter to keep it."
          : "Click a word to remove it from the clip — an “um”, a false start, a name. Click it again to put it back. The audio is trimmed to match."}
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
        {onEditWord && (
          <button
            className={`ghost compact fix-words ${fixing ? "on" : ""}`}
            onClick={() => {
              setEditing(null);
              setFixing((value) => !value);
            }}
            title={fixing ? "Back to cutting words" : "Retype a word the computer got wrong"}
          >
            <Pencil size={13} /> {fixing ? "Done fixing" : "Fix a word"}
          </button>
        )}
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
        {rows.map((row) => {
          const words = row.words.map((entry) => entry.word);
          return (
            <p className="cuts-line" key={row.id}>
              <b>{row.speaker}</b>
              {row.words.length ? (
                row.words.map(({ word, index }, position) => {
                  const cut = isCut((word.start + word.end) / 2, merged);
                  if (editing && editing.segment === row.id && editing.index === index) {
                    return (
                      <span key={`${row.id}-${index}`} className="word editing">
                        <input
                          autoFocus
                          aria-label="Retype this word"
                          value={editing.draft}
                          size={Math.max(3, editing.draft.length + 1)}
                          onChange={(event) =>
                            setEditing({ ...editing, draft: event.target.value })
                          }
                          onKeyDown={(event) => {
                            if (event.key === "Enter") {
                              event.preventDefault();
                              commit(word.text);
                            } else if (event.key === "Escape") {
                              editingRef.current = null;
                              setEditing(null);
                            }
                          }}
                          onBlur={() => commit(word.text)}
                        />
                      </span>
                    );
                  }
                  return (
                    <span
                      key={`${row.id}-${index}`}
                      className={cut ? "word cut" : "word"}
                      role="button"
                      tabIndex={0}
                      title={
                        fixing
                          ? "Click to retype this word"
                          : `${word.start.toFixed(2)}s — click to ${cut ? "restore" : "cut"}`
                      }
                      onClick={(event) => {
                        // Alt-click auditions the word instead of cutting it:
                        // deciding whether a sentence survives usually means
                        // hearing it first.
                        if (event.altKey) {
                          onSeek?.(word.start);
                          return;
                        }
                        if (fixing && onEditWord) {
                          setEditing({ segment: row.id, index, draft: word.text.trim() });
                          return;
                        }
                        toggle(words, position);
                      }}
                      onKeyDown={(event) => {
                        if (event.key === "Enter" || event.key === " ") {
                          event.preventDefault();
                          if (fixing && onEditWord) {
                            setEditing({ segment: row.id, index, draft: word.text.trim() });
                          } else {
                            toggle(words, position);
                          }
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
          );
        })}
      </div>
      <p className="muted small">
        <Scissors size={12} /> Hold Alt and click a word to hear it instead of
        cutting it. Cuts are applied when you export.
      </p>
    </div>
  );
}
