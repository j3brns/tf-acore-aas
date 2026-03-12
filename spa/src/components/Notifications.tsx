/* eslint-disable react-refresh/only-export-components */
import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";

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

const notificationStyles: Record<NotificationSeverity, string> = {
  info: "border-sky-200 bg-sky-50 text-sky-900",
  success: "border-emerald-200 bg-emerald-50 text-emerald-900",
  warning: "border-amber-200 bg-amber-50 text-amber-900",
  error: "border-rose-200 bg-rose-50 text-rose-900",
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
    return id;
  }, []);

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
      className="pointer-events-none fixed inset-x-0 top-4 z-50 mx-auto flex w-full max-w-5xl flex-col gap-3 px-4"
    >
      {notifications.map((notification) => (
        <article
          key={notification.id}
          className={`pointer-events-auto ml-auto w-full max-w-sm rounded-3xl border px-5 py-4 shadow-lg backdrop-blur ${notificationStyles[notification.severity]}`}
          role="status"
        >
          <div className="flex items-start justify-between gap-4">
            <div>
              <p className="text-sm font-semibold">{notification.title}</p>
              <p className="mt-1 text-sm">{notification.message}</p>
            </div>
            <button
              type="button"
              onClick={() => onDismiss(notification.id)}
              className="rounded-full border border-current/20 px-2 py-1 text-xs font-semibold"
            >
              Dismiss
            </button>
          </div>
        </article>
      ))}
    </div>
  );
}
