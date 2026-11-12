import { useEffect, useState } from "react";

const CAPTIONS = ["Loading the official scheme rules", "Preparing your assistant", "Almost ready"];
const WORD = "VaaniSetu";

export default function Splash({ leaving = false, caption }: { leaving?: boolean; caption?: string }) {
  const [step, setStep] = useState(0);
  useEffect(() => {
    if (caption) return;
    const id = window.setInterval(() => setStep((s) => Math.min(s + 1, CAPTIONS.length - 1)), 2600);
    return () => window.clearInterval(id);
  }, [caption]);
  const text = caption ?? CAPTIONS[step];
  return (
    <div className={`splash${leaving ? " leaving" : ""}`} aria-busy="true" aria-live="polite">
      <div className="splash-inner">
        <div className="wordmark" aria-label="VaaniSetu AI">
          {WORD.split("").map((ch, i) => (
            <span key={i} style={{ animationDelay: `${i * 0.06}s` }}>
              {ch}
            </span>
          ))}
          <span className="wordmark-ai" style={{ animationDelay: `${WORD.length * 0.06}s` }}>
            AI
          </span>
        </div>
        <div className="progress-track">
          <div className="progress-bar-indeterminate" />
        </div>
        <div className="splash-caption" key={text}>
          {text}
        </div>
      </div>
    </div>
  );
}
