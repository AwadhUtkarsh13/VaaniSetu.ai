import { useState, type FormEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router";
import { api, ApiError } from "../api";
import { useAuth } from "../auth";
import CodeInput from "../components/CodeInput";
import { Button, Field, PasswordInput, useToast } from "../components/ui";
import { useCountdown } from "../components/useCountdown";
import AuthLayout, { EMAIL_RE } from "./AuthLayout";
import { passwordProblem } from "./SignUp";

export default function ForgotPassword() {
  const { setUser } = useAuth();
  const navigate = useNavigate();
  const notify = useToast();
  const location = useLocation();
  const [step, setStep] = useState<"email" | "reset">("email");
  const [email, setEmail] = useState((location.state as { email?: string } | null)?.email ?? "");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [wait, startWait] = useCountdown();

  async function sendCode(e?: FormEvent) {
    e?.preventDefault();
    if (!EMAIL_RE.test(email.trim())) {
      setErrors({ email: "Enter a valid email address." });
      return;
    }
    setBusy(true);
    setErrors({});
    try {
      const r = await api.forgotPassword(email.trim());
      startWait(r.retry_after);
      setStep("reset");
      setCode("");
      notify("We emailed you a code", "success");
    } catch (err) {
      const error = err as ApiError;
      if (error.retryAfter) {
        startWait(error.retryAfter);
        setStep("reset");
      }
      if (error.code === "not_registered") {
        setErrors({ email: "There's no account with this email." });
      } else setErrors({ [step === "email" ? "email" : "form"]: error.message });
    } finally {
      setBusy(false);
    }
  }

  async function reset(e: FormEvent) {
    e.preventDefault();
    const next: Record<string, string> = {};
    if (code.length !== 6) next.code = "Enter the 6-digit code from the email.";
    const problem = passwordProblem(password);
    if (problem) next.password = problem;
    if (confirm !== password) next.confirm = "The passwords don't match.";
    setErrors(next);
    if (Object.keys(next).length) return;
    setBusy(true);
    try {
      const user = await api.resetPassword(email.trim(), code, password);
      setUser(user);
      notify("Password changed. You're signed in.", "success");
      navigate("/", { replace: true });
    } catch (err) {
      const error = err as ApiError;
      if (error.fields?.new_password) setErrors({ password: error.fields.new_password });
      else setErrors({ code: error.message });
      if (error.code !== "invalid") setCode("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthLayout>
      {step === "email" ? (
        <>
          <h1>Reset your password</h1>
          <p className="lead">Enter your email and we'll send you a code to choose a new password.</p>
          <form onSubmit={sendCode} noValidate>
            <Field label="Email address" htmlFor="email" error={errors.email}>
              <input
                id="email"
                type="email"
                className={`input${errors.email ? " invalid" : ""}`}
                autoComplete="email"
                autoFocus
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@example.com"
              />
            </Field>
            <div style={{ marginTop: 24 }}>
              <Button type="submit" size="lg" block loading={busy}>
                {busy ? "Sending" : "Send code"}
              </Button>
            </div>
          </form>
        </>
      ) : (
        <>
          <h1>Choose a new password</h1>
          <p className="lead">
            Enter the code we sent to <span className="email-pill">{email.trim()}</span> and your new password.
          </p>
          {errors.form && <div className="banner banner-error">{errors.form}</div>}
          <form onSubmit={reset} noValidate>
            <CodeInput value={code} onChange={setCode} invalid={!!errors.code} disabled={busy} />
            {errors.code && (
              <p className="error-text" style={{ marginTop: 10 }}>
                {errors.code}
              </p>
            )}
            <div style={{ marginTop: 22 }}>
              <Field label="New password" htmlFor="new-password" error={errors.password} hint="At least 8 characters, with a letter and a number.">
                <PasswordInput id="new-password" autoComplete="new-password" value={password} invalid={!!errors.password} onChange={(e) => setPassword(e.target.value)} />
              </Field>
              <Field label="Confirm new password" htmlFor="confirm-password" error={errors.confirm}>
                <PasswordInput id="confirm-password" autoComplete="new-password" value={confirm} invalid={!!errors.confirm} onChange={(e) => setConfirm(e.target.value)} />
              </Field>
            </div>
            <div style={{ marginTop: 24 }}>
              <Button type="submit" size="lg" block loading={busy}>
                {busy ? "Saving" : "Set new password"}
              </Button>
            </div>
          </form>
          <p className="resend-row">
            {wait > 0 ? (
              <span>You can ask for a new code in {wait}s.</span>
            ) : (
              <button type="button" className="link-btn" onClick={() => void sendCode()} disabled={busy}>
                Send a new code
              </button>
            )}
          </p>
        </>
      )}
      <p className="auth-switch">
        Remembered it? <Link to="/signin" state={{ email: email.trim() }}>Back to sign in</Link>
      </p>
    </AuthLayout>
  );
}
