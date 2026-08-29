import { useEffect, useRef, useState } from "react";
import { Mic, Pause, Play, Square, Trash2 } from "lucide-react";
import { api } from "./api";
import { SfxCue } from "./SfxPanel";

/**
 * Record a voice-over in the browser and drop it on the clip.
 *
 * An aside over the intro, a correction, a call to action at the end. The
 * take is recorded with MediaRecorder, kept in the media library without the
 * episode's analysis jobs, and placed as a cue at the playhead — the same
 * mechanism as a sound effect, so it is delayed, levelled and mixed the same
 * way on export. Nothing leaves the browser until "Place".
 */
export function VoiceoverPanel({
  projectId,
  playhead,
  clipDuration,
  cues,
  onChange,
}: {
  projectId: string | null;
  /** Clip-relative seconds. */
  playhead: number;
  clipDuration: number;
  cues: SfxCue[];
  onChange: (next: SfxCue[]) => void;
}) {
  const [state, setState] = useState<"idle" | "recording" | "recorded" | "saving">("idle");
  const [seconds, setSeconds] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const blobRef = useRef<Blob | null>(null);
  const urlRef = useRef<string | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const timerRef = useRef<number | null>(null);

  useEffect(() => () => {
    recorderRef.current?.stream.getTracks().forEach((track) => track.stop());
    if (urlRef.current) URL.revokeObjectURL(urlRef.current);
    if (timerRef.current) window.clearInterval(timerRef.current);
  }, []);

  const supported = typeof window !== "undefined" && "MediaRecorder" in window;

  async function start() {
    setError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      // Opus in webm where available (Chrome, Firefox), else whatever the
      // browser offers (Safari gives mp4). FFmpeg reads both.
      const mime = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"].find(
        (candidate) => MediaRecorder.isTypeSupported(candidate),
      );
      const recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
      chunksRef.current = [];
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunksRef.current.push(event.data);
      };
      recorder.onstop = () => {
        stream.getTracks().forEach((track) => track.stop());
        const blob = new Blob(chunksRef.current, { type: recorder.mimeType || "audio/webm" });
        blobRef.current = blob;
        if (urlRef.current) URL.revokeObjectURL(urlRef.current);
        urlRef.current = URL.createObjectURL(blob);
        setState("recorded");
      };
      recorderRef.current = recorder;
      recorder.start(250);
      setSeconds(0);
      timerRef.current = window.setInterval(() => setSeconds((value) => value + 0.1), 100);
      setState("recording");
    } catch (problem) {
      setError(
        problem instanceof Error && /denied|permission/i.test(problem.message)
          ? "Microphone access was refused. Allow it in the browser and try again."
          : "Could not start recording.",
      );
    }
  }

  function stop() {
    if (timerRef.current) window.clearInterval(timerRef.current);
    recorderRef.current?.stop();
  }

  function discard() {
    audioRef.current?.pause();
    blobRef.current = null;
    if (urlRef.current) URL.revokeObjectURL(urlRef.current);
    urlRef.current = null;
    setPreviewing(false);
    setState("idle");
  }

  function togglePreview() {
    if (!urlRef.current) return;
    if (previewing) {
      audioRef.current?.pause();
      setPreviewing(false);
      return;
    }
    const audio = new Audio(urlRef.current);
    audio.onended = () => setPreviewing(false);
    audioRef.current = audio;
    setPreviewing(true);
    void audio.play().catch(() => setPreviewing(false));
  }

  async function place() {
    if (!projectId || !blobRef.current) return;
    setState("saving");
    setError(null);
    try {
      const { media } = await api.saveVoiceover(projectId, blobRef.current);
      const at = Math.round(Math.max(0, Math.min(clipDuration - 0.1, playhead)) * 100) / 100;
      onChange(
        [...cues, { soundId: "", mediaId: media.id, at, gainDb: 0, title: `Voice-over (${seconds.toFixed(1)}s)` }]
          .sort((a, b) => a.at - b.at)
          .slice(0, 16),
      );
      discard();
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : "Could not save the recording.");
      setState("recorded");
    }
  }

  if (!supported) {
    return (
      <div className="design-group">
        <span className="sidebar-label"><Mic size={13} /> Voice-over</span>
        <p className="muted">This browser cannot record audio.</p>
      </div>
    );
  }

  return (
    <div className="design-group voiceover-panel">
      <span className="sidebar-label"><Mic size={13} /> Voice-over</span>
      <p className="muted">
        Record an aside and drop it at the playhead. It mixes in on export like
        an effect; the level slider is in the effects list below.
      </p>
      <div className="mini-fields">
        {state === "idle" && (
          <button className="ghost compact" onClick={() => void start()}>
            <Mic size={13} /> Record
          </button>
        )}
        {state === "recording" && (
          <button className="ghost compact recording" onClick={stop}>
            <Square size={13} /> Stop · {seconds.toFixed(1)}s
          </button>
        )}
        {(state === "recorded" || state === "saving") && (
          <>
            <button className="ghost compact" onClick={togglePreview} disabled={state === "saving"}>
              {previewing ? <Pause size={13} /> : <Play size={13} />} {seconds.toFixed(1)}s
            </button>
            <button className="primary compact" onClick={() => void place()} disabled={state === "saving"}>
              {state === "saving" ? "Saving…" : "Place at playhead"}
            </button>
            <button className="ghost compact" title="Discard" onClick={discard} disabled={state === "saving"}>
              <Trash2 size={13} />
            </button>
          </>
        )}
      </div>
      {error && <p className="form-error">{error}</p>}
    </div>
  );
}
