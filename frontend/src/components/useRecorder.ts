import { useCallback, useEffect, useRef, useState } from "react";

const TARGET_RATE = 16000; // what speech-to-text services expect
const MAX_SECONDS = 60;
/** Loudest-buffer RMS below this means the mic delivered silence (muted, wrong device). */
export const SILENCE_RMS = 0.002;

export type RecorderState = "idle" | "recording" | "denied" | "unsupported";

export interface Recording {
  wav: Blob;
  seconds: number;
  /** RMS of the loudest audio buffer (0-1): ~0 means the microphone sent no sound. */
  peak: number;
  /** The microphone the browser used, e.g. "Microphone Array (Realtek)". */
  device: string;
}

function downsample(input: Float32Array, from: number, to: number): Float32Array {
  if (from <= to) return input; // never upsample: the WAV is then written at the native rate
  const ratio = from / to;
  const out = new Float32Array(Math.floor(input.length / ratio));
  for (let i = 0; i < out.length; i++) {
    const start = Math.floor(i * ratio);
    const end = Math.min(Math.floor((i + 1) * ratio), input.length);
    let sum = 0;
    for (let j = start; j < end; j++) sum += input[j]!;
    out[i] = sum / Math.max(1, end - start);
  }
  return out;
}

function encodeWav(samples: Float32Array, rate: number): Blob {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const write = (offset: number, text: string) => [...text].forEach((c, i) => view.setUint8(offset + i, c.charCodeAt(0)));
  write(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  write(8, "WAVE");
  write(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, rate, true);
  view.setUint32(28, rate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  write(36, "data");
  view.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]!));
    view.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Blob([buffer], { type: "audio/wav" });
}

/** Records the microphone as 16 kHz mono WAV. `level` (0-1) drives the live meter; `heard`
 *  turns true once the mic delivers real sound, so the UI can warn about a silent microphone. */
export function useRecorder(onFinished: (recording: Recording) => void) {
  const [state, setState] = useState<RecorderState>(() =>
    // mediaDevices is missing on insecure (plain-HTTP, non-localhost) origins.
    typeof navigator !== "undefined" && typeof navigator.mediaDevices?.getUserMedia === "function" ? "idle" : "unsupported",
  );
  const [level, setLevel] = useState(0);
  const [seconds, setSeconds] = useState(0);
  const [heard, setHeard] = useState(false);
  const [device, setDevice] = useState("");
  const ctxRef = useRef<AudioContext | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Float32Array[]>([]);
  const peakRef = useRef(0);
  const deviceRef = useRef("");
  const startedRef = useRef(0);
  const timerRef = useRef<number | null>(null);
  const finishedRef = useRef(onFinished);
  finishedRef.current = onFinished;

  const cleanup = useCallback(() => {
    if (timerRef.current) window.clearInterval(timerRef.current);
    timerRef.current = null;
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    void ctxRef.current?.close().catch(() => {});
    ctxRef.current = null;
    setLevel(0);
  }, []);

  const stop = useCallback(
    (discard = false) => {
      const ctx = ctxRef.current;
      if (!ctx) return;
      const rate = ctx.sampleRate;
      const elapsed = (performance.now() - startedRef.current) / 1000;
      cleanup();
      setState("idle");
      setSeconds(0);
      const total = chunksRef.current.reduce((n, c) => n + c.length, 0);
      const merged = new Float32Array(total);
      let offset = 0;
      for (const c of chunksRef.current) {
        merged.set(c, offset);
        offset += c.length;
      }
      chunksRef.current = [];
      if (!discard && total > 0) {
        const outRate = Math.min(rate, TARGET_RATE);
        finishedRef.current({
          wav: encodeWav(downsample(merged, rate, TARGET_RATE), outRate),
          seconds: elapsed,
          peak: peakRef.current,
          device: deviceRef.current,
        });
      }
    },
    [cleanup],
  );

  const start = useCallback(async () => {
    if (ctxRef.current) return;
    let stream: MediaStream;
    try {
      // Browser noise suppression is tuned for calls and can cut out speech; speech-to-text
      // models cope with background noise better on the raw signal. Auto gain lifts quiet mics.
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: false, autoGainControl: true },
      });
    } catch {
      setState("denied");
      return;
    }
    const ctx = new AudioContext();
    if (ctx.state === "suspended") await ctx.resume().catch(() => {});
    const source = ctx.createMediaStreamSource(stream);
    const processor = ctx.createScriptProcessor(4096, 1, 1);
    processor.onaudioprocess = (e) => {
      const data = new Float32Array(e.inputBuffer.getChannelData(0));
      chunksRef.current.push(data);
      let sum = 0;
      for (let i = 0; i < data.length; i++) sum += data[i]! * data[i]!;
      const rms = Math.sqrt(sum / data.length);
      if (rms > peakRef.current) peakRef.current = rms;
      if (rms >= SILENCE_RMS) setHeard(true);
      setLevel(Math.min(1, rms * 6));
    };
    source.connect(processor);
    processor.connect(ctx.destination);
    ctxRef.current = ctx;
    streamRef.current = stream;
    deviceRef.current = stream.getAudioTracks()[0]?.label ?? "";
    setDevice(deviceRef.current);
    chunksRef.current = [];
    peakRef.current = 0;
    setHeard(false);
    startedRef.current = performance.now();
    setSeconds(0);
    setState("recording");
    timerRef.current = window.setInterval(() => {
      const s = Math.floor((performance.now() - startedRef.current) / 1000);
      setSeconds(s);
      if (s >= MAX_SECONDS) stop();
    }, 250);
  }, [stop]);

  useEffect(() => cleanup, [cleanup]);

  return { state, level, seconds, heard, device, start, stop: () => stop(false), cancel: () => stop(true) };
}
