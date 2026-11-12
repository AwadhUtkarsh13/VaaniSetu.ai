import { Link } from "react-router";
import type { Card, CardItem, Citation, Verdict } from "../api";
import { PiiChip } from "./AnswerText";
import { Button, useToast } from "./ui";

export const VERDICTS: Record<Verdict, { label: string; tone: string; icon: string; note: string }> = {
  ELIGIBLE: { label: "Eligible", tone: "ok", icon: "✓", note: "You meet the official rules." },
  LIKELY_ELIGIBLE: {
    label: "Likely eligible",
    tone: "info",
    icon: "✓",
    note: "You match the rules; your name still needs to be found in the official list.",
  },
  NEEDS_VERIFICATION: {
    label: "Needs verification",
    tone: "warn",
    icon: "?",
    note: "This can only be confirmed from the official records.",
  },
  NOT_ELIGIBLE: { label: "Not eligible", tone: "bad", icon: "✕", note: "An official exclusion rule applies to you." },
};

function Value({ value }: { value: string }) {
  const parts = value.split(/(<[A-Z_]+_\d+>)/g).filter(Boolean);
  return (
    <>
      {parts.map((p, i) => {
        const m = /^<([A-Z_]+)_\d+>$/.exec(p);
        return m ? <PiiChip key={i} kind={m[1]!} /> : <span key={i}>{p}</span>;
      })}
    </>
  );
}

function Items({ items }: { items: CardItem[] }) {
  return (
    <dl className="kv">
      {items.map((it) => (
        <div key={it.label}>
          <dt>{it.label}</dt>
          <dd>
            <Value value={it.value} />
          </dd>
        </div>
      ))}
    </dl>
  );
}

function Rules({ citations }: { citations: Citation[] }) {
  if (!citations.length) return null;
  return (
    <div className="rules">
      <div className="rules-title">Official rules applied</div>
      <div className="rules-list">
        {citations.map((c) => (
          <span key={c.id} className="cite-chip" title={c.id}>
            {c.label}
          </span>
        ))}
      </div>
    </div>
  );
}

const FLOW_NAMES: Record<string, string> = {
  ELIGIBILITY_CHECK: "Eligibility check",
  GRIEVANCE_FILE: "Grievance",
};

export default function CardView({
  card,
  active,
  onReply,
  onEdit,
}: {
  card: Card;
  active: boolean;
  onReply: (text: string) => void;
  onEdit: () => void;
}) {
  const notify = useToast();
  const copy = (text: string, what: string) =>
    navigator.clipboard
      .writeText(text)
      .then(() => notify(`${what} copied`, "success"))
      .catch(() => notify(`Couldn't copy the ${what.toLowerCase()}`, "error"));

  if (card.kind === "progress") {
    return (
      <div className="card-inline progress-card">
        <div className="card-inline-head">
          <span className="tag tag-accent">{FLOW_NAMES[card.flow] ?? "In progress"}</span>
          <span className="muted">
            {card.items.length} detail{card.items.length === 1 ? "" : "s"} noted
            {card.remaining > 0 ? ` · ${card.remaining} more to go` : ""}
          </span>
        </div>
        <Items items={card.items} />
      </div>
    );
  }

  if (card.kind === "confirm") {
    return (
      <div className="card-inline confirm-card">
        <div className="card-inline-head">
          <span className="tag tag-saffron">Please check</span>
          <span className="muted">Nothing is final until you confirm</span>
        </div>
        <Items items={card.items} />
        {active && (
          <div className="card-actions">
            <Button onClick={() => onReply("Yes, everything is correct. Please go ahead.")}>Yes, confirm</Button>
            <Button variant="secondary" onClick={onEdit}>
              Change something
            </Button>
            <Button variant="ghost" onClick={() => onReply("No, cancel this.")}>
              Cancel
            </Button>
          </div>
        )}
      </div>
    );
  }

  if (card.kind === "eligibility") {
    const v = VERDICTS[card.verdict];
    return (
      <div className={`card-inline verdict-card tone-${v.tone}`}>
        <div className="verdict-head">
          <span className="verdict-icon" aria-hidden="true">
            {v.icon}
          </span>
          <div>
            <div className="verdict-scheme">{card.scheme}</div>
            <div className="verdict-label">{v.label}</div>
            <div className="verdict-note">{v.note}</div>
          </div>
        </div>
        <details className="card-details">
          <summary>Details you gave</summary>
          <Items items={card.items} />
        </details>
        <Rules citations={card.citations} />
        <div className="card-actions">
          <Link className="btn btn-secondary btn-sm" to={`/applications/${card.application_id}`}>
            View saved result
          </Link>
          <Button
            variant="ghost"
            size="sm"
            onClick={() =>
              copy(
                [`${card.scheme}: ${v.label}`, "", ...card.reasons.map((r) => `- ${r}`), "", "Next steps:", ...card.next_steps.map((s) => `- ${s}`)].join("\n"),
                "Summary",
              )
            }
          >
            Copy summary
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="card-inline grievance-card">
      <div className="card-inline-head">
        <span className="tag tag-ok">Grievance ready</span>
        <span className="muted">To: {card.department}</span>
      </div>
      {card.summary && (
        <p className="grievance-summary">
          <Value value={card.summary} />
        </p>
      )}
      <Items items={card.items} />
      <Rules citations={card.citations} />
      <div className="card-actions">
        <a className="btn btn-primary btn-sm" href={card.portal_url} target="_blank" rel="noopener noreferrer">
          Open CPGRAMS portal
        </a>
        <Button variant="secondary" size="sm" onClick={() => copy(card.summary ?? "", "Complaint text")}>
          Copy complaint text
        </Button>
        <Link className="btn btn-ghost btn-sm" to={`/applications/${card.application_id}`}>
          View saved grievance
        </Link>
      </div>
    </div>
  );
}
