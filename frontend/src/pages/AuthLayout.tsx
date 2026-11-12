import type { ReactNode } from "react";

export default function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <div className="auth">
      <aside className="auth-brand">
        <div className="brand-name">
          VaaniSetu <span className="brand-ai">AI</span>
        </div>
        <div>
          <h1>Government welfare help, in your own language.</h1>
          <ul>
            <li>
              <strong>Speak or type, any way you like</strong>
              English, Hindi or Hinglish — "PM Kisan ka paisa kab aayega?" works just fine.
            </li>
            <li>
              <strong>Answers from the official rules</strong>
              PM-KISAN and Ayushman Bharat PM-JAY, with the exact rule each answer comes from.
            </li>
            <li>
              <strong>Check eligibility, prepare a complaint</strong>
              Step-by-step eligibility checks and ready-to-file CPGRAMS grievances. Nothing is final until
              you confirm.
            </li>
          </ul>
        </div>
        <footer>Aadhaar, PAN, phone and bank numbers are masked before the AI ever sees them.</footer>
      </aside>
      <main className="auth-main">
        <div className="auth-card">
          <div className="mobile-brand">
            VaaniSetu <span className="brand-ai">AI</span>
          </div>
          {children}
        </div>
      </main>
    </div>
  );
}

export const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;
