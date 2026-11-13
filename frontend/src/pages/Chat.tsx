import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";
import { useNavigate, useParams } from "react-router";
import { api, LANGUAGES, type ChatMessage } from "../api";
import { firstName, useAuth } from "../auth";
import AnswerText, { CitationChip, stripMarkup } from "../components/AnswerText";
import { playAudio, stopAudio, usePlayingKey } from "../components/audioPlayer";
import CardView from "../components/Cards";
import { useChatSession, type UiMessage } from "../components/ChatSession";
import { Button, Dots, Skeleton, Spinner, useToast } from "../components/ui";
import { SILENCE_RMS, useRecorder } from "../components/useRecorder";

function silentMicMessage(device: string): string {
  const which = device ? `Your microphone (${device})` : "Your microphone";
  return `${which} sent no sound. Check that it isn't muted, or pick another mic from the mic icon in the browser's address bar, then try again.`;
}

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
              ? `${masked.length} personal detail${masked.length > 1 ? "s" : ""} hidden before the AI saw your message`
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

function MicIcon({ size = 20 }: { size?: number }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} aria-hidden="true">
      <path
        fill="currentColor"
        d="M12 15a3 3 0 0 0 3-3V6a3 3 0 1 0-6 0v6a3 3 0 0 0 3 3Zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.92V21h2v-2.08A7 7 0 0 0 19 12h-2Z"
      />
    </svg>
  );
}

function SpeakerIcon() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
      <path
        fill="currentColor"
        d="M4 9v6h4l5 4V5L8 9H4Zm12.5 3a4.5 4.5 0 0 0-2.5-4v8a4.5 4.5 0 0 0 2.5-4Zm-2.5-8.7v2.06A7 7 0 0 1 14 18.64v2.06A9 9 0 0 0 14 3.3Z"
      />
    </svg>
  );
}

/** "Listen" under every answer: reads it aloud (from the stored, masked text); click again to stop. */
function ListenButton({ message, conversationId }: { message: UiMessage; conversationId: string | null }) {
  const notify = useToast();
  const key = String(message.id);
  const playing = usePlayingKey() === key;
  const [loading, setLoading] = useState(false);
  const stored = typeof message.id === "number";
  if (!message.audio && !(stored && conversationId)) return null;

  async function listen() {
    if (playing) return stopAudio();
    if (message.audio) return playAudio(`data:${message.audio.mime};base64,${message.audio.data}`, key);
    setLoading(true);
    try {
      const blob = await api.messageSpeech(conversationId!, message.id);
      playAudio(URL.createObjectURL(blob), key, true);
    } catch (err) {
      notify((err as Error).message, "error");
    } finally {
      setLoading(false);
    }
  }

  return (
    <button
      className={`listen-btn${playing ? " playing" : ""}`}
      onClick={() => void listen()}
      disabled={loading}
      title="Read this answer aloud"
    >
      {loading ? <Spinner /> : <SpeakerIcon />}
      {loading ? "Preparing audio" : playing ? "Stop" : "Listen"}
    </button>
  );
}

function Message({
  message,
  previous,
  isLast,
  streaming,
  voiceOn,
  conversationId,
  onReply,
  onEdit,
}: {
  message: UiMessage;
  previous?: UiMessage;
  isLast: boolean;
  streaming: boolean;
  voiceOn: boolean;
  conversationId: string | null;
  onReply: (text: string) => void;
  onEdit: () => void;
}) {
  const notify = useToast();
  if (message.role === "user") {
    return (
      <div className="msg msg-user">
        <div className="bubble-user">
          {message.meta?.input_mode === "voice" && (
            <span className="voice-badge" title="Asked by voice">
              <MicIcon size={14} />
            </span>
          )}
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
              <div className="sources-title">Sources: official scheme rules</div>
              {meta.citations.map((c) => (
                <CitationChip key={c.id} label={c.label} />
              ))}
            </div>
          )}
          <div className="meta-row">
            {voiceOn && <ListenButton message={message} conversationId={conversationId} />}
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

export default function ChatPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { user } = useAuth();
  const notify = useToast();
  const { messages, loading, loadError, streaming, open, send, stop, activeId, language, setLanguage, speak, setSpeak } =
    useChatSession();
  const [draft, setDraft] = useState("");
  const [voice, setVoice] = useState<string | null>(null);
  const [fromVoice, setFromVoice] = useState(false); // the draft is a speech transcript under review
  const [transcribing, setTranscribing] = useState(false);
  const [unsure, setUnsure] = useState(false); // the speech model was not confident about the transcript
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const stick = useRef(true);
  const languageRef = useRef(language);
  languageRef.current = language;

  const recorder = useRecorder(({ wav, seconds, peak, device }) => {
    if (seconds < 0.6) {
      notify("That was too short. Press the mic and speak your question.", "error");
      return;
    }
    if (peak < SILENCE_RMS) {
      // Never send silence: a speech model given no speech may return made-up text.
      notify(silentMicMessage(device), "error");
      return;
    }
    setTranscribing(true);
    api
      .transcribe(wav, languageRef.current)
      .then(({ text, uncertain }) => {
        setDraft(text);
        setFromVoice(true);
        setUnsure(uncertain);
        window.setTimeout(() => inputRef.current?.focus(), 0);
      })
      .catch((err: Error) => notify(err.message, "error"))
      .finally(() => setTranscribing(false));
  });
  useEffect(() => {
    if (recorder.state === "denied") notify("Microphone access was blocked. Allow it in the browser to speak.", "error");
  }, [recorder.state, notify]);

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
    send(q, onCreated, fromVoice ? "voice" : "text");
    setFromVoice(false);
    inputRef.current?.focus();
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      ask(draft);
    }
  }

  function startRecording() {
    stopAudio(); // a reply being read aloud must not end up in the recording
    void recorder.start();
  }

  function recordAgain() {
    setDraft("");
    setFromVoice(false);
    startRecording();
  }

  const showWelcome = !id && !activeId && messages.length === 0;
  const waitingForConfirm = messages.at(-1)?.meta?.awaiting_confirmation && !streaming;
  const recording = recorder.state === "recording";
  const micAvailable = !!voice && recorder.state !== "unsupported";
  const reviewing = fromVoice && !!draft && !recording && !transcribing;

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
              <span>Read typed answers aloud too</span>
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
                Your privacy: Aadhaar, PAN, phone, bank and UPI numbers are hidden before the AI sees your message, and are
                never saved.
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
                voiceOn={!!voice}
                conversationId={activeId}
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
        {reviewing && (
          <div className="voice-review">
            <div className="voice-review-text">
              <b>Check your question</b>
              {unsure ? (
                <span className="voice-review-warn">
                  The recording wasn't clear, so this may not be what you said. Fix it or record again closer to the mic.
                </span>
              ) : (
                <span>This is what we heard. Fix anything that's wrong, then press Send.</span>
              )}
            </div>
            <div className="voice-review-actions">
              <Button type="button" size="sm" variant="secondary" onClick={recordAgain} disabled={streaming}>
                <MicIcon size={16} /> Record again
              </Button>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                onClick={() => {
                  setDraft("");
                  setFromVoice(false);
                }}
              >
                Discard
              </Button>
            </div>
          </div>
        )}
        <form
          className={`composer${reviewing ? " reviewing" : ""}`}
          onSubmit={(e) => {
            e.preventDefault();
            ask(draft);
          }}
        >
          {recording ? (
            <div className="recording">
              <span className="rec-dot" style={{ transform: `scale(${1 + recorder.level * 0.8})` }} />
              {recorder.seconds >= 2 && !recorder.heard ? (
                <span className="rec-label rec-warn">
                  No sound from your microphone{recorder.device ? ` (${recorder.device})` : ""}. Is it muted?
                </span>
              ) : (
                <span className="rec-label">Listening… speak your question</span>
              )}
              <span className="rec-time">0:{String(recorder.seconds).padStart(2, "0")}</span>
              <Button type="button" size="sm" variant="ghost" onClick={recorder.cancel}>
                Cancel
              </Button>
              <Button type="button" size="sm" onClick={recorder.stop}>
                Done
              </Button>
            </div>
          ) : transcribing ? (
            <div className="recording">
              <Spinner />
              <span className="rec-label">Converting your speech to text…</span>
            </div>
          ) : (
            <>
              <textarea
                ref={inputRef}
                rows={1}
                value={draft}
                onChange={(e) => {
                  setDraft(e.target.value);
                  if (!e.target.value.trim()) setFromVoice(false);
                }}
                onKeyDown={onKeyDown}
                placeholder={
                  waitingForConfirm ? "Reply yes, no, or tell me what to change…" : "Type or speak your question… (Hindi, English or Hinglish)"
                }
                maxLength={2000}
                autoFocus
              />
              {micAvailable && (
                <button
                  className="mic-btn"
                  type="button"
                  disabled={streaming}
                  onClick={() => (reviewing ? recordAgain() : startRecording())}
                  title="Speak your question"
                >
                  <MicIcon />
                  <span className="sr-only">Speak</span>
                </button>
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
            </>
          )}
        </form>
        <p className="composer-note">
          Answers come from curated official scheme rules. Always confirm on pmkisan.gov.in, pmjay.gov.in or pgportal.gov.in.
        </p>
      </div>
    </div>
  );
}
