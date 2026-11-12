import { createContext, useContext, useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import api, { bootstrapSession, setAccessToken } from '../api/client';
import { authApi } from '../api/endpoints';
import type { User } from '../types';

interface AuthValue {
  user: User | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    bootstrapSession().then((u) => setUser(u)).finally(() => setLoading(false));
    const onLogout = () => setUser(null);
    window.addEventListener('kb-logout', onLogout);
    return () => window.removeEventListener('kb-logout', onLogout);
  }, []);

  const login = async (email: string, password: string) => {
    const res = await authApi.login(email, password);
    setAccessToken(res.data.access_token);
    const me = await authApi.me();
    setUser(me.data);
  };

  const logout = async () => {
    try { await authApi.logout(); } catch { /* 忽略网络错误，本地态照常清理 */ }
    setAccessToken(null);
    setUser(null);
  };

  return <AuthContext.Provider value={{ user, loading, login, logout }}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth 必须在 AuthProvider 内使用');
  return ctx;
}

export { api };
