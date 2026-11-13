import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";
import { api, ApiError, streamChat, type ChatEvent, type ChatMessage } from "../api";
import { useAuth } from "../auth";
import { playAudio } from "./audioPlayer";
import { useLibrary } from "./Library";

export interface UiMessage extends ChatMessage {
  pending?: boolean;
  status?: string;
  error?: string;
  retry?: () => void;
  audio?: { data: string; mime: string } | null;
}

interface ChatSessionState {
  activeId: string | null;
  messages: UiMessage[];
  loading: boolean;
  loadError: string | null;
  streaming: boolean;
  language: string;
  speak: boolean;
  setLanguage: (code: string) => void;
  setSpeak: (on: boolean) => void;
  open: (id: string | null) => void;
  /** `inputMode: "voice"` = a reviewed speech transcript; its reply is always spoken. */
  send: (text: string, onCreated?: (id: string) => void, inputMode?: "text" | "voice") => void;
  stop: () => void;
}

const ChatContext = createContext<ChatSessionState | null>(null);
let tempCounter = 0;
const tempId = () => `tmp-${++tempCounter}`;

function stored(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function store(key: string, value: string) {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* private mode: preference just won't persist */
  }
}

export function ChatProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const { touchConversation, refreshApplications } = useLibrary();
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<UiMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [streaming, setStreaming] = useState(false);
  const [language, setLanguageState] = useState(() => stored("vs:language") ?? user?.preferred_language ?? "auto");
  const [speak, setSpeakState] = useState(() => stored("vs:speak") === "1");
  const activeRef = useRef<string | null>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const loadSeq = useRef(0);
  const prefs = useRef({ language, speak });
  prefs.current = { language, speak };

  const setLanguage = useCallback((code: string) => {
    setLanguageState(code);
    store("vs:language", code);
  }, []);
  const setSpeak = useCallback((on: boolean) => {
    setSpeakState(on);
    store("vs:speak", on ? "1" : "0");
  }, []);

  const open = useCallback((id: string | null) => {
    if (id === activeRef.current && (id !== null || loadSeq.current > 0)) return;
    activeRef.current = id;
    setActiveId(id);
    setLoadError(null);
    const seq = ++loadSeq.current;
    if (id === null) {
      setMessages([]);
      setLoading(false);
      return;
    }
    setLoading(true);
    setMessages([]);
    api
      .messages(id)
      .then((list) => seq === loadSeq.current && setMessages(list))
      .catch((err: ApiError) => seq === loadSeq.current && setLoadError(err.message))
      .finally(() => seq === loadSeq.current && setLoading(false));
  }, []);

  const run = useCallback(
    (
      userContent: string,
      inputMode: "text" | "voice",
      call: (convId: string | null, onEvent: (e: ChatEvent) => void, signal: AbortSignal) => Promise<void>,
      retry: () => void,
      onCreated?: (id: string) => void,
    ) => {
      if (controllerRef.current) return;
      const userId = tempId();
      const placeholderId = tempId();
      let convId = activeRef.current;
      const now = new Date().toISOString();
      setMessages((list) => [
        ...list.map((m) => (m.meta?.awaiting_confirmation ? { ...m, meta: { ...m.meta, awaiting_confirmation: false } } : m)),
        { id: userId, role: "user", content: userContent, meta: { input_mode: inputMode }, created_at: now },
        {
          id: placeholderId,
          role: "assistant",
          content: "",
          meta: {},
          created_at: now,
          pending: true,
          status: "Reading your message",
        },
      ]);
      const controller = new AbortController();
      controllerRef.current = controller;
      setStreaming(true);

      const visible = () => activeRef.current === convId;
      const patch = (id: string | number, fn: (m: UiMessage) => UiMessage) => {
        if (visible()) setMessages((list) => list.map((m) => (m.id === id ? fn(m) : m)));
      };

      const onEvent = (event: ChatEvent) => {
        switch (event.type) {
          case "conversation":
            if (!convId) {
              convId = event.id;
              activeRef.current = event.id;
              setActiveId(event.id);
              onCreated?.(event.id);
            }
            touchConversation({ id: event.id, title: event.title, updated_at: new Date().toISOString() });
            break;
          case "status":
            patch(placeholderId, (m) => ({ ...m, status: event.text }));
            break;
          case "done": {
            touchConversation({ ...event.conversation, updated_at: new Date().toISOString() });
            if (event.message.meta.card && "application_id" in event.message.meta.card) void refreshApplications();
            if (!visible()) break;
            setMessages((list) =>
              list.map((m) => {
                if (m.id === userId) {
                  // Keep what the user typed (or reviewed) on screen; the server stores it redacted.
                  return event.user_message ? { ...event.user_message, content: m.content } : m;
                }
                if (m.id === placeholderId) return { ...event.message, audio: event.audio };
                return m;
              }),
            );
            // Spoken questions always get a spoken answer; typed ones follow "Speak replies".
            if (event.audio && (inputMode === "voice" || prefs.current.speak))
              playAudio(`data:${event.audio.mime};base64,${event.audio.data}`, String(event.message.id));
            break;
          }
          case "error":
            patch(placeholderId, (m) => ({ ...m, pending: false, error: event.message, retry }));
            break;
        }
      };

      call(convId, onEvent, controller.signal)
        .catch((err: Error) => {
          if (err.name === "AbortError") patch(placeholderId, (m) => ({ ...m, pending: false, error: "Stopped." }));
          else patch(placeholderId, (m) => ({ ...m, pending: false, error: err.message, retry }));
        })
        .finally(() => {
          controllerRef.current = null;
          setStreaming(false);
          patch(placeholderId, (m) => (m.pending ? { ...m, pending: false } : m));
        });
    },
    [touchConversation, refreshApplications],
  );

  const send = useCallback(
    (text: string, onCreated?: (id: string) => void, inputMode: "text" | "voice" = "text") => {
      const again = () => send(text, undefined, inputMode);
      run(
        text,
        inputMode,
        (conversation_id, onEvent, signal) =>
          streamChat(
            {
              message: text,
              conversation_id,
              language: prefs.current.language,
              speak: inputMode === "voice" || prefs.current.speak,
              input_mode: inputMode,
            },
            onEvent,
            signal,
          ),
        again,
        onCreated,
      );
    },
    [run],
  );

  const stop = useCallback(() => controllerRef.current?.abort(), []);

  const value = useMemo(
    () => ({ activeId, messages, loading, loadError, streaming, language, speak, setLanguage, setSpeak, open, send, stop }),
    [activeId, messages, loading, loadError, streaming, language, speak, setLanguage, setSpeak, open, send, stop],
  );
  return <ChatContext.Provider value={value}>{children}</ChatContext.Provider>;
}

export function useChatSession(): ChatSessionState {
  const ctx = useContext(ChatContext);
  if (!ctx) throw new Error("useChatSession must be used inside ChatProvider");
  return ctx;
}
