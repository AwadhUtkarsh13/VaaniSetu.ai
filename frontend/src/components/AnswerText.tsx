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

/**
 * Citations arrive as "PM-KISAN §2.1" or "PM-KISAN §2.1 Amount of benefit" ("§" is the legal
 * section sign). Show them in plain words: "PM-KISAN, Section 2.1 · Amount of benefit".
 */
export function readableCitation(label: string): string {
  const m = /^(.*?)\s*§\s*([\d.]+)\s*(.*)$/.exec(label.trim());
  if (!m) return label;
  const [, scheme, section, title] = m;
  return `${scheme}, Section ${section}${title ? ` · ${title}` : ""}`;
}

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

function DocIcon() {
  return (
    <svg viewBox="0 0 24 24" width="12" height="12" aria-hidden="true">
      <path fill="currentColor" d="M6 2h8l6 6v14H6V2Zm7 1.5V9h5.5L13 3.5ZM8 13h8v1.6H8V13Zm0 3.4h8V18H8v-1.6Z" />
    </svg>
  );
}

function LockIcon() {
  return (
    <svg viewBox="0 0 24 24" width="11" height="11" aria-hidden="true">
      <path fill="currentColor" d="M12 2a5 5 0 0 0-5 5v3H5v12h14V10h-2V7a5 5 0 0 0-5-5Zm-3 8V7a3 3 0 1 1 6 0v3H9Z" />
    </svg>
  );
}

/** A source chip: which official rule an answer comes from. */
export function CitationChip({ label }: { label: string }) {
  return (
    <span className="cite-chip" title="The official rule this comes from">
      <DocIcon />
      {readableCitation(label)}
    </span>
  );
}

export function PiiChip({ kind }: { kind: string }) {
  return (
    <span className="pii-chip" title="Hidden for your privacy: the AI never saw this value">
      <LockIcon />
      {TOKEN_LABELS[kind] ?? "Detail"} hidden
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
            if (value.startsWith("cite:")) return <CitationChip label={value.slice(5)} />;
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
