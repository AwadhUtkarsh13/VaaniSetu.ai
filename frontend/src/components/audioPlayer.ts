import { useEffect, useState } from "react";

/** One shared player: starting a reply stops whatever was playing. */
let current: { audio: HTMLAudioElement; key: string; url?: string } | null = null;
const listeners = new Set<(key: string | null) => void>();

function notify() {
  const key = current?.key ?? null;
  listeners.forEach((l) => l(key));
}

export function stopAudio() {
  if (!current) return;
  current.audio.pause();
  if (current.url) URL.revokeObjectURL(current.url);
  current = null;
  notify();
}

/** Play `src` (a data: or blob: URL) as the reply identified by `key`. */
export function playAudio(src: string, key: string, ownsUrl = false) {
  stopAudio();
  const audio = new Audio(src);
  current = { audio, key, url: ownsUrl ? src : undefined };
  const finish = () => {
    if (current?.audio === audio) stopAudio();
  };
  audio.addEventListener("ended", finish);
  audio.addEventListener("error", finish);
  notify();
  void audio.play().catch(finish);
}

/** Key of the reply currently being read aloud (re-renders on change). */
export function usePlayingKey(): string | null {
  const [key, setKey] = useState<string | null>(current?.key ?? null);
  useEffect(() => {
    listeners.add(setKey);
    return () => {
      listeners.delete(setKey);
    };
  }, []);
  return key;
}
