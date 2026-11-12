import { useEffect, useState } from "react";
import { useNavigate } from "react-router";
import { api, ApiError } from "../api";
import { useAuth } from "../auth";
import CodeInput from "../components/CodeInput";
import { Button, useToast } from "../components/ui";
import { useCountdown } from "../components/useCountdown";
import AuthLayout from "./AuthLayout";

export default function VerifyEmail() {
  const { user, setUser, signOut } = useAuth();
  const navigate = useNavigate();
  const notify = useToast();
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [sending, setSending] = useState(false);
  const [consoleMode, setConsoleMode] = useState(false);
  const [wait, startWait] = useCountdown();

  useEffect(() => {
    api.health().then((h) => setConsoleMode(h.email === "console")).catch(() => {});
    api.verificationStatus().then((s) => startWait(s.retry_after)).catch(() => {});
  }, [startWait]);

  async function verify(value = code) {
    if (value.length !== 6) {
      setError("Enter all 6 digits.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const updated = await api.verifyEmail(value);
      setUser(updated);
      notify("Email confirmed. Welcome to VaaniSetu AI.", "success");
      navigate("/", { replace: true });
    } catch (err) {
      setError((err as ApiError).message);
      setCode("");
    } finally {
      setBusy(false);
    }
  }

  async function resend() {
    setSending(true);
    setError(null);
    try {
      const r = await api.resendVerification();
      startWait(r.retry_after);
      setCode("");
      notify("A new code is on its way", "success");
    } catch (err) {
      const e = err as ApiError;
      if (e.retryAfter) startWait(e.retryAfter);
      setError(e.message);
    } finally {
      setSending(false);
    }
  }

  if (!user) return null;
  return (
    <AuthLayout>
      <h1>Check your email</h1>
      <p className="lead">
        We sent a 6-digit code to <span className="email-pill">{user.email}</span>. Enter it below to confirm it's you.
      </p>
      {consoleMode && (
        <div className="banner banner-info">
          Email sending isn't set up on this server yet, so the code was printed in the server window instead.
        </div>
      )}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void verify();
        }}
      >
        <CodeInput value={code} onChange={setCode} onComplete={(v) => void verify(v)} invalid={!!error} disabled={busy} />
        {error && (
          <p className="error-text" style={{ marginTop: 10 }}>
            {error}
          </p>
        )}
        <div style={{ marginTop: 22 }}>
          <Button type="submit" size="lg" block loading={busy} disabled={code.length !== 6}>
            {busy ? "Checking" : "Confirm email"}
          </Button>
        </div>
      </form>
      <p className="resend-row">
        Didn't get it? Check your spam folder, or{" "}
        {wait > 0 ? (
          <span>send a new code in {wait}s</span>
        ) : (
          <button type="button" className="link-btn" onClick={resend} disabled={sending}>
            {sending ? "sending..." : "send a new code"}
          </button>
        )}
        .
      </p>
      <p className="auth-switch">
        Wrong email?{" "}
        <button
          type="button"
          className="link-btn"
          onClick={async () => {
            await signOut();
            navigate("/signup", { replace: true });
          }}
        >
          Use a different account
        </button>
      </p>
    </AuthLayout>
  );
}
