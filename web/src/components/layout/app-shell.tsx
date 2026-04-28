"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import {
  Bot,
  Brush,
  ChevronLeft,
  Gift,
  ImageIcon,
  LogOut,
  Menu,
  ScrollText,
  Server,
  Settings,
  Sparkles,
  Ticket,
  Users,
  X,
} from "lucide-react";

import webConfig from "@/constants/common-env";
import { fetchMe, logout } from "@/lib/api";
import { cn } from "@/lib/utils";
import {
  clearStoredAuthSession,
  getStoredAuthSession,
  setStoredAuthSession,
  type StoredAuthSession,
} from "@/store/auth";
import {
  getNavigationGroups,
  getPageMeta,
  isPublicAppRoute,
  normalizeDashboardPath,
  type NavIcon,
} from "@/components/layout/nav-config";

const iconMap: Record<NavIcon, React.ComponentType<{ className?: string }>> = {
  brush: Brush,
  gift: Gift,
  users: Users,
  ticket: Ticket,
  sparkles: Sparkles,
  server: Server,
  bot: Bot,
  image: ImageIcon,
  logs: ScrollText,
  settings: Settings,
};

function AppLogo({ compact = false }: { compact?: boolean }) {
  return (
    <Link href="/image" className="flex min-w-0 items-center gap-3">
      <span className="grid size-10 shrink-0 place-items-center rounded-2xl bg-gradient-to-br from-teal-400 to-teal-600 text-white shadow-[0_16px_40px_-18px_rgba(13,148,136,0.9)]">
        <Sparkles className="size-5" />
      </span>
      {compact ? null : (
        <span className="min-w-0">
          <span className="block truncate text-base font-black tracking-tight text-slate-950">Genapi</span>
          <span className="block truncate text-xs font-medium text-slate-400">Image Console</span>
        </span>
      )}
    </Link>
  );
}

function SidebarContent({
  session,
  pathname,
  onNavigate,
}: {
  session: StoredAuthSession;
  pathname: string;
  onNavigate?: () => void;
}) {
  const normalizedPath = normalizeDashboardPath(pathname);
  const groups = getNavigationGroups(session.role);

  return (
    <div className="flex h-full flex-col">
      <div className="flex h-16 items-center border-b border-slate-200/70 px-5">
        <AppLogo />
      </div>
      <nav className="flex-1 overflow-y-auto px-3 py-4">
        {groups.map((group) => (
          <div key={group.label} className="mb-6">
            <div className="px-3 pb-2 text-[11px] font-bold uppercase tracking-[0.18em] text-slate-400">
              {group.label}
            </div>
            <div className="space-y-1">
              {group.items.map((item) => {
                const Icon = iconMap[item.icon];
                const active = normalizedPath === item.href || pathname === item.href;
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    onClick={onNavigate}
                    className={cn(
                      "group flex items-center gap-3 rounded-2xl px-3 py-2.5 text-sm font-semibold transition",
                      active
                        ? "bg-teal-50 text-teal-700 shadow-[inset_0_0_0_1px_rgba(20,184,166,0.16)]"
                        : "text-slate-600 hover:bg-slate-100/80 hover:text-slate-950",
                    )}
                  >
                    <span
                      className={cn(
                        "grid size-9 shrink-0 place-items-center rounded-xl transition",
                        active ? "bg-teal-500 text-white shadow-sm" : "bg-white text-slate-400 group-hover:text-teal-600",
                      )}
                    >
                      <Icon className="size-4" />
                    </span>
                    <span className="min-w-0">
                      <span className="block truncate">{item.label}</span>
                      <span className="block truncate text-xs font-medium opacity-60">{item.description}</span>
                    </span>
                  </Link>
                );
              })}
            </div>
          </div>
        ))}
      </nav>
      <div className="border-t border-slate-200/70 p-4 text-xs text-slate-400">
        <div className="rounded-2xl bg-slate-50 p-3">
          <div className="font-semibold text-slate-600">{session.role === "admin" ? "管理员" : "普通用户"}</div>
          <div className="mt-1 truncate">{session.name}</div>
        </div>
      </div>
    </div>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [session, setSession] = useState<StoredAuthSession | null>(null);
  const [mobileOpen, setMobileOpen] = useState(false);
  const isPublic = isPublicAppRoute(pathname);
  const meta = useMemo(() => getPageMeta(pathname), [pathname]);

  useEffect(() => {
    let active = true;

    const loadSession = async () => {
      if (isPublic) {
        setSession(null);
        return;
      }

      const storedSession = await getStoredAuthSession();
      if (!active) {
        return;
      }
      if (!storedSession) {
        setSession(null);
        return;
      }
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
          setSession(verifiedSession);
        }
      } catch {
        await clearStoredAuthSession();
        if (active) {
          setSession(null);
        }
      }
    };

    void loadSession();
    return () => {
      active = false;
    };
  }, [isPublic, pathname]);

  const handleLogout = async () => {
    try {
      await logout();
    } catch {
      // Local cleanup still has to happen if the server is unavailable.
    }
    await clearStoredAuthSession();
    setSession(null);
    router.replace("/login");
  };

  if (isPublic || !session) {
    return (
      <main className="min-h-screen overflow-hidden bg-[radial-gradient(circle_at_top_left,_rgba(240,253,250,0.98),_rgba(248,250,252,0.98)_42%,_rgba(241,245,249,1)_100%)] text-slate-950">
        {children}
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-slate-50 text-slate-950">
      <div className="pointer-events-none fixed inset-0 bg-[radial-gradient(circle_at_20%_10%,rgba(20,184,166,0.13),transparent_28%),radial-gradient(circle_at_80%_0%,rgba(14,165,233,0.1),transparent_28%),linear-gradient(rgba(15,23,42,0.035)_1px,transparent_1px),linear-gradient(90deg,rgba(15,23,42,0.035)_1px,transparent_1px)] bg-[size:auto,auto,64px_64px,64px_64px]" />

      <aside className="fixed inset-y-0 left-0 z-40 hidden w-72 border-r border-slate-200/80 bg-white/88 backdrop-blur-xl lg:block">
        <SidebarContent session={session} pathname={pathname} />
      </aside>

      {mobileOpen ? (
        <div className="fixed inset-0 z-50 lg:hidden">
          <button
            type="button"
            aria-label="关闭菜单"
            className="absolute inset-0 bg-slate-950/40"
            onClick={() => setMobileOpen(false)}
          />
          <aside className="relative h-full w-[min(88vw,320px)] border-r border-slate-200 bg-white shadow-2xl">
            <button
              type="button"
              className="absolute right-3 top-3 grid size-9 place-items-center rounded-xl text-slate-400 hover:bg-slate-100 hover:text-slate-900"
              onClick={() => setMobileOpen(false)}
              aria-label="关闭菜单"
            >
              <X className="size-5" />
            </button>
            <SidebarContent session={session} pathname={pathname} onNavigate={() => setMobileOpen(false)} />
          </aside>
        </div>
      ) : null}

      <div className="relative flex min-h-screen flex-col lg:pl-72">
        <header className="sticky top-0 z-30 border-b border-white/70 bg-white/78 backdrop-blur-xl">
          <div className="flex h-16 items-center justify-between gap-3 px-4 md:px-6 lg:px-8">
            <div className="flex min-w-0 items-center gap-3">
              <button
                type="button"
                className="grid size-10 place-items-center rounded-2xl border border-slate-200 bg-white text-slate-600 shadow-sm lg:hidden"
                onClick={() => setMobileOpen(true)}
                aria-label="打开菜单"
              >
                <Menu className="size-5" />
              </button>
              <div className="min-w-0">
                <h1 className="truncate text-base font-black tracking-tight text-slate-950 md:text-lg">
                  {meta.title}
                </h1>
                <p className="hidden truncate text-xs font-medium text-slate-500 md:block">{meta.description}</p>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <span className="hidden rounded-full bg-teal-50 px-3 py-1 text-xs font-bold text-teal-700 sm:inline-flex">
                v{webConfig.appVersion}
              </span>
              <button
                type="button"
                className="inline-flex h-10 items-center gap-2 rounded-2xl border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-600 shadow-sm transition hover:border-rose-200 hover:bg-rose-50 hover:text-rose-600"
                onClick={() => void handleLogout()}
              >
                <LogOut className="size-4" />
                <span className="hidden sm:inline">退出</span>
              </button>
            </div>
          </div>
        </header>
        <div className="flex-1 px-4 py-5 md:px-6 lg:px-8">
          <div className="mx-auto max-w-[1540px] animate-in fade-in slide-in-from-bottom-2 duration-300">{children}</div>
        </div>
        <button
          type="button"
          className="fixed bottom-4 right-4 hidden size-10 place-items-center rounded-2xl border border-slate-200 bg-white text-slate-400 shadow-sm transition hover:text-slate-800 lg:grid"
          onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })}
          aria-label="回到顶部"
        >
          <ChevronLeft className="size-4 rotate-90" />
        </button>
      </div>
    </main>
  );
}
