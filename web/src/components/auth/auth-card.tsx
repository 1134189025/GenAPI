import type { ComponentProps, ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import { AlertTriangle, Sparkles } from "lucide-react";

import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

type AuthTone = "teal" | "emerald" | "amber";

const toneStyles: Record<
  AuthTone,
  {
    logo: string;
    eyebrow: string;
    icon: string;
    ring: string;
  }
> = {
  teal: {
    logo: "from-teal-400 via-cyan-500 to-sky-600 shadow-[0_22px_48px_-22px_rgba(14,165,233,0.9)]",
    eyebrow: "text-teal-700",
    icon: "text-teal-500",
    ring: "focus-visible:border-teal-300 focus-visible:ring-teal-100",
  },
  emerald: {
    logo: "from-emerald-400 via-teal-500 to-emerald-700 shadow-[0_22px_48px_-22px_rgba(16,185,129,0.9)]",
    eyebrow: "text-emerald-700",
    icon: "text-emerald-500",
    ring: "focus-visible:border-emerald-300 focus-visible:ring-emerald-100",
  },
  amber: {
    logo: "from-amber-300 via-orange-400 to-amber-600 shadow-[0_22px_48px_-22px_rgba(245,158,11,0.75)]",
    eyebrow: "text-amber-700",
    icon: "text-amber-500",
    ring: "focus-visible:border-amber-300 focus-visible:ring-amber-100",
  },
};

export function AuthShell({
  title,
  subtitle,
  children,
  brandName = "Genapi",
  eyebrow,
  footer,
  icon: Icon = Sparkles,
  maxWidth = "max-w-[520px]",
  tone = "teal",
}: {
  title: string;
  subtitle: string;
  children: ReactNode;
  brandName?: string;
  eyebrow?: string;
  footer?: ReactNode;
  icon?: LucideIcon;
  maxWidth?: string;
  tone?: AuthTone;
}) {
  const styles = toneStyles[tone];

  return (
    <div className="relative isolate min-h-dvh w-full overflow-y-auto overflow-x-hidden overscroll-contain px-4 pt-[max(1.5rem,env(safe-area-inset-top))] pb-[max(1.5rem,env(safe-area-inset-bottom))] text-slate-950 sm:pt-[max(2rem,env(safe-area-inset-top))] sm:pb-[max(2rem,env(safe-area-inset-bottom))]">
      <div className="absolute inset-0 -z-20 bg-[radial-gradient(circle_at_12%_12%,rgba(20,184,166,0.20),transparent_28%),radial-gradient(circle_at_86%_8%,rgba(14,165,233,0.16),transparent_27%),radial-gradient(circle_at_52%_78%,rgba(16,185,129,0.14),transparent_30%),linear-gradient(135deg,rgba(248,250,252,0.99),rgba(240,253,250,0.92)_45%,rgba(226,232,240,0.98))]" />
      <div className="absolute inset-0 -z-10 bg-[linear-gradient(rgba(15,23,42,0.045)_1px,transparent_1px),linear-gradient(90deg,rgba(15,23,42,0.045)_1px,transparent_1px)] bg-[size:56px_56px] [mask-image:radial-gradient(ellipse_at_center,black_36%,transparent_78%)]" />
      <div className="pointer-events-none absolute -left-24 top-20 -z-10 h-72 w-72 rounded-full bg-teal-300/20 blur-3xl" />
      <div className="pointer-events-none absolute -right-28 bottom-16 -z-10 h-80 w-80 rounded-full bg-sky-300/20 blur-3xl" />
      <div className="pointer-events-none absolute left-1/2 top-1/2 -z-10 h-64 w-64 -translate-x-1/2 -translate-y-1/2 rounded-full border border-white/60 bg-white/20 shadow-[inset_0_0_80px_rgba(255,255,255,0.35)]" />

      <div className="relative z-10 mx-auto flex min-h-[calc(100dvh_-_3rem)] w-full items-start justify-center sm:items-center">
        <div className={cn("w-full", maxWidth)}>
          <div className="mb-6 text-center">
            <div
              className={cn(
                "mx-auto mb-4 grid size-16 place-items-center rounded-[1.35rem] bg-gradient-to-br text-white shadow-xl ring-1 ring-white/80",
                styles.logo,
              )}
            >
              <Icon className="size-7" />
            </div>
            <div className={cn("text-xs font-black uppercase tracking-[0.28em]", styles.eyebrow)}>
              {eyebrow || brandName}
            </div>
            <h1 className="mt-2 text-3xl font-black tracking-tight text-slate-950 sm:text-4xl">{title}</h1>
            <p className="mx-auto mt-3 max-w-[34rem] text-sm leading-6 text-slate-500">{subtitle}</p>
          </div>

          <Card className="overflow-hidden rounded-[2rem] border-white/75 bg-white/85 shadow-[0_32px_110px_-44px_rgba(15,23,42,0.48)] backdrop-blur-2xl">
            <CardContent className="p-5 sm:p-7">{children}</CardContent>
          </Card>

          {footer ? (
            <div className="mt-5 rounded-[1.35rem] border border-white/60 bg-white/60 px-4 py-3 text-sm text-slate-500 shadow-[0_18px_56px_-42px_rgba(15,23,42,0.4)] backdrop-blur-xl">
              {footer}
            </div>
          ) : null}

          <div className="mt-6 text-center text-xs font-medium text-slate-400">
            API relay console for {brandName}
          </div>
        </div>
      </div>
    </div>
  );
}

type AuthFieldProps = Omit<ComponentProps<typeof Input>, "id" | "className"> & {
  id: string;
  label: string;
  icon: LucideIcon;
  hint?: ReactNode;
  inputClassName?: string;
  wrapperClassName?: string;
  tone?: AuthTone;
};

export function AuthField({
  id,
  label,
  icon: Icon,
  hint,
  inputClassName,
  wrapperClassName,
  tone = "teal",
  ...props
}: AuthFieldProps) {
  const hintId = hint ? `${id}-hint` : undefined;
  const styles = toneStyles[tone];

  return (
    <div className={cn("space-y-2", wrapperClassName)}>
      <label htmlFor={id} className="block text-sm font-bold text-slate-700">
        {label}
      </label>
      <div className="relative">
        <Icon className={cn("pointer-events-none absolute left-4 top-1/2 size-4 -translate-y-1/2", styles.icon)} />
        <Input
          {...props}
          id={id}
          aria-describedby={hintId || props["aria-describedby"]}
          className={cn(
            "h-12 rounded-2xl border-slate-200/80 bg-white/90 pl-11 text-slate-950 shadow-sm placeholder:text-slate-400",
            styles.ring,
            inputClassName,
          )}
        />
      </div>
      {hint ? (
        <p id={hintId} className="text-xs leading-5 text-slate-500">
          {hint}
        </p>
      ) : null}
    </div>
  );
}

export function AuthNotice({
  title,
  description,
  children,
  icon: Icon = AlertTriangle,
  tone = "teal",
}: {
  title: string;
  description: ReactNode;
  children?: ReactNode;
  icon?: LucideIcon;
  tone?: AuthTone;
}) {
  const styles = toneStyles[tone];

  return (
    <div className="text-center">
      <div className="mx-auto mb-4 grid size-12 place-items-center rounded-2xl bg-slate-50 ring-1 ring-slate-200/80">
        <Icon className={cn("size-5", styles.icon)} />
      </div>
      <h2 className="text-xl font-black tracking-tight text-slate-950">{title}</h2>
      <div className="mx-auto mt-2 max-w-sm text-sm leading-6 text-slate-500">{description}</div>
      {children ? <div className="mt-5 flex flex-col gap-2 sm:flex-row sm:justify-center">{children}</div> : null}
    </div>
  );
}
