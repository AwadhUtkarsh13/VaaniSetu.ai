import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router";
import { api } from "../api";
import { useAuth } from "../auth";
import { ChatProvider, useChatSession } from "./ChatSession";
import { LibraryProvider, useLibrary } from "./Library";
import { Avatar, Button, ConfirmDialog, Skeleton, useToast } from "./ui";

function Sidebar({ onNavigate }: { onNavigate: () => void }) {
  const { user } = useAuth();
  const { conversations, applications, removeConversation } = useLibrary();
  const { activeId, open, streaming } = useChatSession();
  const navigate = useNavigate();
  const notify = useToast();
  const [pendingDelete, setPendingDelete] = useState<{ id: string; title: string } | null>(null);
  const [deleting, setDeleting] = useState(false);

  async function confirmDelete() {
    if (!pendingDelete) return;
    setDeleting(true);
    try {
      await api.deleteConversation(pendingDelete.id);
      removeConversation(pendingDelete.id);
      if (activeId === pendingDelete.id) {
        open(null);
        navigate("/");
      }
      notify("Conversation deleted");
    } catch (err) {
      notify((err as Error).message, "error");
    } finally {
      setDeleting(false);
      setPendingDelete(null);
    }
  }

  return (
    <aside className="sidebar">
      <div className="sidebar-brand">
        <span className="name">
          VaaniSetu <span className="brand-ai">AI</span>
        </span>
        <span className="sub">Welfare help</span>
      </div>
      <Button
        block
        disabled={streaming}
        onClick={() => {
          open(null);
          navigate("/");
          onNavigate();
        }}
      >
        New chat
      </Button>
      <nav className="nav">
        <NavLink to="/applications" onClick={onNavigate}>
          My applications
          <span className="count">{applications ? applications.length : ""}</span>
        </NavLink>
        <NavLink to="/profile" onClick={onNavigate}>
          Profile
        </NavLink>
      </nav>

      <div className="section-label">Recent chats</div>
      <div className="conv-list">
        {conversations === null ? (
          [80, 65, 72].map((w) => <Skeleton key={w} width={`${w}%`} height={16} style={{ margin: "10px 12px" }} />)
        ) : conversations.length === 0 ? (
          <p className="conv-empty">Your conversations will appear here.</p>
        ) : (
          conversations.map((c) => (
            <div key={c.id} className={`conv-item${c.id === activeId ? " active" : ""}`}>
              <Link to={`/c/${c.id}`} title={c.title} onClick={onNavigate}>
                {c.title}
              </Link>
              <button className="remove" onClick={() => setPendingDelete({ id: c.id, title: c.title })}>
                Delete
              </button>
            </div>
          ))
        )}
      </div>

      {user && (
        <div className="account">
          <Link to="/profile" onClick={onNavigate}>
            <Avatar user={user} size={38} />
            <span className="who">
              <b>{user.name}</b>
              <span>{user.email}</span>
            </span>
          </Link>
        </div>
      )}

      {pendingDelete && (
        <ConfirmDialog
          title="Delete this conversation?"
          message={`"${pendingDelete.title}" will be removed permanently. Saved applications are kept.`}
          confirmLabel="Delete"
          busy={deleting}
          onConfirm={confirmDelete}
          onCancel={() => setPendingDelete(null)}
        />
      )}
    </aside>
  );
}

function Shell() {
  const [menuOpen, setMenuOpen] = useState(false);
  const location = useLocation();
  useEffect(() => setMenuOpen(false), [location.pathname]);
  return (
    <div className={`shell${menuOpen ? " menu-open" : ""}`}>
      <Sidebar onNavigate={() => setMenuOpen(false)} />
      <div className="scrim" onClick={() => setMenuOpen(false)} />
      <div className="main">
        <header className="topbar">
          <span className="name">
            VaaniSetu <span className="brand-ai">AI</span>
          </span>
          <Button variant="secondary" size="sm" onClick={() => setMenuOpen(true)}>
            Menu
          </Button>
        </header>
        <Outlet />
      </div>
    </div>
  );
}

export default function AppShell() {
  return (
    <LibraryProvider>
      <ChatProvider>
        <Shell />
      </ChatProvider>
    </LibraryProvider>
  );
}
