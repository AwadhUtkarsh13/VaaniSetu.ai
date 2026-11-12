import { useState, type FormEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router";
import { api, ApiError } from "../api";
import { useAuth } from "../auth";
import { Button, Field, PasswordInput } from "../components/ui";
import AuthLayout, { EMAIL_RE } from "./AuthLayout";

export default function SignIn() {
  const { setUser } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const notice = (location.state as { notice?: string } | null)?.notice;
  const [email, setEmail] = useState((location.state as { email?: string } | null)?.email ?? "");
  const [password, setPassword] = useState("");
  const [errors, setErrors] = useState<{ email?: string; password?: string; form?: string }>({});
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    const next: typeof errors = {};
    if (!EMAIL_RE.test(email.trim())) next.email = "Enter a valid email address.";
    if (!password) next.password = "Enter your password.";
    setErrors(next);
    if (Object.keys(next).length) return;

    setBusy(true);
    try {
      const user = await api.login(email.trim(), password);
      setUser(user);
      navigate(user.email_verified ? "/" : "/verify", { replace: true });
    } catch (err) {
      const error = err as ApiError;
      if (error.code === "not_registered") {
        navigate("/signup", {
          state: {
            email: email.trim(),
            notice: "There's no account for this email yet. Create one below - it only takes a minute.",
          },
        });
        return;
      }
      if (error.code === "wrong_password") setErrors({ password: "That password isn't right. Please try again." });
      else if (error.fields?.email) setErrors({ email: error.fields.email });
      else setErrors({ form: error.message });
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthLayout>
      <h1>Welcome back</h1>
      <p className="lead">Sign in to continue to your assistant.</p>
      {notice && <div className="banner banner-info">{notice}</div>}
      {errors.form && <div className="banner banner-error">{errors.form}</div>}
      <form onSubmit={submit} noValidate>
        <Field label="Email address" htmlFor="email" error={errors.email}>
          <input
            id="email"
            className={`input${errors.email ? " invalid" : ""}`}
            type="email"
            autoComplete="email"
            autoFocus
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@example.com"
          />
        </Field>
        <Field label="Password" htmlFor="password" error={errors.password}>
          <PasswordInput
            id="password"
            autoComplete="current-password"
            value={password}
            invalid={!!errors.password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </Field>
        <div className="forgot-row">
          <Link to="/forgot" state={{ email: email.trim() }}>
            Forgot password?
          </Link>
        </div>
        <div style={{ marginTop: 20 }}>
          <Button type="submit" size="lg" block loading={busy}>
            {busy ? "Signing in" : "Sign in"}
          </Button>
        </div>
      </form>
      <p className="auth-switch">
        New here? <Link to="/signup" state={{ email: email.trim() }}>Create an account</Link>
      </p>
    </AuthLayout>
  );
}
