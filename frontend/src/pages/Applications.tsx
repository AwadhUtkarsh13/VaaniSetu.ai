import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { api, ApiError, formatDateTime, type ApplicationDetail } from "../api";
import { PiiChip } from "../components/AnswerText";
import { VERDICTS } from "../components/Cards";
import { useLibrary } from "../components/Library";
import { Button, ConfirmDialog, Skeleton, Spinner, useToast } from "../components/ui";

const LABELS: Record<string, string> = {
  scheme_name: "Scheme", state: "State / UT", occupation: "Occupation", land_holding_hectares: "Land holding (ha)",
  annual_income_inr: "Annual income (₹)", is_income_tax_payer: "Income-tax payer", has_aadhaar_linked_bank: "Aadhaar-linked bank",
  age_years: "Age", ministry_or_department: "Ministry / Department", district: "District",
  scheme_reference: "Scheme / reference no.", incident_summary: "What happened", grievance_category: "Category", urgency: "Urgency",
};  // prettier-ignore

const SCHEMES: Record<string, string> = { PM_KISAN: "PM-KISAN", PM_JAY: "Ayushman Bharat PM-JAY" };

function show(value: unknown) {
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "string" && value in SCHEMES) return SCHEMES[value];
  if (typeof value === "number") return value.toLocaleString("en-IN");
  const text = String(value);
  if (/^[A-Z_]+$|^[a-z_]+$/.test(text)) return text.replaceAll("_", " ");
  return text.split(/(<[A-Z_]+_\d+>)/g).map((p, i) => {
    const m = /^<([A-Z_]+)_\d+>$/.exec(p);
    return m ? <PiiChip key={i} kind={m[1]!} /> : <span key={i}>{p}</span>;
  });
}

function download(app: ApplicationDetail) {
  const blob = new Blob([JSON.stringify(app.payload, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `vaanisetu-${app.action_type.toLowerCase()}-${app.id.slice(0, 8)}.json`;
  a.click();
  URL.revokeObjectURL(url);
}

function Detail({ id }: { id: string }) {
  const navigate = useNavigate();
  const notify = useToast();
  const { removeApplication } = useLibrary();
  const [app, setApp] = useState<ApplicationDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    setApp(null);
    api.application(id).then(setApp).catch((e: ApiError) => setError(e.message));
  }, [id]);

  if (error) return <div className="msg-error">{error}</div>;
  if (!app) return <Spinner large />;
  const verdict = app.payload.eligibility_verdict;
  const v = verdict ? VERDICTS[verdict.verdict] : null;

  return (
    <div className="page-enter">
      <Link to="/applications" className="back-link">
        ← All applications
      </Link>
      <div className="card" style={{ marginTop: 14 }}>
        <div className="card-head">
          <div>
            <h2>{app.title}</h2>
            <p className="muted">Saved {formatDateTime(app.created_at)}</p>
          </div>
          {v && <span className={`verdict-pill tone-${v.tone}`}>{v.label}</span>}
          {!v && <span className="verdict-pill tone-ok">Ready to file</span>}
        </div>

        {verdict && (
          <>
            <h3>Why</h3>
            <ul className="plain-list">{verdict.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
            {verdict.next_steps.length > 0 && (
              <>
                <h3 style={{ marginTop: 18 }}>What to do next</h3>
                <ul className="plain-list">{verdict.next_steps.map((s) => <li key={s}>{s}</li>)}</ul>
              </>
            )}
          </>
        )}
        {!verdict && (
          <p className="lead" style={{ fontSize: 15 }}>
            File this on <a href="https://pgportal.gov.in" target="_blank" rel="noopener noreferrer">pgportal.gov.in</a>{" "}
            using the details below. You'll get a registration number to track it; authorities should reply within 21 days.
          </p>
        )}

        <h3 style={{ marginTop: 22 }}>Details</h3>
        <dl className="kv">
          {Object.entries(app.payload.validated_slots).map(([k, val]) => (
            <div key={k}>
              <dt>{LABELS[k] ?? k}</dt>
              <dd>{show(val)}</dd>
            </div>
          ))}
        </dl>
        <h3 style={{ marginTop: 22 }}>Conversation summary</h3>
        <p className="muted">{show(app.payload.redacted_transcript_summary)}</p>

        <details className="card-details" style={{ marginTop: 22 }}>
          <summary>Technical details (portal-ready JSON, personal data masked)</summary>
          <pre className="json">{JSON.stringify(app.payload, null, 2)}</pre>
        </details>

        <div className="form-actions">
          <Button variant="ghost" onClick={() => setConfirming(true)}>
            Delete
          </Button>
          {app.conversation_id && (
            <Button variant="secondary" onClick={() => navigate(`/c/${app.conversation_id}`)}>
              Open conversation
            </Button>
          )}
          <Button
            variant="secondary"
            onClick={() =>
              navigator.clipboard
                .writeText(JSON.stringify(app.payload, null, 2))
                .then(() => notify("JSON copied", "success"))
                .catch(() => notify("Couldn't copy", "error"))
            }
          >
            Copy JSON
          </Button>
          <Button onClick={() => download(app)}>Download JSON</Button>
        </div>
      </div>
      {confirming && (
        <ConfirmDialog
          title="Delete this application?"
          message="The saved result will be removed permanently. The conversation stays."
          confirmLabel="Delete"
          busy={deleting}
          onCancel={() => setConfirming(false)}
          onConfirm={async () => {
            setDeleting(true);
            try {
              await api.deleteApplication(app.id);
              removeApplication(app.id);
              notify("Application deleted");
              navigate("/applications");
            } catch (e) {
              notify((e as Error).message, "error");
            } finally {
              setDeleting(false);
              setConfirming(false);
            }
          }}
        />
      )}
    </div>
  );
}

export default function ApplicationsPage() {
  const { id } = useParams();
  const { applications, refreshApplications } = useLibrary();
  useEffect(() => {
    void refreshApplications();
  }, [refreshApplications]);

  return (
    <div className="page">
      <div className="page-inner">
        {id ? (
          <Detail id={id} />
        ) : (
          <div className="page-enter">
            <div className="page-head">
              <div>
                <h1>My applications</h1>
                <p className="lead">Eligibility results and grievances you confirmed in chat, ready to use on the official portals.</p>
              </div>
            </div>
            {applications === null ? (
              <div style={{ display: "grid", gap: 12 }}>
                {[0, 1].map((i) => <Skeleton key={i} height={74} style={{ borderRadius: 14 }} />)}
              </div>
            ) : applications.length === 0 ? (
              <div className="card">
                <h2>Nothing here yet</h2>
                <p className="muted" style={{ marginTop: 6 }}>
                  Ask "Am I eligible for PM-KISAN?" or "I want to file a complaint" in a chat. When you confirm the details, the
                  result is saved here.
                </p>
                <div style={{ marginTop: 18 }}>
                  <Link className="btn btn-primary" to="/">
                    Start a chat
                  </Link>
                </div>
              </div>
            ) : (
              <div className="app-list">
                {applications.map((a) => {
                  const v = a.verdict ? VERDICTS[a.verdict] : null;
                  return (
                    <Link key={a.id} to={`/applications/${a.id}`} className="app-row">
                      <span className={`app-kind ${a.action_type === "CPGRAMS_GRIEVANCE" ? "kind-grievance" : "kind-eligibility"}`}>
                        {a.action_type === "CPGRAMS_GRIEVANCE" ? "Grievance" : "Eligibility"}
                      </span>
                      <span className="app-title">
                        <b>{a.title}</b>
                        <span>{formatDateTime(a.created_at)}</span>
                      </span>
                      {v ? <span className={`verdict-pill tone-${v.tone}`}>{v.label}</span> : <span className="verdict-pill tone-ok">Ready to file</span>}
                    </Link>
                  );
                })}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
