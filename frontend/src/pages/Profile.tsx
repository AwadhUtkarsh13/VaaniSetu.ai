import { useEffect, useRef, useState, type FormEvent } from "react";
import { useNavigate } from "react-router";
import { api, ApiError, formatDate, LANGUAGES, type Gender, type User } from "../api";
import { useAuth } from "../auth";
import { Avatar, Button, Field, PasswordInput, useToast } from "../components/ui";
import { passwordProblem } from "./SignUp";

const GENDERS: { value: Gender; label: string }[] = [
  { value: "female", label: "Female" },
  { value: "male", label: "Male" },
  { value: "non_binary", label: "Non-binary" },
  { value: "other", label: "Other" },
  { value: "prefer_not_to_say", label: "Prefer not to say" },
];

const PHOTO_TYPES = ["image/jpeg", "image/png", "image/webp"];

function formFrom(user: User) {
  return {
    name: user.name,
    phone: user.phone,
    dob: user.dob ?? "",
    gender: (user.gender ?? "") as Gender | "",
    preferred_language: user.preferred_language || "auto",
  };
}

function PhotoCard({ user }: { user: User }) {
  const { setUser } = useAuth();
  const notify = useToast();
  const inputRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState<"upload" | "remove" | null>(null);

  async function choose(file: File) {
    if (!PHOTO_TYPES.includes(file.type)) return notify("Please choose a JPG, PNG or WebP image.", "error");
    if (file.size > 3 * 1024 * 1024) return notify("Photos can be up to 3 MB.", "error");
    setBusy("upload");
    try {
      setUser(await api.uploadPhoto(file));
      notify("Photo updated", "success");
    } catch (err) {
      notify((err as Error).message, "error");
    } finally {
      setBusy(null);
    }
  }

  async function remove() {
    setBusy("remove");
    try {
      setUser(await api.deletePhoto());
      notify("Photo removed");
    } catch (err) {
      notify((err as Error).message, "error");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="photo-row">
      <Avatar user={user} size={96} />
      <div>
        <h2>{user.name}</h2>
        <p className="muted">Member since {formatDate(user.created_at)}</p>
        <div className="actions">
          <Button variant="secondary" size="sm" loading={busy === "upload"} onClick={() => inputRef.current?.click()}>
            {user.has_photo ? "Change photo" : "Upload photo"}
          </Button>
          {user.has_photo && (
            <Button variant="ghost" size="sm" loading={busy === "remove"} onClick={remove}>
              Remove photo
            </Button>
          )}
        </div>
        <input
          ref={inputRef}
          type="file"
          accept={PHOTO_TYPES.join(",")}
          hidden
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) void choose(file);
            e.target.value = "";
          }}
        />
      </div>
    </div>
  );
}

function DetailsForm({ user }: { user: User }) {
  const { setUser } = useAuth();
  const notify = useToast();
  const [form, setForm] = useState(() => formFrom(user));
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  useEffect(() => setForm(formFrom(user)), [user]);

  const original = formFrom(user);
  const dirty = (Object.keys(form) as (keyof typeof form)[]).some((k) => form[k] !== original[k]);
  const today = new Date().toISOString().slice(0, 10);

  async function save(e: FormEvent) {
    e.preventDefault();
    const next: Record<string, string> = {};
    if (form.name.trim().length < 2) next.name = "Enter your full name.";
    const digits = form.phone.replace(/\D/g, "");
    if (digits.length < 7 || digits.length > 15) next.phone = "Enter a valid phone number.";
    if (form.dob && form.dob > today) next.dob = "Date of birth can't be in the future.";
    setErrors(next);
    if (Object.keys(next).length) return;

    const changes: Parameters<typeof api.updateProfile>[0] = {};
    if (form.name !== original.name) changes.name = form.name.trim();
    if (form.phone !== original.phone) changes.phone = form.phone.trim();
    if (form.dob !== original.dob) changes.dob = form.dob || null;
    if (form.gender !== original.gender) changes.gender = form.gender || null;
    if (form.preferred_language !== original.preferred_language) changes.preferred_language = form.preferred_language;
    setSaving(true);
    try {
      setUser(await api.updateProfile(changes));
      notify("Profile saved", "success");
    } catch (err) {
      const error = err as ApiError;
      if (Object.keys(error.fields ?? {}).length) setErrors(error.fields);
      else notify(error.message, "error");
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={save} noValidate>
      <div className="row">
        <Field label="Full name" htmlFor="p-name" error={errors.name}>
          <input id="p-name" className={`input${errors.name ? " invalid" : ""}`} value={form.name} autoComplete="name" onChange={(e) => setForm({ ...form, name: e.target.value })} />
        </Field>
        <Field label="Email address" htmlFor="p-email" hint="Your email is used to sign in and can't be changed.">
          <input id="p-email" className="input" value={user.email} disabled />
        </Field>
      </div>
      <div className="row" style={{ marginTop: 16 }}>
        <Field label="Phone number" htmlFor="p-phone" error={errors.phone}>
          <input id="p-phone" type="tel" className={`input${errors.phone ? " invalid" : ""}`} value={form.phone} autoComplete="tel" onChange={(e) => setForm({ ...form, phone: e.target.value })} />
        </Field>
        <Field label="Date of birth" htmlFor="p-dob" error={errors.dob}>
          <input id="p-dob" type="date" className={`input${errors.dob ? " invalid" : ""}`} value={form.dob} max={today} min="1900-01-01" onChange={(e) => setForm({ ...form, dob: e.target.value })} />
        </Field>
      </div>
      <div className="row" style={{ marginTop: 16 }}>
        <Field label="Gender" htmlFor="p-gender" error={errors.gender}>
          <select id="p-gender" className="input" value={form.gender} onChange={(e) => setForm({ ...form, gender: e.target.value as Gender | "" })}>
            <option value="">Select</option>
            {GENDERS.map((g) => (
              <option key={g.value} value={g.value}>
                {g.label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Reply language" htmlFor="p-lang" hint="New chats reply in this language. You can change it in any chat.">
          <select id="p-lang" className="input" value={form.preferred_language} onChange={(e) => setForm({ ...form, preferred_language: e.target.value })}>
            {LANGUAGES.map((l) => (
              <option key={l.code} value={l.code}>
                {l.label}
              </option>
            ))}
          </select>
        </Field>
      </div>
      <div className="form-actions">
        {dirty && (
          <Button type="button" variant="ghost" onClick={() => setForm(original)} disabled={saving}>
            Discard
          </Button>
        )}
        <Button type="submit" disabled={!dirty} loading={saving}>
          Save changes
        </Button>
      </div>
    </form>
  );
}

function PasswordForm() {
  const notify = useToast();
  const [form, setForm] = useState({ current: "", next: "", confirm: "" });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);

  async function save(e: FormEvent) {
    e.preventDefault();
    const next: Record<string, string> = {};
    if (!form.current) next.current = "Enter your current password.";
    const problem = passwordProblem(form.next);
    if (problem) next.next = problem;
    if (form.confirm !== form.next) next.confirm = "The passwords don't match.";
    setErrors(next);
    if (Object.keys(next).length) return;
    setSaving(true);
    try {
      await api.changePassword(form.current, form.next);
      setForm({ current: "", next: "", confirm: "" });
      notify("Password updated. Other devices have been signed out.", "success");
    } catch (err) {
      const error = err as ApiError;
      if (error.code === "wrong_password") setErrors({ current: error.message });
      else if (error.fields?.new_password) setErrors({ next: error.fields.new_password });
      else notify(error.message, "error");
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={save} noValidate>
      <Field label="Current password" htmlFor="pw-current" error={errors.current}>
        <PasswordInput id="pw-current" autoComplete="current-password" value={form.current} invalid={!!errors.current} onChange={(e) => setForm({ ...form, current: e.target.value })} />
      </Field>
      <div className="row" style={{ marginTop: 16 }}>
        <Field label="New password" htmlFor="pw-new" error={errors.next} hint="At least 8 characters, with a letter and a number.">
          <PasswordInput id="pw-new" autoComplete="new-password" value={form.next} invalid={!!errors.next} onChange={(e) => setForm({ ...form, next: e.target.value })} />
        </Field>
        <Field label="Confirm new password" htmlFor="pw-confirm" error={errors.confirm}>
          <PasswordInput id="pw-confirm" autoComplete="new-password" value={form.confirm} invalid={!!errors.confirm} onChange={(e) => setForm({ ...form, confirm: e.target.value })} />
        </Field>
      </div>
      <div className="form-actions">
        <Button type="submit" variant="secondary" loading={saving}>
          Update password
        </Button>
      </div>
    </form>
  );
}

export default function ProfilePage() {
  const { user, signOut } = useAuth();
  const navigate = useNavigate();
  const [signingOut, setSigningOut] = useState(false);
  if (!user) return null;

  return (
    <div className="page">
      <div className="page-inner page-enter">
        <div className="page-head">
          <div>
            <h1>Your profile</h1>
            <p className="lead">Keep your details up to date. They're saved to your account.</p>
          </div>
        </div>

        <div className="card">
          <PhotoCard user={user} />
          <DetailsForm user={user} />
        </div>

        <div className="card">
          <div className="card-head">
            <h2>Password</h2>
          </div>
          <PasswordForm />
        </div>

        <div className="card">
          <div className="card-head">
            <div>
              <h2>Sign out</h2>
              <p className="muted" style={{ marginTop: 4 }}>
                You'll need your email and password to sign in again.
              </p>
            </div>
            <Button
              variant="secondary"
              loading={signingOut}
              onClick={async () => {
                setSigningOut(true);
                await signOut();
                navigate("/signin", { replace: true });
              }}
            >
              Sign out
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
