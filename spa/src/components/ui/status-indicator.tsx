import * as React from "react"
import { cn } from "../../lib/utils"

export type StatusTone = "success" | "warning" | "error" | "info" | "neutral";

interface StatusIndicatorProps extends React.HTMLAttributes<HTMLDivElement> {
  tone?: StatusTone;
  label: string;
  value: string;
  pulse?: boolean;
}

const toneStyles: Record<StatusTone, string> = {
  success: "bg-success/10 text-success border-success/20",
  warning: "bg-warning/10 text-warning border-warning/20",
  error: "bg-destructive/10 text-destructive border-destructive/20",
  info: "bg-primary/10 text-primary border-primary/20",
  neutral: "bg-muted text-muted-foreground border-border",
};

const dotStyles: Record<StatusTone, string> = {
  success: "bg-success",
  warning: "bg-warning",
  error: "bg-destructive",
  info: "bg-primary",
  neutral: "bg-muted-foreground",
};

export function StatusIndicator({
  tone = "neutral",
  label,
  value,
  pulse = false,
  className,
  ...props
}: StatusIndicatorProps) {
  return (
    <div
      className={cn(
        "flex items-center gap-2 rounded-lg border px-2.5 py-1 text-[10px] font-bold uppercase tracking-wider",
        toneStyles[tone],
        className
      )}
      {...props}
    >
      <span className="opacity-70">{label}:</span>
      <span className="flex items-center gap-1.5">
        <span
          className={cn(
            "h-1.5 w-1.5 rounded-full",
            dotStyles[tone],
            pulse && "animate-pulse"
          )}
        />
        {value}
      </span>
    </div>
  );
}
