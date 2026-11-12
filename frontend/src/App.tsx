import { useEffect, useState, type ReactNode } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router";
import { api } from "./api";
import { AuthProvider, useAuth } from "./auth";
import AppShell from "./components/AppShell";
import Splash from "./components/Splash";
import { ToastProvider } from "./components/ui";
import ApplicationsPage from "./pages/Applications";
import ChatPage from "./pages/Chat";
import ForgotPassword from "./pages/ForgotPassword";
import ProfilePage from "./pages/Profile";
import SignIn from "./pages/SignIn";
import SignUp from "./pages/SignUp";
import VerifyEmail from "./pages/VerifyEmail";

const MIN_SPLASH_MS = 900;
const MAX_WAIT_MS = 60_000;

function useServerReady(): { ready: boolean; caption?: string } {
  const [ready, setReady] = useState(false);
  const [caption, setCaption] = useState<string | undefined>();
  useEffect(() => {
    let cancelled = false;
    const started = Date.now();
    async function poll() {
      while (!cancelled) {
        try {
          const health = await api.health();
          if (health.ready || health.error || Date.now() - started > MAX_WAIT_MS) break;
          if (Date.now() - started > 2500) setCaption("Getting the assistant ready");
        } catch {
          setCaption("Connecting to VaaniSetu AI");
        }
        await new Promise((r) => setTimeout(r, 700));
      }
      const wait = Math.max(0, MIN_SPLASH_MS - (Date.now() - started));
      setTimeout(() => !cancelled && setReady(true), wait);
    }
    void poll();
    return () => {
      cancelled = true;
    };
  }, []);
  return { ready, caption };
}

function RequireAuth({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const location = useLocation();
  if (!user) return <Navigate to="/signin" replace state={{ from: location.pathname }} />;
  if (!user.email_verified) return <Navigate to="/verify" replace />;
  return <>{children}</>;
}

function RequireUnverified({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  if (!user) return <Navigate to="/signin" replace />;
  if (user.email_verified) return <Navigate to="/" replace />;
  return <>{children}</>;
}

function PublicOnly({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  if (user) return <Navigate to={user.email_verified ? "/" : "/verify"} replace />;
  return <>{children}</>;
}

function Root() {
  const { checking } = useAuth();
  const { ready, caption } = useServerReady();
  const booted = ready && !checking;
  const [splashGone, setSplashGone] = useState(false);
  useEffect(() => {
    if (!booted) return;
    const id = window.setTimeout(() => setSplashGone(true), 350);
    return () => window.clearTimeout(id);
  }, [booted]);

  return (
    <>
      {booted && (
        <Routes>
          <Route path="/signin" element={<PublicOnly><SignIn /></PublicOnly>} />
          <Route path="/signup" element={<PublicOnly><SignUp /></PublicOnly>} />
          <Route path="/forgot" element={<PublicOnly><ForgotPassword /></PublicOnly>} />
          <Route path="/verify" element={<RequireUnverified><VerifyEmail /></RequireUnverified>} />
          <Route element={<RequireAuth><AppShell /></RequireAuth>}>
            <Route index element={<ChatPage />} />
            <Route path="c/:id" element={<ChatPage />} />
            <Route path="applications" element={<ApplicationsPage />} />
            <Route path="applications/:id" element={<ApplicationsPage />} />
            <Route path="profile" element={<ProfilePage />} />
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      )}
      {!splashGone && <Splash leaving={booted} caption={caption} />}
    </>
  );
}

export default function App() {
  return (
    <ToastProvider>
      <AuthProvider>
        <BrowserRouter>
          <Root />
        </BrowserRouter>
      </AuthProvider>
    </ToastProvider>
  );
}
