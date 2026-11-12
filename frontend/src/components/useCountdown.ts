import { useCallback, useEffect, useState } from "react";

/** Seconds remaining until an action may be repeated. */
export function useCountdown(initial = 0): [number, (seconds: number) => void] {
  const [remaining, setRemaining] = useState(initial);
  useEffect(() => {
    if (remaining <= 0) return;
    const id = window.setTimeout(() => setRemaining((s) => s - 1), 1000);
    return () => window.clearTimeout(id);
  }, [remaining]);
  const start = useCallback((seconds: number) => setRemaining(Math.max(0, Math.ceil(seconds))), []);
  return [remaining, start];
}
