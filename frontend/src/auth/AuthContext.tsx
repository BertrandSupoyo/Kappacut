import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { jget, jpost } from "@/api/client";
import type { User } from "@/api/types";

interface AuthValue {
  user: User | null;
  loading: boolean;
  /** this person's session ran out, as opposed to never having signed in or signing out */
  expired: boolean;
  refresh: () => Promise<void>;
  logout: () => Promise<void>;
}

const Ctx = createContext<AuthValue>({
  user: null,
  loading: true,
  expired: false,
  refresh: async () => {},
  logout: async () => {},
});

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [expired, setExpired] = useState(false);
  // Who this tab last belonged to. The chat, the open project and cached lists stay in memory so that
  // someone whose session ran out can carry on. They must never reach anyone else, and loading the page
  // again is what clears all of them at once, including any added later.
  const owner = useRef<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const me = await jget<User>("/api/users/me");
      if (owner.current && owner.current !== me.id) {
        window.location.replace("/app");
        return;
      }
      owner.current = me.id;
      setExpired(false);
      setUser(me);
    } catch {
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  const logout = useCallback(async () => {
    await jpost("/api/auth/logout").catch(() => {});
    window.location.replace("/");
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    const onExpired = () => {
      if (owner.current) setExpired(true);
      setUser(null);
    };
    window.addEventListener("auth:expired", onExpired);
    return () => window.removeEventListener("auth:expired", onExpired);
  }, []);

  const value = useMemo(() => ({ user, loading, expired, refresh, logout }), [user, loading, expired, refresh, logout]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export const useAuth = () => useContext(Ctx);
