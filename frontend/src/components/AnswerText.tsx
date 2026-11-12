import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

const TOKEN_LABELS: Record<string, string> = {
  AADHAAR: "Aadhaar",
  PAN: "PAN",
  PHONE: "Phone",
  BANK_ACCOUNT: "Bank account",
  UPI: "UPI ID",
  EMAIL: "Email",
  NAME: "Name",
};

/** Turn citation brackets and masked-PII tokens into inline code markers rendered as chips. */
function prepare(text: string): string {
  return text
    .replace(/\[([^\]\n]*§[^\]\n]*)\]/g, (_, inner: string) =>
      inner
        .split(/\s*;\s*/)
        .map((c) => `\`cite:${c.trim()}\``)
        .join(" "),
    )
    .replace(/<([A-Z_]+)_(\d+)>/g, (_, kind: string) => `\`pii:${kind}\``);
}

export function stripMarkup(text: string): string {
  return text
    .replace(/\s*\[[^\]\n]*§[^\]\n]*\]/g, "")
    .replace(/<([A-Z_]+)_\d+>/g, (_, k: string) => `[${TOKEN_LABELS[k] ?? "hidden"}]`)
    .replace(/\*\*/g, "");
}

export function PiiChip({ kind }: { kind: string }) {
  return (
    <span className="pii-chip" title="Masked for your privacy: the AI never saw this value">
      🔒 {TOKEN_LABELS[kind] ?? "Hidden"} hidden
    </span>
  );
}

export default function AnswerText({ text, className = "answer" }: { text: string; className?: string }) {
  return (
    <div className={className}>
      <Markdown
        remarkPlugins={[remarkGfm]}
        components={{
          code({ children }) {
            const value = String(children);
            if (value.startsWith("cite:")) return <span className="cite-chip">{value.slice(5)}</span>;
            if (value.startsWith("pii:")) return <PiiChip kind={value.slice(4)} />;
            return <code>{children}</code>;
          },
          a({ href, children }) {
            return (
              <a href={href} target="_blank" rel="noopener noreferrer">
                {children}
              </a>
            );
          },
        }}
      >
        {prepare(text)}
      </Markdown>
    </div>
  );
}
