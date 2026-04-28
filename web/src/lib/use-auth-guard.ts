"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { fetchMe } from "@/lib/api";
import {
  clearStoredAuthSession,
  getDefaultRouteForRole,
  getStoredAuthSession,
  setStoredAuthSession,
  type AuthRole,
  type StoredAuthSession,
} from "@/store/auth";

type UseAuthGuardResult = {
  isCheckingAuth: boolean;
  session: StoredAuthSession | null;
};

export function useAuthGuard(allowedRoles?: AuthRole[]): UseAuthGuardResult {
  const router = useRouter();
  const [session, setSession] = useState<StoredAuthSession | null>(null);
  const [isCheckingAuth, setIsCheckingAuth] = useState(true);
  const allowedRolesKey = (allowedRoles || []).join(",");

  useEffect(() => {
    let active = true;

    const load = async () => {
      const roleList = allowedRolesKey ? (allowedRolesKey.split(",") as AuthRole[]) : [];
      const storedSession = await getStoredAuthSession();
      if (!active) {
        return;
      }

      if (!storedSession) {
        setSession(null);
        setIsCheckingAuth(false);
        router.replace("/login");
        return;
      }

      let verifiedSession: StoredAuthSession;
      try {
        const data = await fetchMe(false);
        verifiedSession = {
          key: storedSession.key,
          role: data.user.role,
          subjectId: data.user.id,
          name: data.user.email,
        };
        await setStoredAuthSession(verifiedSession);
      } catch {
        await clearStoredAuthSession();
        if (!active) {
          return;
        }
        setSession(null);
        setIsCheckingAuth(false);
        router.replace("/login");
        return;
      }

      if (!active) {
        return;
      }

      if (roleList.length > 0 && !roleList.includes(verifiedSession.role)) {
        setSession(verifiedSession);
        setIsCheckingAuth(false);
        router.replace(getDefaultRouteForRole(verifiedSession.role));
        return;
      }

      setSession(verifiedSession);
      setIsCheckingAuth(false);
    };

    void load();
    return () => {
      active = false;
    };
  }, [allowedRolesKey, router]);

  return { isCheckingAuth, session };
}

export function useRedirectIfAuthenticated() {
  const router = useRouter();
  const [isCheckingAuth, setIsCheckingAuth] = useState(true);

  useEffect(() => {
    let active = true;

    const load = async () => {
      const storedSession = await getStoredAuthSession();
      if (!active) {
        return;
      }

      if (storedSession) {
        try {
          const data = await fetchMe(false);
          const verifiedSession: StoredAuthSession = {
            key: storedSession.key,
            role: data.user.role,
            subjectId: data.user.id,
            name: data.user.email,
          };
          await setStoredAuthSession(verifiedSession);
          if (active) {
            router.replace(getDefaultRouteForRole(verifiedSession.role));
          }
          return;
        } catch {
          await clearStoredAuthSession();
        }
      }

      setIsCheckingAuth(false);
    };

    void load();
    return () => {
      active = false;
    };
  }, [router]);

  return { isCheckingAuth };
}
