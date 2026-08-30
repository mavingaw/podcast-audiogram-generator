/**
 * The "?" in the corner.
 *
 * Every screen explains itself in a few plain sentences: what it is for,
 * the two or three things you can do here, and where to go next. Written
 * for somebody who has never edited a video — no jargon, no assumptions.
 * The same overlay carries the "Large text" switch, which is the single
 * most useful accessibility control for a shared tool.
 */
import { useEffect, useState } from "react";

type Guide = {
  title: string;
  what: string;
  steps: string[];
  tip?: string;
};

export const GUIDES: Record<string, Guide> = {
  home: {
    title: "Start here",
    what: "Kinder turns a bit of your podcast into a short video you can post on Instagram, TikTok or YouTube.",
    steps: [
      "Press the big blue “New creation” button.",
      "Answer the questions one screen at a time — where it will be posted, which episode, which part.",
      "Pick a look you like. You can change anything later.",
    ],
    tip: "Nothing is ever posted for you. You download the video and post it yourself.",
  },
  quick: {
    title: "Making a clip",
    what: "These five steps make one video. Each step asks one question.",
    steps: [
      "Where will it live? — this sets the shape of the video.",
      "Which episode? — upload a file or pick one you already added.",
      "Which part? — click a line of the words to jump there, then drag the yellow handles to trim.",
      "Which look? — pick a style. Then press “Open in Studio” to see it.",
    ],
    tip: "Stuck on “Analyzing”? That is the computer listening to the episode and writing down the words. A one-hour episode takes a few minutes.",
  },
  studio: {
    title: "The Studio",
    what: "This is your video. What you see in the middle is what you will get.",
    steps: [
      "Click anything on the picture to select it. Drag it to move it. Drag the little circles on its corners to make it bigger or smaller.",
      "Right-click anything for more options — hide, duplicate, delete.",
      "The panel on the right changes colours, fonts and the words on screen.",
      "Press “Preview” to watch it, and “Export” when you are happy. Your video appears under Exports.",
    ],
    tip: "Keys: Space plays and pauses · arrows nudge the selected item (hold Shift for bigger steps) · Delete removes it · Ctrl+D copies it. Made a mess? History on the right puts things back.",
  },
  projects: {
    title: "Your projects",
    what: "Every clip you have started or finished. Nothing here is deleted unless you delete it.",
    steps: [
      "Click a project to open it in the Studio.",
      "Right-click (or press ⋯) to rename or delete it.",
    ],
  },
  templates: {
    title: "Looks",
    what: "A look is a ready-made style — colours, font, caption style — so you do not have to design anything.",
    steps: [
      "Pick a look when you make a clip. Every part of it can still be changed afterwards.",
      "Made something you like in the Studio? Save it here and use it on the next episode.",
    ],
  },
  feeds: {
    title: "Watching your podcast feed",
    what: "Paste your podcast's RSS address and Kinder will fetch new episodes on its own, write down the words, and suggest clips.",
    steps: [
      "Paste the address and press “Watch”.",
      "Suggested clips arrive in your Inbox. Nothing is posted anywhere.",
    ],
    tip: "The RSS address is on your podcast host's website — usually under “Share” or “Distribution”.",
  },
  inbox: {
    title: "Inbox",
    what: "Clips that were cut for you automatically from a watched feed.",
    steps: [
      "Approve the ones you want to keep, or throw the rest away.",
      "Approved clips become projects you can open and finish.",
    ],
  },
  settings: {
    title: "Settings",
    what: "Everything about you and your show, in one place.",
    steps: [
      "Your show: the cover picture on every clip, and the intro and outro videos.",
      "Posting accounts: connect YouTube and the rest once; finished clips then get a Post button.",
      "Admin (if that's you): transcription model, computer settings, accounts, and platform app keys.",
    ],
  },
  exports: {
    title: "Finished videos",
    what: "Every video you have made, ready to download and post.",
    steps: [
      "Press “Video” to download the video file.",
      "“Captions” downloads the words as a subtitle file, if the app you are posting to wants one.",
    ],
  },
};

const LARGE_TEXT_KEY = "kinder.largeText";

export function readLargeText(): boolean {
  try {
    return localStorage.getItem(LARGE_TEXT_KEY) === "1";
  } catch {
    return false;
  }
}

export function applyLargeText(on: boolean) {
  document.documentElement.classList.toggle("large-text", on);
  try {
    localStorage.setItem(LARGE_TEXT_KEY, on ? "1" : "0");
  } catch {
    // Private browsing: the switch still works for this visit.
  }
}

export function HelpButton({ view, onStart }: { view: string; onStart?: () => void }) {
  const [open, setOpen] = useState(false);
  const [large, setLarge] = useState(readLargeText);
  const guide = GUIDES[view] ?? GUIDES.home;

  useEffect(() => {
    if (!open) return;
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [open]);

  return (
    <>
      <button
        className="icon-button help-button"
        title="Help — what does this screen do?"
        aria-label="Help"
        aria-haspopup="dialog"
        onClick={() => setOpen(true)}
      >
        ?
      </button>
      {open && (
        <div className="help-overlay" role="dialog" aria-modal="true" aria-label={guide.title} onClick={() => setOpen(false)}>
          <div className="help-card" onClick={(e) => e.stopPropagation()}>
            <div className="help-head">
              <span className="kicker">Help</span>
              <h2>{guide.title}</h2>
            </div>
            <p className="help-what">{guide.what}</p>
            <ol className="help-steps">
              {guide.steps.map((step) => (
                <li key={step}>{step}</li>
              ))}
            </ol>
            {guide.tip && <p className="help-tip">{guide.tip}</p>}
            <div className="help-foot">
              <label className="help-switch">
                <input
                  type="checkbox"
                  checked={large}
                  onChange={(e) => {
                    setLarge(e.target.checked);
                    applyLargeText(e.target.checked);
                  }}
                />
                Large text
              </label>
              <div className="help-actions">
                {onStart && (
                  <button
                    className="primary"
                    onClick={() => {
                      setOpen(false);
                      onStart();
                    }}
                  >
                    Show me how
                  </button>
                )}
                <button onClick={() => setOpen(false)}>Got it</button>
              </div>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
