import { createContext, useContext, useState, useCallback, ReactNode } from "react";

export type Role = "buyer" | "workshop" | "admin";

interface AuthState {
  role: Role | null;
  token: string | null;
}

interface AuthContextValue extends AuthState {
  login: (role: Role, token: string) => void;
  logout: () => void;
}

const STORAGE_KEY = "saathi-auth";

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

function loadInitial(): AuthState {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) return JSON.parse(raw) as AuthState;
  } catch {}
  return { role: null, token: null };
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>(loadInitial);

  const login = useCallback((role: Role, token: string) => {
    const next = { role, token };
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    setState(next);
  }, []);

  const logout = useCallback(() => {
    localStorage.removeItem(STORAGE_KEY);
    setState({ role: null, token: null });
  }, []);

  return (
    <AuthContext.Provider value={{ ...state, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
