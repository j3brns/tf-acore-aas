/* eslint-disable react-refresh/only-export-components */
import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { Info, CheckCircle2, AlertTriangle, AlertCircle, X } from "lucide-react";
import { Button } from "./ui/button";
import { cn } from "../lib/utils";

type NotificationSeverity = "info" | "success" | "warning" | "error";

type NotificationItem = {
  id: string;
  title: string;
  message: string;
  severity: NotificationSeverity;
};

type NotificationInput = Omit<NotificationItem, "id">;

type NotificationContextValue = {
  notifications: NotificationItem[];
  notify: (notification: NotificationInput) => string;
  dismiss: (id: string) => void;
};

const NotificationContext = createContext<NotificationContextValue | undefined>(undefined);

const notificationConfig: Record<NotificationSeverity, { icon: any; className: string }> = {
  info: {
    icon: Info,
    className: "border-primary/20 bg-slate-900/90 text-primary-foreground",
  },
  success: {
    icon: CheckCircle2,
    className: "border-success/20 bg-slate-900/90 text-success",
  },
  warning: {
    icon: AlertTriangle,
    className: "border-warning/20 bg-slate-900/90 text-warning",
  },
  error: {
    icon: AlertCircle,
    className: "border-destructive/20 bg-slate-900/90 text-destructive",
  },
};

type NotificationProviderProps = {
  children: ReactNode;
};

export function NotificationProvider({ children }: NotificationProviderProps) {
  const [notifications, setNotifications] = useState<NotificationItem[]>([]);

  const dismiss = useCallback((id: string) => {
    setNotifications((current) => current.filter((notification) => notification.id !== id));
  }, []);

  const notify = useCallback((notification: NotificationInput) => {
    const id = `toast-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    setNotifications((current) => [...current, { id, ...notification }]);
    
    // Auto-dismiss after 5 seconds
    setTimeout(() => dismiss(id), 5000);
    
    return id;
  }, [dismiss]);

  const value = useMemo(
    () => ({
      notifications,
      notify,
      dismiss,
    }),
    [dismiss, notifications, notify],
  );

  return (
    <NotificationContext.Provider value={value}>
      {children}
      <NotificationViewport notifications={notifications} onDismiss={dismiss} />
    </NotificationContext.Provider>
  );
}

export function useNotifications(): NotificationContextValue {
  const context = useContext(NotificationContext);
  if (!context) {
    throw new Error("useNotifications must be used within a NotificationProvider");
  }
  return context;
}

type NotificationViewportProps = {
  notifications: NotificationItem[];
  onDismiss: (id: string) => void;
};

function NotificationViewport({ notifications, onDismiss }: NotificationViewportProps) {
  return (
    <div
      aria-live="polite"
      aria-relevant="additions"
      className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-full max-w-md flex-col gap-3 px-4 sm:bottom-8 sm:right-8"
    >
      {notifications.map((notification) => {
        const { icon: Icon, className } = notificationConfig[notification.severity];
        return (
          <article
            key={notification.id}
            className={cn(
              "pointer-events-auto flex items-start gap-4 rounded-2xl border p-4 shadow-2xl backdrop-blur-xl animate-in slide-in-from-right-8 fade-in duration-300",
              className
            )}
            role="status"
          >
            <Icon className="h-5 w-5 shrink-0 mt-0.5" />
            <div className="flex-1 min-w-0">
              <p className="text-sm font-bold leading-tight">{notification.title}</p>
              <p className="mt-1 text-xs font-medium opacity-80 leading-normal">{notification.message}</p>
            </div>
            <Button
              variant="ghost"
              size="icon"
              onClick={() => onDismiss(notification.id)}
              className="h-6 w-6 rounded-lg hover:bg-white/10 -mt-1 -mr-1"
              aria-label="Dismiss notification"
            >
              <X className="h-3 w-3" />
            </Button>
          </article>
        );
      })}
    </div>
  );
}
