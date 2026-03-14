import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link, NavLink, useLocation } from "react-router-dom";
import { useAuth } from "../auth/useAuth";
import { getApiClient } from "../api/client";
import { getIdentityContext } from "../auth/identity";
import { PageBanner } from "./PageBanner";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { StatusIndicator } from "./ui/status-indicator";
import { Menu, X, Shield, Globe, LogOut } from "lucide-react";
import { cn } from "../lib/utils";

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
      {
        label: "Jobs",
        path: "/jobs",
        description: "Track long-running async tasks.",
      },
    ],
  },
  {
    label: "Tenant Settings",
    items: [
      {
        label: "Overview",
        path: "/tenant/overview",
        description: "Usage, health, and tenant posture.",
        requiresTenant: true,
      },
      {
        label: "Access Control",
        path: "/tenant/access",
        description: "Invite and manage tenant users.",
        requiresTenant: true,
      },
      {
        label: "API Keys",
        path: "/tenant/api-keys",
        description: "Rotate and inspect machine credentials.",
        requiresTenant: true,
      },
      {
        label: "Webhooks",
        path: "/tenant/webhooks",
        description: "Configure async result delivery.",
        requiresTenant: true,
      },
    ],
  },
  {
    label: "Operator Console",
    items: [
      {
        label: "Platform Health",
        path: "/operations/overview",
        description: "Global status and operator controls.",
        requiresOperator: true,
      },
      {
        label: "Tenant Portfolio",
        path: "/operations/tenants",
        description: "Cross-tenant oversight and actions.",
        requiresOperator: true,
      },
      {
        label: "Infrastructure",
        path: "/operations/quota",
        description: "Runtime utilisation and regional state.",
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
    if (!mobileNavOpen) return;

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMobileNavOpen(false);
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
            if (item.requiresOperator && !identity.isOperator) return false;
            if (item.requiresTenant && !identity.tenantId) return false;
            return true;
          }),
        }))
        .filter((group) => group.items.length > 0),
    [identity.isOperator, identity.tenantId],
  );

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 selection:bg-cyan-500/30">
      <div className="fixed inset-0 -z-10 bg-[radial-gradient(circle_at_top_left,_rgba(34,197,94,0.15),_transparent_30%),radial-gradient(circle_at_top_right,_rgba(56,189,248,0.15),_transparent_30%),linear-gradient(180deg,_#020617_0%,_#0f172a_100%)]" />
      
      <header className="sticky top-0 z-30 border-b border-white/10 bg-slate-950/80 backdrop-blur-md">
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-4 py-3 sm:px-6 lg:px-8">
          <div className="flex items-center gap-4">
            <Button
              variant="ghost"
              size="icon"
              aria-controls="mobile-navigation"
              aria-expanded={mobileNavOpen}
              aria-label={mobileNavOpen ? "Close navigation" : "Open navigation"}
              onClick={() => setMobileNavOpen((current) => !current)}
              className="md:hidden"
            >
              {mobileNavOpen ? <X /> : <Menu />}
            </Button>
            
            <Link to={identity.isOperator ? "/operations/overview" : "/agents"} className="group flex items-center gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-cyan-500 to-blue-600 shadow-lg shadow-cyan-500/20 group-hover:scale-105 transition-transform">
                <Globe className="h-6 w-6 text-white" />
              </div>
              <div className="hidden sm:block">
                <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-cyan-400">LoopaaS Platform</p>
                <p className="text-sm font-bold text-white">Agent Portal</p>
              </div>
            </Link>
          </div>

          <div className="hidden md:flex items-center gap-3">
            <StatusIndicator label="Env" value={environment} tone={environment === "prod" ? "info" : "neutral"} />
            <StatusIndicator 
              label="Region" 
              value="eu-west-2" 
              tone="success" 
              title="Primary control plane region: London"
            />
            <StatusIndicator 
              label="Data" 
              value="EU-Only" 
              tone="success" 
              title="All data remains within the European Union"
            />
          </div>

          <div className="flex items-center gap-4">
            <div className="hidden lg:block text-right">
              <p className="text-sm font-semibold text-white">{identity.displayName}</p>
              <Badge variant="secondary" className="mt-0.5 text-[10px] h-4">
                {identity.roleLabel}
              </Badge>
            </div>
            
            {isAuthenticated && (
              <Button variant="outline" size="sm" onClick={logout} className="rounded-full gap-2 border-white/10 hover:bg-white/5">
                <LogOut className="h-4 w-4" />
                <span className="hidden sm:inline">Sign Out</span>
              </Button>
            )}
          </div>
        </div>
      </header>

      {/* Mobile Navigation Drawer */}
      {mobileNavOpen && (
        <div 
          className="fixed inset-0 z-40 bg-slate-950/80 backdrop-blur-sm md:hidden" 
          onClick={() => setMobileNavOpen(false)}
          aria-hidden="true"
        />
      )}
      
      <div
        id="mobile-navigation"
        className={cn(
          "fixed inset-y-0 left-0 z-50 w-80 transform border-r border-white/10 bg-slate-950 px-6 py-8 transition-transform duration-300 ease-in-out md:hidden",
          mobileNavOpen ? "translate-x-0" : "-translate-x-full"
        )}
      >
        <div className="flex items-center justify-between mb-8">
          <div className="flex items-center gap-3">
             <Globe className="h-6 w-6 text-cyan-400" />
             <span className="font-bold">LoopaaS</span>
          </div>
          <Button variant="ghost" size="icon" onClick={() => setMobileNavOpen(false)}>
            <X />
          </Button>
        </div>
        <NavigationContent
          groups={availableNavigation}
          currentPath={location.pathname}
          onNavigate={() => setMobileNavOpen(false)}
        />
      </div>

      <div className="mx-auto flex max-w-7xl gap-8 px-4 pb-12 pt-8 sm:px-6 lg:px-8">
        <aside className="sticky top-24 hidden h-[calc(100vh-8rem)] w-72 shrink-0 overflow-y-auto rounded-3xl border border-white/10 bg-slate-900/50 p-6 backdrop-blur-xl md:block custom-scrollbar">
          <NavigationContent groups={availableNavigation} currentPath={location.pathname} />
          
          <div className="mt-10 pt-6 border-t border-white/5">
            <div className="flex items-center gap-2 text-xs font-semibold text-slate-500 uppercase tracking-widest mb-4">
              <Shield className="h-3 w-3" />
              Security Posture
            </div>
            <div className="space-y-3">
              <div className="flex items-center justify-between text-xs">
                <span className="text-slate-400">Tenant ID</span>
                <span className="font-mono text-cyan-400">{identity.tenantId?.slice(0, 8) ?? "None"}</span>
              </div>
              <div className="flex items-center justify-between text-xs">
                <span className="text-slate-400">Compliance</span>
                <span className="text-emerald-400">SOC2 Ready</span>
              </div>
            </div>
          </div>
        </aside>

        <main className="min-w-0 flex-1 space-y-8">
          <header className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <nav className="flex items-center gap-2 text-xs font-medium text-slate-500 mb-2" aria-label="Breadcrumb">
                <Link to="/" className="hover:text-white transition-colors">Portal</Link>
                <span>/</span>
                <span className="text-slate-300 capitalize">{location.pathname.split("/")[1] || "Dashboard"}</span>
              </nav>
              <h1 className="text-2xl font-bold tracking-tight text-white sm:text-3xl">
                {getPageTitle(location.pathname)}
              </h1>
            </div>
            
            <div className="flex items-center gap-2">
               {identity.isOperator && (
                 <Badge variant="outline" className="border-emerald-500/30 bg-emerald-500/10 text-emerald-400">
                    Operator Mode
                 </Badge>
               )}
            </div>
          </header>

          {!identity.tenantId && !identity.isOperator && (
            <PageBanner title="Incomplete Identity" severity="warning">
              Your session is active but missing a required <code>tenantid</code> claim. 
              Some features will be unavailable until your account is correctly provisioned.
            </PageBanner>
          )}

          <div className="rounded-3xl bg-slate-900/40 border border-white/5 p-6 shadow-2xl ring-1 ring-white/10 backdrop-blur-sm sm:p-8">
            {children}
          </div>
          
          <footer className="mt-12 flex flex-col gap-4 border-t border-white/5 pt-8 sm:flex-row sm:items-center sm:justify-between text-xs text-slate-500 font-medium">
             <p>© 2026 LoopaaS Platform. All data remains in the EU.</p>
             <div className="flex gap-6">
                <a href="/help" className="hover:text-white transition-colors">Documentation</a>
                <a href="/support" className="hover:text-white transition-colors">Support</a>
                <a href="/privacy" className="hover:text-white transition-colors">Privacy Policy</a>
             </div>
          </footer>
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
    <nav className="space-y-8">
      {groups.map((group) => (
        <section key={group.label}>
          <p className="px-3 text-[10px] font-bold uppercase tracking-[0.2em] text-slate-500">{group.label}</p>
          <div className="mt-4 space-y-1">
            {group.items.map((item) => {
              const isActive = currentPath === item.path || currentPath.startsWith(item.path + "/");
              return (
                <NavLink
                  key={item.path}
                  to={item.path}
                  onClick={onNavigate}
                  className={cn(
                    "group flex flex-col rounded-xl px-4 py-3 transition-all duration-200",
                    isActive
                      ? "bg-gradient-to-r from-cyan-500/10 to-blue-500/5 border border-cyan-500/20 text-white shadow-lg shadow-cyan-500/5"
                      : "text-slate-400 hover:text-white hover:bg-white/5 border border-transparent"
                  )}
                >
                  <p className="text-sm font-semibold">{item.label}</p>
                  <p className="mt-1 text-[11px] font-medium opacity-60 group-hover:opacity-100 transition-opacity">
                    {item.description}
                  </p>
                </NavLink>
              );
            })}
          </div>
        </section>
      ))}
    </nav>
  );
}

function getPageTitle(path: string): string {
  const parts = path.split("/").filter(Boolean);
  if (parts.length === 0) return "Platform Dashboard";
  
  const lastPart = parts[parts.length - 1];
  return lastPart.charAt(0).toUpperCase() + lastPart.slice(1).replace(/-/g, " ");
}
