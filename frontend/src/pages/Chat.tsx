import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";
import { useNavigate, useParams } from "react-router";
import { api, LANGUAGES, type ChatMessage } from "../api";
import { firstName, useAuth } from "../auth";
import AnswerText, { stripMarkup } from "../components/AnswerText";
import CardView from "../components/Cards";
import { playAudio, useChatSession, type UiMessage } from "../components/ChatSession";
import { Button, Dots, Skeleton, Spinner, useToast } from "../components/ui";
import { useRecorder } from "../components/useRecorder";

function greeting(): string {
  const h = new Date().getHours();
  if (h < 12) return "Good morning";
  if (h < 17) return "Good afternoon";
  return "Good evening";
}

const SUGGESTIONS = [
  { topic: "PM-KISAN", q: "PM-KISAN mein saal ka kitna paisa milta hai?" },
  { topic: "Eligibility", q: "Am I eligible for Ayushman Bharat PM-JAY?" },
  { topic: "Grievance", q: "Meri PM-KISAN ki kist 6 mahine se nahi aayi, complaint karni hai" },
  { topic: "आयुष्मान भारत", q: "आयुष्मान कार्ड बनवाने के लिए कितने पैसे लगते हैं?" },
];

const STAGE_NAMES: Record<string, string> = {
  asr_ms: "Speech to text",
  guardrail_ms: "Privacy masking",
  retrieval_ms: "Searching rules",
  llm_ms: "AI reasoning",
  nmt_ms: "Translation",
  tts_ms: "Spoken reply",
  total_ms: "Total",
};

function BehindTheAnswer({ meta, masked }: { meta: ChatMessage["meta"]; masked: { entity_type: string }[] }) {
  const timings = meta.timings ?? {};
  return (
    <details className="behind">
      <summary>How this answer was made</summary>
      <div className="behind-grid">
        <div>
          <b>Privacy</b>
          <span>
            {masked.length
              ? `${masked.length} personal detail${masked.length > 1 ? "s" : ""} masked before the AI saw your message`
              : "No personal details found in your message"}
          </span>
        </div>
        <div>
          <b>Understood as</b>
          <span>{(meta.intent ?? "—").replaceAll("_", " ").toLowerCase()}</span>
        </div>
        <div>
          <b>Reply language</b>
          <span>{LANGUAGES.find((l) => l.code === meta.language)?.label ?? meta.language ?? "—"}</span>
        </div>
        <div>
          <b>Time taken</b>
          <span>
            {Object.entries(timings)
              .filter(([k]) => k in STAGE_NAMES)
              .map(([k, v]) => `${STAGE_NAMES[k]} ${(v / 1000).toFixed(1)}s`)
              .join(" · ") || "—"}
          </span>
        </div>
      </div>
    </details>
  );
}

function Message({
  message,
  previous,
  isLast,
  streaming,
  onReply,
  onEdit,
}: {
  message: UiMessage;
  previous?: UiMessage;
  isLast: boolean;
  streaming: boolean;
  onReply: (text: string) => void;
  onEdit: () => void;
}) {
  const notify = useToast();
  if (message.role === "user") {
    return (
      <div className="msg msg-user">
        <div className="bubble-user">
          {message.meta?.input_mode === "voice" && <span className="voice-badge">🎙️</span>}
          <AnswerText text={message.content} className="user-text" />
        </div>
      </div>
    );
  }
  const meta = message.meta ?? {};
  const card = meta.card;
  return (
    <div className="msg msg-assistant">
      <div className="who-label">VaaniSetu</div>
      {message.pending ? (
        <div className="thinking">
          <Dots />
          <span className="status" key={message.status}>
            {message.status}
          </span>
        </div>
      ) : (
        message.content && <AnswerText text={message.content} />
      )}
      {message.error && (
        <div className="msg-error">
          <span>{message.error}</span>
          {message.retry && (
            <Button size="sm" variant="secondary" onClick={message.retry} disabled={streaming}>
              Try again
            </Button>
          )}
        </div>
      )}
      {!message.pending && !message.error && (
        <>
          {card && (
            <CardView card={card} active={isLast && !streaming && !!meta.awaiting_confirmation} onReply={onReply} onEdit={onEdit} />
          )}
          {meta.citations && meta.citations.length > 0 && !card && (
            <div className="sources">
              <div className="sources-title">From the official rules</div>
              {meta.citations.map((c) => (
                <span key={c.id} className="cite-chip">
                  {c.label}
                </span>
              ))}
            </div>
          )}
          <div className="meta-row">
            {message.audio && (
              <button className="link-btn" onClick={() => playAudio(message.audio!)}>
                ▶ Play reply
              </button>
            )}
            <button
              className="link-btn"
              onClick={() =>
                navigator.clipboard
                  .writeText(stripMarkup(message.content))
                  .then(() => notify("Reply copied", "success"))
                  .catch(() => notify("Couldn't copy the reply", "error"))
              }
            >
              Copy
            </button>
          </div>
          {meta.intent && <BehindTheAnswer meta={meta} masked={previous?.meta?.masked ?? meta.masked ?? []} />}
        </>
      )}
    </div>
  );
}

function VoiceButton({ disabled, onAudio }: { disabled: boolean; onAudio: (wav: Blob) => void }) {
  const notify = useToast();
  const recorder = useRecorder((wav, seconds) => {
    if (seconds < 0.6) notify("Hold on a little longer — that was too short.", "error");
    else onAudio(wav);
  });
  useEffect(() => {
    if (recorder.state === "denied") notify("Microphone access was blocked. Allow it in the browser to speak.", "error");
  }, [recorder.state, notify]);
  if (recorder.state === "unsupported") return null;
  const recording = recorder.state === "recording";
  return recording ? (
    <div className="recording">
      <span className="rec-dot" style={{ transform: `scale(${1 + recorder.level * 0.8})` }} />
      <span className="rec-time">0:{String(recorder.seconds).padStart(2, "0")}</span>
      <Button size="sm" variant="ghost" onClick={recorder.cancel}>
        Cancel
      </Button>
      <Button size="sm" onClick={recorder.stop}>
        Send voice
      </Button>
    </div>
  ) : (
    <button className="mic-btn" type="button" disabled={disabled} onClick={() => void recorder.start()} title="Speak your message">
      <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
        <path fill="currentColor" d="M12 15a3 3 0 0 0 3-3V6a3 3 0 1 0-6 0v6a3 3 0 0 0 3 3Zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.92V21h2v-2.08A7 7 0 0 0 19 12h-2Z" />
      </svg>
      <span className="sr-only">Speak</span>
    </button>
  );
}

export default function ChatPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { user } = useAuth();
  const { messages, loading, loadError, streaming, open, send, sendVoice, stop, activeId, language, setLanguage, speak, setSpeak } =
    useChatSession();
  const [draft, setDraft] = useState("");
  const [voice, setVoice] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const stick = useRef(true);

  useEffect(() => {
    open(id ?? null);
  }, [id, open]);
  useEffect(() => {
    api.health().then((h) => setVoice(h.voice)).catch(() => setVoice(null));
  }, []);
  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [messages]);
  useLayoutEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [draft]);

  const onCreated = (newId: string) => navigate(`/c/${newId}`, { replace: !id });

  function ask(text: string) {
    const q = text.trim();
    if (!q || streaming) return;
    stick.current = true;
    setDraft("");
    send(q, onCreated);
    inputRef.current?.focus();
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      ask(draft);
    }
  }

  const showWelcome = !id && !activeId && messages.length === 0;
  const waitingForConfirm = messages.at(-1)?.meta?.awaiting_confirmation && !streaming;

  return (
    <div className="chat">
      <div className="chat-toolbar">
        <label className="lang-picker">
          <span className="picker-label">Reply in</span>
          <select className="input input-sm" value={language} onChange={(e) => setLanguage(e.target.value)} disabled={streaming}>
            {LANGUAGES.map((l) => (
              <option key={l.code} value={l.code}>
                {l.label}
              </option>
            ))}
          </select>
        </label>
        {voice && (
          <label className={`switch${speak ? " on" : ""}`}>
            <input type="checkbox" checked={speak} onChange={(e) => setSpeak(e.target.checked)} />
            <span className="switch-track">
              <span className="switch-thumb" />
            </span>
            <span className="switch-text">
              <b>Speak replies</b>
              <span>Read answers aloud</span>
            </span>
          </label>
        )}
      </div>
      <div
        className="chat-scroll"
        ref={scrollRef}
        onScroll={(e) => {
          const el = e.currentTarget;
          stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
        }}
      >
        <div className="chat-inner">
          {showWelcome ? (
            <div className="welcome">
              <h1>
                {greeting()}, {user ? firstName(user) : ""}
              </h1>
              <p className="lead">
                Ask about <b>PM-KISAN</b> or <b>Ayushman Bharat PM-JAY</b>, check whether you qualify, or prepare a complaint
                for <b>CPGRAMS</b>. Type or speak in English, Hindi or Hinglish.
              </p>
              <div className="suggestions">
                {SUGGESTIONS.map((s) => (
                  <button key={s.q} className="suggestion" onClick={() => ask(s.q)}>
                    <small>{s.topic}</small>
                    {s.q}
                  </button>
                ))}
              </div>
              <p className="privacy-note">
                🔒 Aadhaar, PAN, phone, bank and UPI numbers are masked before the AI sees your message, and are never saved.
              </p>
            </div>
          ) : loading ? (
            <div style={{ display: "grid", gap: 14 }}>
              <Skeleton width="55%" height={18} style={{ marginLeft: "auto" }} />
              <Skeleton width="90%" height={16} />
              <Skeleton width="75%" height={16} />
            </div>
          ) : loadError ? (
            <div className="msg-error">{loadError}</div>
          ) : (
            messages.map((m, i) => (
              <Message
                key={m.id}
                message={m}
                previous={messages[i - 1]}
                isLast={i === messages.length - 1}
                streaming={streaming}
                onReply={ask}
                onEdit={() => {
                  setDraft("Please change ");
                  inputRef.current?.focus();
                }}
              />
            ))
          )}
        </div>
      </div>
      <div className="composer-wrap">
        <form
          className="composer"
          onSubmit={(e) => {
            e.preventDefault();
            ask(draft);
          }}
        >
          <textarea
            ref={inputRef}
            rows={1}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder={waitingForConfirm ? "Reply yes, no, or tell me what to change…" : "Type your question… (Hindi, English or Hinglish)"}
            maxLength={2000}
            autoFocus
          />
          {voice && (
            <VoiceButton
              disabled={streaming}
              onAudio={(wav) => {
                stick.current = true;
                sendVoice(wav, onCreated);
              }}
            />
          )}
          {streaming ? (
            <Button type="button" variant="secondary" onClick={stop}>
              <Spinner /> Stop
            </Button>
          ) : (
            <Button type="submit" disabled={!draft.trim()}>
              Send
            </Button>
          )}
        </form>
        <p className="composer-note">
          Answers come from curated official scheme rules. Always confirm on pmkisan.gov.in, pmjay.gov.in or pgportal.gov.in.
        </p>
      </div>
    </div>
  );
}
