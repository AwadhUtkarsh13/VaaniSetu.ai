export type Gender = "female" | "male" | "non_binary" | "other" | "prefer_not_to_say";

export interface User {
  id: string;
  email: string;
  name: string;
  phone: string;
  dob: string | null;
  gender: Gender | null;
  preferred_language: string;
  has_photo: boolean;
  photo_version: number;
  email_verified: boolean;
  created_at: string;
}

export interface ConversationItem {
  id: string;
  title: string;
  updated_at: string;
}

export interface Citation {
  id: string;
  label: string;
}

export interface CardItem {
  label: string;
  value: string;
}

export type Verdict = "ELIGIBLE" | "LIKELY_ELIGIBLE" | "NEEDS_VERIFICATION" | "NOT_ELIGIBLE";

export type Card =
  | { kind: "progress"; flow: string; items: CardItem[]; remaining: number }
  | { kind: "confirm"; flow: string; items: CardItem[] }
  | {
      kind: "eligibility";
      application_id: string;
      scheme: string;
      verdict: Verdict;
      reasons: string[];
      next_steps: string[];
      items: CardItem[];
      citations: Citation[];
    }
  | {
      kind: "grievance";
      application_id: string;
      department: string | null;
      summary: string | null;
      items: CardItem[];
      portal_url: string;
      citations: Citation[];
    };

export interface MessageMeta {
  intent?: string | null;
  language?: string;
  input_mode?: "text" | "voice";
  card?: Card | null;
  citations?: Citation[];
  awaiting_confirmation?: boolean;
  masked?: { entity_type: string; token: string }[];
  timings?: Record<string, number>;
  llm_calls?: number;
  tokens?: number;
  degraded?: string[];
}

export interface ChatMessage {
  id: number | string;
  role: "user" | "assistant";
  content: string;
  meta: MessageMeta;
  created_at: string;
}

export interface ApplicationItem {
  id: string;
  action_type: "SCHEME_ELIGIBILITY_CHECK" | "CPGRAMS_GRIEVANCE";
  title: string;
  verdict: Verdict | null;
  conversation_id: string | null;
  created_at: string;
}

export interface ApplicationDetail extends ApplicationItem {
  payload: Record<string, unknown> & {
    validated_slots: Record<string, unknown>;
    citations: string[];
    redacted_transcript_summary: string;
    eligibility_verdict?: { verdict: Verdict; reasons: string[]; next_steps: string[] } | null;
  };
}

export interface Health {
  ready: boolean;
  error: string | null;
  email: "smtp" | "console";
  voice: "openai" | "bhashini" | null;
}

export type ChatEvent =
  | { type: "conversation"; id: string; title: string }
  | { type: "status"; text: string }
  | {
      type: "done";
      conversation: { id: string; title: string };
      user_message: ChatMessage | null;
      transcript: string | null;
      message: ChatMessage;
      audio: { data: string; mime: string } | null;
    }
  | { type: "error"; code?: string; message: string };

export class ApiError extends Error {
  status: number;
  code: string;
  fields: Record<string, string>;
  retryAfter?: number;

  constructor(status: number, code: string, message: string, fields: Record<string, string> = {}) {
    super(message);
    this.status = status;
    this.code = code;
    this.fields = fields;
  }
}

export const UNAUTHORIZED_EVENT = "vaanisetu:unauthorized";
export const UNVERIFIED_EVENT = "vaanisetu:unverified";

function toApiError(status: number, payload: unknown): ApiError {
  const detail = (payload as { detail?: unknown } | null)?.detail;
  if (detail && typeof detail === "object") {
    const d = detail as { code?: string; message?: string; fields?: Record<string, string>; retry_after?: number };
    const err = new ApiError(status, d.code ?? "error", d.message ?? "Something went wrong.", d.fields ?? {});
    err.retryAfter = d.retry_after;
    return err;
  }
  if (status === 0) return new ApiError(0, "network", "Can't reach the server. Check that VaaniSetu AI is running.");
  return new ApiError(status, "error", "Something went wrong. Please try again.");
}

function signalUnauthorized(err: ApiError) {
  if (err.status === 401 && err.code === "not_signed_in") window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
  if (err.status === 403 && err.code === "email_not_verified") window.dispatchEvent(new Event(UNVERIFIED_EVENT));
}

async function request<T>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const { json, headers, ...rest } = init;
  let response: Response;
  try {
    response = await fetch(path, {
      credentials: "same-origin",
      ...rest,
      headers: json !== undefined ? { "Content-Type": "application/json", ...headers } : headers,
      body: json !== undefined ? JSON.stringify(json) : rest.body,
    });
  } catch {
    throw toApiError(0, null);
  }
  if (response.status === 204) return undefined as T;
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const err = toApiError(response.status, payload);
    signalUnauthorized(err);
    throw err;
  }
  return payload as T;
}

function upload<T>(path: string, file: File): Promise<T> {
  const form = new FormData();
  form.append("file", file);
  return request<T>(path, { method: "POST", body: form });
}

export const api = {
  health: () => request<Health>("/api/health"),

  checkEmail: (email: string) => request<{ registered: boolean }>("/api/auth/check-email", { method: "POST", json: { email } }),
  login: (email: string, password: string) =>
    request<User>("/api/auth/login", { method: "POST", json: { email, password } }),
  signup: (body: { name: string; email: string; phone: string; password: string }) =>
    request<User>("/api/auth/signup", { method: "POST", json: body }),
  logout: () => request<void>("/api/auth/logout", { method: "POST" }),
  verifyEmail: (code: string) => request<User>("/api/auth/verify-email", { method: "POST", json: { code } }),
  resendVerification: () => request<{ retry_after: number }>("/api/auth/resend-verification", { method: "POST" }),
  verificationStatus: () => request<{ retry_after: number }>("/api/auth/verification-status"),
  forgotPassword: (email: string) =>
    request<{ retry_after: number }>("/api/auth/forgot-password", { method: "POST", json: { email } }),
  resetPassword: (email: string, code: string, new_password: string) =>
    request<User>("/api/auth/reset-password", { method: "POST", json: { email, code, new_password } }),

  me: () => request<User>("/api/me"),
  updateProfile: (body: Partial<Pick<User, "name" | "phone" | "dob" | "gender" | "preferred_language">>) =>
    request<User>("/api/me", { method: "PATCH", json: body }),
  uploadPhoto: (file: File) => upload<User>("/api/me/photo", file),
  deletePhoto: () => request<User>("/api/me/photo", { method: "DELETE" }),
  changePassword: (current_password: string, new_password: string) =>
    request<void>("/api/me/password", { method: "POST", json: { current_password, new_password } }),

  listConversations: () => request<ConversationItem[]>("/api/conversations"),
  messages: (id: string) => request<ChatMessage[]>(`/api/conversations/${id}/messages`),
  renameConversation: (id: string, title: string) =>
    request<ConversationItem>(`/api/conversations/${id}`, { method: "PATCH", json: { title } }),
  deleteConversation: (id: string) => request<void>(`/api/conversations/${id}`, { method: "DELETE" }),

  listApplications: () => request<ApplicationItem[]>("/api/applications"),
  application: (id: string) => request<ApplicationDetail>(`/api/applications/${id}`),
  deleteApplication: (id: string) => request<void>(`/api/applications/${id}`, { method: "DELETE" }),

  /** Speech to text only: the citizen reviews (and can edit) the question before sending it. */
  transcribe: (audio: Blob, language: string) => {
    const form = new FormData();
    form.append("audio", audio, "voice.wav");
    form.append("language", language);
    return request<{ text: string }>("/api/chat/transcribe", { method: "POST", body: form });
  },

  /** The reply read aloud (MP3/WAV), generated on demand from the stored, masked text. */
  messageSpeech: async (conversationId: string, messageId: number | string): Promise<Blob> => {
    let response: Response;
    try {
      response = await fetch(`/api/conversations/${conversationId}/messages/${messageId}/speech`, {
        method: "POST",
        credentials: "same-origin",
      });
    } catch {
      throw toApiError(0, null);
    }
    if (!response.ok) {
      const err = toApiError(response.status, await response.json().catch(() => null));
      signalUnauthorized(err);
      throw err;
    }
    return response.blob();
  },
};

async function readStream(response: Response, onEvent: (event: ChatEvent) => void): Promise<void> {
  if (!response.ok || !response.body) {
    const err = toApiError(response.status, await response.json().catch(() => null));
    signalUnauthorized(err);
    throw err;
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let newline = buffer.indexOf("\n");
    while (newline >= 0) {
      const line = buffer.slice(0, newline).trim();
      buffer = buffer.slice(newline + 1);
      if (line) onEvent(JSON.parse(line) as ChatEvent);
      newline = buffer.indexOf("\n");
    }
  }
  if (buffer.trim()) onEvent(JSON.parse(buffer) as ChatEvent);
}

export async function streamChat(
  body: {
    message: string;
    conversation_id: string | null;
    language: string;
    speak: boolean;
    input_mode: "text" | "voice";
  },
  onEvent: (event: ChatEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  let response: Response;
  try {
    response = await fetch("/api/chat", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal,
    });
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw toApiError(0, null);
  }
  await readStream(response, onEvent);
}

export function parseDate(value: string): Date {
  return new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(value) ? value : `${value}Z`);
}

export function formatDate(value: string): string {
  return new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short", year: "numeric" }).format(parseDate(value));
}

export function formatDateTime(value: string): string {
  return new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" }).format(
    parseDate(value),
  );
}

export function photoUrl(user: User): string | null {
  return user.has_photo ? `/api/me/photo?v=${user.photo_version}` : null;
}

export const LANGUAGES: { code: string; label: string }[] = [
  { code: "auto", label: "Same as I write" },
  { code: "en", label: "English" },
  { code: "hinglish", label: "Hinglish" },
  { code: "hi", label: "हिन्दी" },
  { code: "mr", label: "मराठी" },
  { code: "bn", label: "বাংলা" },
  { code: "ta", label: "தமிழ்" },
  { code: "te", label: "తెలుగు" },
  { code: "gu", label: "ગુજરાતી" },
  { code: "kn", label: "ಕನ್ನಡ" },
  { code: "ml", label: "മലയാളം" },
  { code: "pa", label: "ਪੰਜਾਬੀ" },
  { code: "or", label: "ଓଡ଼ିଆ" },
  { code: "ur", label: "اردو" },
];
