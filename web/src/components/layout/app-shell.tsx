"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import {
  Bot,
  Brush,
  ChevronLeft,
  Crown,
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

import { logout } from "@/lib/api";
import { isAuthSessionChangedError, verifyStoredAuthSession } from "@/lib/auth-session";
import { cn } from "@/lib/utils";
import {
  clearStoredAuthSession,
  getStoredAuthSession,
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
  crown: Crown,
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
    <div className="flex h-full flex-col p-4">
      <div className="mb-8 flex h-12 items-center px-4">
        <AppLogo />
      </div>
      <nav className="flex-1 space-y-8 overflow-y-auto px-1">
        {groups.map((group) => (
          <div key={group.label}>
            <div className="px-4 pb-3 text-[10px] font-bold uppercase tracking-[0.2em] text-foreground/50">
              {group.label}
            </div>
            <div className="space-y-1.5">
              {group.items.map((item) => {
                const Icon = iconMap[item.icon];
                const active = normalizedPath === item.href || pathname === item.href;
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    onClick={onNavigate}
                    className={cn(
                      "group relative flex items-center gap-3 rounded-2xl px-4 py-2.5 text-sm font-medium transition-all duration-200",
                      active
                        ? "bg-primary/10 text-primary ring-1 ring-primary/10"
                        : "text-foreground/60 hover:bg-black/5 hover:text-foreground dark:hover:bg-white/5",
                    )}
                  >
                    <Icon
                      className={cn(
                        "size-4 transition-transform duration-200 group-hover:scale-110",
                        active ? "text-primary" : "text-foreground/45 group-hover:text-foreground/70",
                      )}
                    />
                    <span className="truncate">{item.label}</span>
                    {active ? <div className="absolute inset-y-2 left-0 w-1 rounded-full bg-primary" /> : null}
                  </Link>
                );
              })}
            </div>
          </div>
        ))}
      </nav>
      <div className="mt-auto pt-4">
        <div className="rounded-2xl bg-black/5 p-4 dark:bg-white/5">
          <div className="text-[10px] font-bold uppercase tracking-wider text-foreground/50">当前身份</div>
          <div className="mt-1 flex items-center gap-2">
            <div className="size-2 rounded-full bg-emerald-500 animate-pulse" />
            <div className="truncate text-sm font-semibold text-foreground/80">{session.name}</div>
          </div>
        </div>
      </div>
    </div>
  );
}

function MobileUserBottomNavigation({ pathname }: { pathname: string }) {
  const normalizedPath = normalizeDashboardPath(pathname);
  const items = getNavigationGroups("user").flatMap((group) => group.items);

  return (
    <nav
      aria-label="移动端用户导航"
      className="fixed inset-x-0 bottom-0 z-50 h-[calc(3.5rem+env(safe-area-inset-bottom))] border-t border-slate-200/80 bg-white px-2 pb-[calc(0.35rem+env(safe-area-inset-bottom))] pt-1.5 shadow-[0_-14px_42px_-30px_rgba(15,23,42,0.4)] backdrop-blur-xl lg:hidden"
    >
      <div className="mx-auto grid max-w-md grid-cols-4 gap-1">
        {items.map((item) => {
          const Icon = iconMap[item.icon];
          const active = normalizedPath === item.href || pathname === item.href;

          return (
            <Link
              key={item.href}
              href={item.href}
              aria-current={active ? "page" : undefined}
              className={cn(
                "group flex min-w-0 flex-col items-center gap-0.5 rounded-xl px-1 py-1 text-[10px] font-bold transition",
                active ? "bg-teal-50 text-teal-700" : "text-slate-500 hover:bg-slate-100/80 hover:text-slate-900",
              )}
            >
              <span
                className={cn(
                  "grid size-7 place-items-center rounded-lg transition",
                  active ? "bg-teal-500 text-white shadow-sm" : "bg-slate-50 text-slate-400 group-hover:text-teal-600",
                )}
              >
                <Icon className="size-3.5" />
              </span>
              <span className="block w-full truncate text-center leading-3">{item.label}</span>
            </Link>
          );
        })}
      </div>
    </nav>
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
        const verifiedSession = await verifyStoredAuthSession(storedSession);
        if (active) {
          setSession(verifiedSession);
        }
      } catch (error) {
        if (isAuthSessionChangedError(error)) {
          if (active) {
            setSession(error.latestSession);
          }
          return;
        }
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

  useEffect(() => {
    if (!mobileOpen) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setMobileOpen(false);
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [mobileOpen]);

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
      <main className="min-h-screen overflow-x-hidden bg-background text-foreground">
        {children}
      </main>
    );
  }

  const normalizedPath = normalizeDashboardPath(pathname);
  const isImageWorkspace = normalizedPath === "/image";

  return (
    <main className="min-h-screen overflow-x-hidden bg-background text-foreground transition-colors duration-500">
      <aside className="fixed left-5 top-5 bottom-5 z-40 hidden w-72 glass-panel rounded-[2.5rem] lg:block">
        <SidebarContent session={session} pathname={pathname} />
      </aside>

      {session.role === "admin" && mobileOpen ? (
        <div className="fixed inset-0 z-50 lg:hidden">
          <button
            type="button"
            aria-label="关闭菜单"
            className="absolute inset-0 bg-background/40 backdrop-blur-md"
            onClick={() => setMobileOpen(false)}
          />
          <aside
            role="dialog"
            aria-modal="true"
            aria-label="移动端管理菜单"
            className="relative h-full w-[min(88vw,320px)] border-r border-border bg-card/80 pt-[max(1rem,env(safe-area-inset-top))] pb-[max(1rem,env(safe-area-inset-bottom))] backdrop-blur-2xl"
          >
            <button
              type="button"
              aria-label="关闭菜单"
              className="absolute right-4 top-[max(1rem,env(safe-area-inset-top))] grid size-10 place-items-center rounded-2xl hover:bg-black/5 dark:hover:bg-white/5"
              onClick={() => setMobileOpen(false)}
            >
              <X className="size-5" />
            </button>
            <SidebarContent session={session} pathname={pathname} onNavigate={() => setMobileOpen(false)} />
          </aside>
        </div>
      ) : null}

      <div className="relative flex min-h-screen flex-col lg:pl-80">
        <header className="sticky top-0 z-30 px-4 py-4 md:px-6 lg:px-8">
          <div className="mx-auto flex h-16 w-full max-w-[1600px] items-center justify-between gap-3 glass-panel rounded-[2rem] px-4 sm:px-6">
            <div className="flex min-w-0 items-center gap-3 sm:gap-4">
              {session.role === "admin" ? (
                <button
                  type="button"
                  aria-label="打开菜单"
                  className="grid size-10 place-items-center rounded-xl bg-black/5 hover:bg-black/10 dark:bg-white/5 dark:hover:bg-white/10 lg:hidden"
                  onClick={() => setMobileOpen(true)}
                >
                  <Menu className="size-5" />
                </button>
              ) : null}
              <div className="min-w-0">
                <h1 className="truncate text-lg font-bold tracking-tight text-foreground md:text-xl">
                  {meta.title}
                </h1>
              </div>
            </div>
            <div className="flex shrink-0 items-center gap-2 sm:gap-3">
              <button
                type="button"
                aria-label="退出登录"
                className="flex h-10 items-center gap-2 rounded-2xl bg-foreground/5 px-3 text-sm font-semibold transition-all hover:bg-destructive/10 hover:text-destructive sm:px-4"
                onClick={() => void handleLogout()}
              >
                <LogOut className="size-4" />
                <span className="hidden sm:inline">退出</span>
              </button>
            </div>
          </div>
        </header>
        <div
          className={cn(
            "min-w-0 flex-1 px-4 md:px-6 lg:px-8",
            isImageWorkspace
              ? "pt-3 pb-0 lg:pt-5 lg:pb-5"
              : session.role === "user"
                ? "pt-4 pb-[calc(3.75rem+env(safe-area-inset-bottom))] lg:pb-8"
                : "pt-4 pb-8",
          )}
        >
          <div className="mx-auto max-w-[1600px]">
            {children}
          </div>
        </div>
        <button
          type="button"
          aria-label="回到顶部"
          className="fixed bottom-8 right-8 hidden size-12 place-items-center rounded-2xl glass-panel text-foreground/40 transition-all hover:scale-110 hover:text-foreground lg:grid"
          onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })}
        >
          <ChevronLeft className="size-5 rotate-90" />
        </button>
        {session.role === "user" ? <MobileUserBottomNavigation pathname={pathname} /> : null}
      </div>
    </main>
  );
}
