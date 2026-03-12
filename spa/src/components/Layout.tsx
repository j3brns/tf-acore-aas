import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link, NavLink, useLocation } from "react-router-dom";
import { useAuth } from "../auth/useAuth";
import { getApiClient } from "../api/client";
import { getIdentityContext } from "../auth/identity";
import { PageBanner } from "./PageBanner";

type LayoutProps = {
  children: ReactNode;
};

type NavigationItem = {
  label: string;
  path: string;
  description: string;
  requiresTenant?: boolean;
  requiresOperator?: boolean;
};

type NavigationGroup = {
  label: string;
  items: NavigationItem[];
};

const navigationGroups: NavigationGroup[] = [
  {
    label: "Workspace",
    items: [
      {
        label: "Agents",
        path: "/agents",
        description: "Browse and invoke platform agents.",
      },
      {
        label: "Sessions",
        path: "/sessions",
        description: "Review current and recent activity.",
      },
    ],
  },
  {
    label: "Tenant",
    items: [
      {
        label: "Overview",
        path: "/tenant/overview",
        description: "Usage, health, and tenant posture.",
        requiresTenant: true,
      },
      {
        label: "API Keys",
        path: "/tenant/api-keys",
        description: "Rotate and inspect machine credentials.",
        requiresTenant: true,
      },
      {
        label: "Access",
        path: "/tenant/access",
        description: "Invite and manage tenant users.",
        requiresTenant: true,
      },
      {
        label: "Webhooks",
        path: "/tenant/webhooks",
        description: "Configure async job callbacks.",
        requiresTenant: true,
      },
      {
        label: "Audit Exports",
        path: "/tenant/audit",
        description: "Export invocation logs to S3.",
        requiresTenant: true,
      },
      {
        label: "Settings",
        path: "/tenant/settings",
        description: "Manage tenant profile.",
        requiresTenant: true,
      },
    ],
  },
  {
    label: "Operations",
    items: [
      {
        label: "Overview",
        path: "/operations/overview",
        description: "Platform status and operator controls.",
        requiresOperator: true,
      },
      {
        label: "Tenants",
        path: "/operations/tenants",
        description: "Cross-tenant portfolio view.",
        requiresOperator: true,
      },
      {
        label: "Quota",
        path: "/operations/quota",
        description: "Runtime utilisation and headroom.",
        requiresOperator: true,
      },
    ],
  },
];

export function Layout({ children }: LayoutProps) {
  const { account, logout, isAuthenticated, getAccessToken } = useAuth();
  const location = useLocation();
  const [mobileNavOpen, setMobileNavOpen] = useState(false);

  useEffect(() => {
    if (isAuthenticated) {
      getApiClient(getAccessToken);
    }
  }, [getAccessToken, isAuthenticated]);

  useEffect(() => {
    if (!mobileNavOpen) {
      return;
    }

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setMobileNavOpen(false);
      }
    };

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [mobileNavOpen]);

  const identity = getIdentityContext(account);
  const environment = import.meta.env.VITE_ENVIRONMENT_NAME ?? import.meta.env.MODE;

  const availableNavigation = useMemo(
    () =>
      navigationGroups
        .map((group) => ({
          ...group,
          items: group.items.filter((item) => {
            if (item.requiresOperator && !identity.isOperator) {
              return false;
            }
            if (item.requiresTenant && !identity.tenantId) {
              return false;
            }
            return true;
          }),
        }))
        .filter((group) => group.items.length > 0),
    [identity.isOperator, identity.tenantId],
  );

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <div className="fixed inset-0 -z-10 bg-[radial-gradient(circle_at_top_left,_rgba(34,197,94,0.22),_transparent_26%),radial-gradient(circle_at_top_right,_rgba(56,189,248,0.20),_transparent_24%),linear-gradient(180deg,_#08111f_0%,_#0f172a_45%,_#e2e8f0_45%,_#f8fafc_100%)]" />
      <header className="border-b border-white/10 bg-slate-950/80 backdrop-blur">
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-4 py-4 sm:px-6 lg:px-8">
          <div className="flex items-center gap-3">
            <button
              type="button"
              aria-controls="mobile-navigation"
              aria-expanded={mobileNavOpen}
              aria-label="Open navigation"
              onClick={() => setMobileNavOpen((current) => !current)}
              className="inline-flex h-11 w-11 items-center justify-center rounded-2xl border border-white/10 bg-white/5 text-sm font-semibold text-white md:hidden"
            >
              Menu
            </button>
            <Link to={identity.isOperator ? "/operations/overview" : "/agents"} className="space-y-1">
              <p className="text-xs font-semibold uppercase tracking-[0.3em] text-cyan-300">Agent Platform</p>
              <p className="text-lg font-semibold text-white">Production Shell</p>
            </Link>
          </div>
          <div className="hidden flex-wrap items-center justify-end gap-2 md:flex">
            <ShellChip label="Env" value={environment.toUpperCase()} />
            <ShellChip label="Tenant" value={identity.tenantId ?? "Unavailable"} />
            <ShellChip label="Role" value={identity.roleLabel} />
          </div>
          <div className="flex items-center gap-3">
            <div className="hidden text-right md:block">
              <p className="text-sm font-medium text-white">{identity.displayName}</p>
              <p className="text-xs text-slate-400">{identity.username}</p>
            </div>
            {isAuthenticated && (
              <button
                type="button"
                onClick={logout}
                className="rounded-2xl border border-cyan-400/30 bg-cyan-400/10 px-4 py-2 text-sm font-semibold text-cyan-100 transition hover:border-cyan-300 hover:bg-cyan-400/20"
              >
                Logout
              </button>
            )}
          </div>
        </div>
      </header>

      {mobileNavOpen && (
        <div className="fixed inset-0 z-40 bg-slate-950/65 backdrop-blur-sm md:hidden" onClick={() => setMobileNavOpen(false)}>
          <div
            id="mobile-navigation"
            role="dialog"
            aria-modal="true"
            aria-label="Primary navigation"
            className="h-full w-[min(20rem,85vw)] border-r border-white/10 bg-slate-950 px-4 py-6"
            onClick={(event) => event.stopPropagation()}
          >
            <NavigationContent
              groups={availableNavigation}
              currentPath={location.pathname}
              onNavigate={() => setMobileNavOpen(false)}
            />
          </div>
        </div>
      )}

      <div className="mx-auto flex max-w-7xl gap-8 px-4 pb-10 pt-6 sm:px-6 lg:px-8">
        <aside className="sticky top-6 hidden h-fit w-80 shrink-0 rounded-[2rem] border border-white/10 bg-slate-950/70 p-5 shadow-2xl backdrop-blur md:block">
          <NavigationContent groups={availableNavigation} currentPath={location.pathname} />
        </aside>
        <main className="min-w-0 flex-1 space-y-6">
          <section className="grid gap-4 lg:grid-cols-[1.5fr_1fr]">
            <div className="rounded-[2rem] border border-white/10 bg-slate-950/70 p-6 shadow-2xl backdrop-blur">
              <p className="text-xs font-semibold uppercase tracking-[0.3em] text-cyan-300">Current Context</p>
              <h1 className="mt-3 text-3xl font-semibold text-white">Tenant and operator journeys share one shell.</h1>
              <p className="mt-3 max-w-2xl text-sm text-slate-300">
                Navigation, route gating, and shell context stay explicit so tenant-scoped work and operator controls do not blur together.
              </p>
            </div>
            <div className="grid gap-3 rounded-[2rem] border border-white/10 bg-white/80 p-6 text-slate-900 shadow-xl">
              <ShellMetric label="Tenant Context" value={identity.tenantId ?? "Missing"} tone={identity.tenantId ? "default" : "warning"} />
              <ShellMetric label="Role Surface" value={identity.roleLabel} tone={identity.isOperator ? "success" : "default"} />
              <ShellMetric label="Route" value={location.pathname} tone="default" mono />
            </div>
          </section>

          {!identity.tenantId && (
            <PageBanner title="Tenant Context Missing" severity="warning">
              Tenant routes are disabled because the current token does not include a `tenantid` claim.
            </PageBanner>
          )}

          {identity.isOperator && (
            <PageBanner title="Operator Access Active" severity="info">
              Platform-wide routes are visible because the current session includes `Platform.Admin` or `Platform.Operator`.
            </PageBanner>
          )}

          <div className="rounded-[2rem] bg-white/90 p-4 shadow-xl ring-1 ring-slate-200 sm:p-6">
            {children}
          </div>
        </main>
      </div>
    </div>
  );
}

function NavigationContent({
  groups,
  currentPath,
  onNavigate,
}: {
  groups: NavigationGroup[];
  currentPath: string;
  onNavigate?: () => void;
}) {
  return (
    <nav className="space-y-6">
      {groups.map((group) => (
        <section key={group.label}>
          <p className="px-3 text-xs font-semibold uppercase tracking-[0.25em] text-slate-400">{group.label}</p>
          <div className="mt-3 space-y-2">
            {group.items.map((item) => (
              <NavLink
                key={item.path}
                to={item.path}
                onClick={onNavigate}
                className={({ isActive }) =>
                  [
                    "block rounded-2xl border px-4 py-3 transition",
                    isActive || currentPath === item.path
                      ? "border-cyan-300/40 bg-cyan-300/10 text-white shadow-lg"
                      : "border-white/10 bg-white/5 text-slate-300 hover:border-white/20 hover:bg-white/10 hover:text-white",
                  ].join(" ")
                }
              >
                <p className="text-sm font-semibold">{item.label}</p>
                <p className="mt-1 text-xs opacity-80">{item.description}</p>
              </NavLink>
            ))}
          </div>
        </section>
      ))}
    </nav>
  );
}

function ShellChip({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-full border border-white/10 bg-white/5 px-3 py-2 text-xs font-semibold uppercase tracking-[0.2em] text-slate-200">
      <span className="text-slate-400">{label}</span> {value}
    </div>
  );
}

function ShellMetric({
  label,
  value,
  tone,
  mono = false,
}: {
  label: string;
  value: string;
  tone: "default" | "success" | "warning";
  mono?: boolean;
}) {
  const toneStyles = {
    default: "bg-slate-100 text-slate-900",
    success: "bg-emerald-50 text-emerald-900",
    warning: "bg-amber-50 text-amber-900",
  };

  return (
    <div className={`rounded-2xl px-4 py-3 ${toneStyles[tone]}`}>
      <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">{label}</p>
      <p className={`mt-1 text-sm font-semibold ${mono ? "font-mono" : ""}`}>{value}</p>
    </div>
  );
}
