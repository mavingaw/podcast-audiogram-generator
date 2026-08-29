/**
 * Interface sound cues.
 *
 * The effects come from a licensed pack served by the backend, so the URLs are
 * fetched once at sign-in rather than bundled. Playback is best-effort: a
 * browser that blocks autoplay, a library that was never imported, or a user
 * who muted cues all end up in the same place — silence, and no thrown error.
 */

export type SfxRole =
  | "select"
  | "confirm"
  | "cursor"
  | "cancel"
  | "error"
  | "open"
  | "close"
  | "swipe";

const STORAGE_KEY = "pas.sfx.enabled";
const VOLUME = 0.35;

let roles: Partial<Record<SfxRole, string>> = {};
const cache = new Map<SfxRole, HTMLAudioElement>();

function readEnabled(): boolean {
  try {
    return window.localStorage.getItem(STORAGE_KEY) !== "off";
  } catch {
    return true;
  }
}

let enabled = readEnabled();

export function sfxEnabled(): boolean {
  return enabled;
}

export function setSfxEnabled(next: boolean): void {
  enabled = next;
  try {
    window.localStorage.setItem(STORAGE_KEY, next ? "on" : "off");
  } catch {
    // A blocked storage API only costs us the preference, not the feature.
  }
}

export function loadSfx(next: Record<string, string>): void {
  roles = next as Partial<Record<SfxRole, string>>;
  cache.clear();
}

export function play(role: SfxRole): void {
  if (!enabled) return;
  const url = roles[role];
  if (!url) return;

  let audio = cache.get(role);
  if (!audio) {
    audio = new Audio(url);
    audio.preload = "auto";
    audio.volume = VOLUME;
    cache.set(role, audio);
  }
  audio.currentTime = 0;
  // Cues are decoration; a rejected play() must never surface to the user.
  void audio.play().catch(() => undefined);
}
