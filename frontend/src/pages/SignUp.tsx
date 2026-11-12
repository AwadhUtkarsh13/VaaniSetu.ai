import { useState, type FormEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router";
import { api, ApiError } from "../api";
import { useAuth } from "../auth";
import { Button, Field, PasswordInput } from "../components/ui";
import AuthLayout, { EMAIL_RE } from "./AuthLayout";

type Errors = Partial<Record<"name" | "email" | "phone" | "password" | "confirm" | "form", string>>;

export function passwordProblem(value: string): string | undefined {
  if (value.length < 8) return "Use at least 8 characters.";
  if (!/[A-Za-z]/.test(value) || !/\d/.test(value)) return "Include at least one letter and one number.";
  return undefined;
}

export default function SignUp() {
  const { setUser } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const state = location.state as { email?: string; notice?: string } | null;
  const [form, setForm] = useState({ name: "", email: state?.email ?? "", phone: "", password: "", confirm: "" });
  const [errors, setErrors] = useState<Errors>({});
  const [busy, setBusy] = useState(false);

  const set = (key: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm((f) => ({ ...f, [key]: e.target.value }));

  function validate(): Errors {
    const next: Errors = {};
    if (form.name.trim().length < 2) next.name = "Enter your full name.";
    if (!EMAIL_RE.test(form.email.trim())) next.email = "Enter a valid email address.";
    const digits = form.phone.replace(/\D/g, "");
    if (digits.length < 7 || digits.length > 15 || !/^\+?[0-9 ()-]+$/.test(form.phone.trim()))
      next.phone = "Enter a valid phone number, for example +91 98765 43210.";
    const pw = passwordProblem(form.password);
    if (pw) next.password = pw;
    if (form.confirm !== form.password) next.confirm = "The passwords don't match.";
    return next;
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    const next = validate();
    setErrors(next);
    if (Object.keys(next).length) return;
    setBusy(true);
    try {
      const user = await api.signup({
        name: form.name.trim(),
        email: form.email.trim(),
        phone: form.phone.trim(),
        password: form.password,
      });
      setUser(user);
      navigate("/verify", { replace: true });
    } catch (err) {
      const error = err as ApiError;
      if (error.code === "already_registered") {
        navigate("/signin", { state: { email: form.email.trim(), notice: "You already have an account. Please sign in." } });
        return;
      }
      setErrors({ ...error.fields, form: Object.keys(error.fields ?? {}).length ? undefined : error.message });
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthLayout>
      <h1>Create your account</h1>
      <p className="lead">A few details and you're ready to go.</p>
      {state?.notice && <div className="banner banner-info">{state.notice}</div>}
      {errors.form && <div className="banner banner-error">{errors.form}</div>}
      <form onSubmit={submit} noValidate>
        <Field label="Full name" htmlFor="name" error={errors.name}>
          <input id="name" className={`input${errors.name ? " invalid" : ""}`} autoComplete="name" autoFocus={!state?.email ? true : undefined} value={form.name} onChange={set("name")} placeholder="Asha Rao" />
        </Field>
        <Field label="Email address" htmlFor="email" error={errors.email}>
          <input id="email" type="email" className={`input${errors.email ? " invalid" : ""}`} autoComplete="email" value={form.email} onChange={set("email")} placeholder="you@example.com" />
        </Field>
        <Field label="Phone number" htmlFor="phone" error={errors.phone}>
          <input id="phone" type="tel" className={`input${errors.phone ? " invalid" : ""}`} autoComplete="tel" value={form.phone} onChange={set("phone")} placeholder="+91 98765 43210" />
        </Field>
        <Field label="Password" htmlFor="password" error={errors.password} hint="At least 8 characters, with a letter and a number.">
          <PasswordInput id="password" autoComplete="new-password" value={form.password} invalid={!!errors.password} onChange={set("password")} />
        </Field>
        <Field label="Confirm password" htmlFor="confirm" error={errors.confirm}>
          <PasswordInput id="confirm" autoComplete="new-password" value={form.confirm} invalid={!!errors.confirm} onChange={set("confirm")} />
        </Field>
        <div style={{ marginTop: 24 }}>
          <Button type="submit" size="lg" block loading={busy}>
            {busy ? "Creating your account" : "Create account"}
          </Button>
        </div>
      </form>
      <p className="auth-switch">
        Already have an account? <Link to="/signin" state={{ email: form.email.trim() }}>Sign in</Link>
      </p>
    </AuthLayout>
  );
}
