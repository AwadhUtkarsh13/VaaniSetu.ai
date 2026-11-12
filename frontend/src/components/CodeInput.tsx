import { useEffect, useRef, type ClipboardEvent, type KeyboardEvent } from "react";

const LENGTH = 6;

export default function CodeInput({
  value,
  onChange,
  onComplete,
  invalid = false,
  disabled = false,
  autoFocus = true,
}: {
  value: string;
  onChange: (value: string) => void;
  onComplete?: (value: string) => void;
  invalid?: boolean;
  disabled?: boolean;
  autoFocus?: boolean;
}) {
  const refs = useRef<(HTMLInputElement | null)[]>([]);
  const digits = Array.from({ length: LENGTH }, (_, i) => value[i] ?? "");

  useEffect(() => {
    if (autoFocus) refs.current[0]?.focus();
  }, [autoFocus]);

  function update(next: string) {
    const clean = next.replace(/\D/g, "").slice(0, LENGTH);
    onChange(clean);
    if (clean.length === LENGTH) onComplete?.(clean);
    return clean;
  }

  function onType(index: number, raw: string) {
    const typed = raw.replace(/\D/g, "");
    if (!typed) return;
    const chars = digits.slice();
    for (let i = 0; i < typed.length && index + i < LENGTH; i++) chars[index + i] = typed[i]!;
    const clean = update(chars.join(""));
    refs.current[Math.min(index + typed.length, LENGTH - 1, clean.length)]?.focus();
  }

  function onKeyDown(index: number, e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Backspace") {
      e.preventDefault();
      const chars = digits.slice();
      if (chars[index]) chars[index] = "";
      else if (index > 0) {
        chars[index - 1] = "";
        refs.current[index - 1]?.focus();
      }
      onChange(chars.join("").replace(/\D/g, ""));
    } else if (e.key === "ArrowLeft" && index > 0) refs.current[index - 1]?.focus();
    else if (e.key === "ArrowRight" && index < LENGTH - 1) refs.current[index + 1]?.focus();
  }

  function onPaste(e: ClipboardEvent<HTMLInputElement>) {
    e.preventDefault();
    const clean = update(e.clipboardData.getData("text"));
    refs.current[Math.min(clean.length, LENGTH - 1)]?.focus();
  }

  return (
    <div className={`code-inputs${invalid ? " invalid" : ""}`} role="group" aria-label="6-digit code">
      {digits.map((d, i) => (
        <input
          key={i}
          ref={(el) => {
            refs.current[i] = el;
          }}
          value={d}
          inputMode="numeric"
          autoComplete={i === 0 ? "one-time-code" : "off"}
          aria-label={`Digit ${i + 1}`}
          maxLength={LENGTH}
          disabled={disabled}
          onChange={(e) => onType(i, e.target.value)}
          onKeyDown={(e) => onKeyDown(i, e)}
          onPaste={onPaste}
          onFocus={(e) => e.target.select()}
        />
      ))}
    </div>
  );
}
