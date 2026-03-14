import type { ReactNode } from "react";
import { Info, CheckCircle2, AlertTriangle, AlertCircle } from "lucide-react";
import { cn } from "../lib/utils";

type BannerSeverity = "info" | "success" | "warning" | "error";

const severityConfig: Record<BannerSeverity, { icon: any; className: string }> = {
  info: {
    icon: Info,
    className: "border-primary/20 bg-primary/5 text-primary-foreground",
  },
  success: {
    icon: CheckCircle2,
    className: "border-success/20 bg-success/5 text-success",
  },
  warning: {
    icon: AlertTriangle,
    className: "border-warning/20 bg-warning/5 text-warning",
  },
  error: {
    icon: AlertCircle,
    className: "border-destructive/20 bg-destructive/5 text-destructive",
  },
};

type PageBannerProps = {
  title: string;
  severity?: BannerSeverity;
  children: ReactNode;
  className?: string;
};

export function PageBanner({ title, severity = "info", children, className }: PageBannerProps) {
  const { icon: Icon, className: severityClass } = severityConfig[severity];
  
  return (
    <section
      aria-live={severity === "error" || severity === "warning" ? "assertive" : "polite"}
      className={cn(
        "flex gap-4 rounded-2xl border p-5 shadow-sm backdrop-blur-sm",
        severityClass,
        className
      )}
    >
      <Icon className="h-5 w-5 shrink-0 mt-0.5" />
      <div className="flex-1">
        <p className="text-xs font-bold uppercase tracking-[0.15em] mb-1">{title}</p>
        <div className="text-sm font-medium opacity-90 leading-relaxed">{children}</div>
      </div>
    </section>
  );
}
