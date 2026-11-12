import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, type ApplicationItem, type ConversationItem } from "../api";

interface LibraryState {
  conversations: ConversationItem[] | null;
  applications: ApplicationItem[] | null;
  refreshConversations: () => Promise<void>;
  refreshApplications: () => Promise<void>;
  touchConversation: (item: ConversationItem) => void;
  removeConversation: (id: string) => void;
  removeApplication: (id: string) => void;
}

const LibraryContext = createContext<LibraryState | null>(null);

export function LibraryProvider({ children }: { children: ReactNode }) {
  const [conversations, setConversations] = useState<ConversationItem[] | null>(null);
  const [applications, setApplications] = useState<ApplicationItem[] | null>(null);

  const refreshConversations = useCallback(async () => {
    try {
      setConversations(await api.listConversations());
    } catch {
      setConversations((c) => c ?? []);
    }
  }, []);

  const refreshApplications = useCallback(async () => {
    try {
      setApplications(await api.listApplications());
    } catch {
      setApplications((a) => a ?? []);
    }
  }, []);

  useEffect(() => {
    void refreshConversations();
    void refreshApplications();
  }, [refreshConversations, refreshApplications]);

  const touchConversation = useCallback((item: ConversationItem) => {
    setConversations((list) => [item, ...(list ?? []).filter((c) => c.id !== item.id)]);
  }, []);

  const removeConversation = useCallback((id: string) => {
    setConversations((list) => (list ?? []).filter((c) => c.id !== id));
  }, []);

  const removeApplication = useCallback((id: string) => {
    setApplications((list) => (list ?? []).filter((a) => a.id !== id));
  }, []);

  const value = useMemo(
    () => ({
      conversations,
      applications,
      refreshConversations,
      refreshApplications,
      touchConversation,
      removeConversation,
      removeApplication,
    }),
    [conversations, applications, refreshConversations, refreshApplications, touchConversation, removeConversation, removeApplication],
  );
  return <LibraryContext.Provider value={value}>{children}</LibraryContext.Provider>;
}

export function useLibrary(): LibraryState {
  const ctx = useContext(LibraryContext);
  if (!ctx) throw new Error("useLibrary must be used inside LibraryProvider");
  return ctx;
}
