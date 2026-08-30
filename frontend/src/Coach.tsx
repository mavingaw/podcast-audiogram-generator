/**
 * "Show me": a walkthrough that points at the real button.
 *
 * Describing a step is not the same as showing it. The coach dims the
 * screen, cuts a window around the thing to press, and says one sentence
 * about it. It never presses anything itself: the person does the click,
 * and the coach moves on when the next screen appears — which is how it
 * knows the click worked.
 */
import { useEffect, useState } from "react";

export type CoachStep = {
  /** CSS selector for the thing to spotlight. */
  target: string;
  /** One sentence, in plain words. */
  text: string;
  /** When this selector appears, the step is done. Omit on the last step. */
  doneWhen?: string;
};

export const MAKE_A_CLIP: CoachStep[] = [
  {
    target: ".new-project",
    text: "Press this big blue button to start a new clip.",
    doneWhen: ".destination-grid",
  },
  {
    target: ".destination-grid",
    text: "Where will you post it? Pick one — it sets the shape of the video. Then press Continue.",
    doneWhen: ".source-list, .upload-drop",
  },
  {
    target: ".source-list, .upload-drop",
    text: "Click an episode you have already added, or drop an audio file here. Then press Continue.",
    doneWhen: ".clip-fields",
  },
  {
    target: ".waveform-editor",
    text: "This is the whole episode. Click a line of the words below to jump to it, and drag the yellow handles to trim your clip. Press Continue when it sounds right.",
    doneWhen: ".template-grid",
  },
  {
    target: ".template-grid",
    text: "Pick a look you like. You can change every part of it later. Then press Review creation.",
    doneWhen: "[data-coach='open-studio']",
  },
  {
    target: "[data-coach='open-studio']",
    text: "Press this to see your clip.",
    doneWhen: ".design-canvas",
  },
  {
    target: ".design-canvas",
    text: "This is your video. Click anything on it to move or resize it. When you are happy, press Export at the top right and your video appears under Exports.",
  },
];

function visible(selector: string): HTMLElement | null {
  for (const el of Array.from(document.querySelectorAll<HTMLElement>(selector))) {
    const r = el.getBoundingClientRect();
    if (r.width > 0 && r.height > 0) return el;
  }
  return null;
}

export function Coach({
  steps,
  onDone,
}: {
  steps: CoachStep[];
  onDone: () => void;
}) {
  const [index, setIndex] = useState(0);
  const [box, setBox] = useState<DOMRect | null>(null);
  const step = steps[index];

  // Follow the target as the page changes, and advance when the next
  // screen arrives. Polling is deliberate: the targets are created and
  // destroyed by React on its own schedule, and a MutationObserver over the
  // whole app would fire far more often than four times a second.
  useEffect(() => {
    if (!step) return;
    let stopped = false;
    const tick = () => {
      if (stopped) return;
      if (step.doneWhen && visible(step.doneWhen)) {
        setIndex((i) => i + 1);
        return;
      }
      const el = visible(step.target);
      if (el) {
        const r = el.getBoundingClientRect();
        // Bring it into view once; a target below the fold is no help.
        if (r.top < 0 || r.bottom > window.innerHeight) {
          el.scrollIntoView({ block: "center", behavior: "smooth" });
        }
        setBox(r);
      } else {
        setBox(null);
      }
    };
    tick();
    const timer = window.setInterval(tick, 250);
    window.addEventListener("resize", tick);
    window.addEventListener("scroll", tick, true);
    return () => {
      stopped = true;
      window.clearInterval(timer);
      window.removeEventListener("resize", tick);
      window.removeEventListener("scroll", tick, true);
    };
  }, [step, index]);

  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") onDone();
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [onDone]);

  // Ran off the end: finish on the next tick, not during render.
  useEffect(() => {
    if (!step) onDone();
  }, [step, onDone]);
  if (!step) return null;

  const pad = 8;
  const last = index === steps.length - 1;
  // The bubble goes below the target, or above it when there is no room.
  const below = box ? box.bottom + pad + 16 : 0;
  const bubbleTop = box && below + 140 < window.innerHeight ? below : box ? Math.max(12, box.top - pad - 156) : 80;
  const bubbleLeft = box ? Math.min(Math.max(12, box.left), window.innerWidth - 372) : 80;

  return (
    <div className="coach" aria-live="polite">
      {box ? (
        <div
          className="coach-spot"
          style={{
            left: box.left - pad,
            top: box.top - pad,
            width: box.width + pad * 2,
            height: box.height + pad * 2,
          }}
        />
      ) : (
        <div className="coach-dim" />
      )}
      <div className="coach-bubble" role="dialog" style={{ top: bubbleTop, left: bubbleLeft }}>
        <span className="coach-count">
          Step {index + 1} of {steps.length}
        </span>
        <p>{box ? step.text : "Looking for the next thing to press…"}</p>
        <div className="coach-actions">
          <button className="text-button" onClick={onDone}>
            {last ? "Done" : "Stop showing me"}
          </button>
          {!last && step.doneWhen && (
            <button className="text-button" onClick={() => setIndex((i) => i + 1)}>
              Skip this step
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
