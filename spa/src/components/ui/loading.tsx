import { Loader2 } from "lucide-react";
import { cn } from "../../lib/utils";

interface LoadingProps {
  message?: string;
  className?: string;
  size?: "sm" | "md" | "lg";
}

export function Loading({ message = "Loading...", className, size = "md" }: LoadingProps) {
  const sizeClasses = {
    sm: "h-4 w-4",
    md: "h-8 w-8",
    lg: "h-12 w-12",
  };

  return (
    <div className={cn("flex flex-col items-center justify-center gap-4 p-8", className)}>
      <Loader2 className={cn("animate-spin text-primary", sizeClasses[size])} />
      <p className="text-sm font-medium text-muted-foreground animate-pulse">{message}</p>
    </div>
  );
}
