import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export function DataPanel({
  title,
  description,
  toolbar,
  children,
  className,
}: {
  title?: string;
  description?: string;
  toolbar?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={cn("apple-card flex flex-col", className)}>
      {(title || description || toolbar) ? (
        <div className="flex flex-col gap-3 px-6 py-5 md:flex-row md:items-center md:justify-between">
          <div>
            {title ? <h2 className="text-base font-bold text-foreground">{title}</h2> : null}
            {description ? <p className="mt-0.5 text-xs font-medium text-muted-foreground">{description}</p> : null}
          </div>
          {toolbar ? <div className="flex flex-wrap gap-2">{toolbar}</div> : null}
        </div>
      ) : null}
      <div className="flex-1">{children}</div>
    </section>
  );
}
