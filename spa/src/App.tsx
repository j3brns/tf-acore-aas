import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { NotificationProvider } from "./components/Notifications";
import { PageBanner } from "./components/PageBanner";
import { AgentCataloguePage } from "./pages/AgentCataloguePage";
import { InvokePage } from "./pages/InvokePage";
import { SessionsPage } from "./pages/SessionsPage";
import { AdminPage } from "./pages/AdminPage";
import { TenantDashboardPage } from "./pages/TenantDashboardPage";
import { TenantApiKeysPage } from "./pages/TenantApiKeysPage";
import { TenantMembersPage } from "./pages/TenantMembersPage";
import { TenantWebhooksPage } from "./pages/TenantWebhooksPage";
import { TenantAuditPage } from "./pages/TenantAuditPage";
import { TenantSettingsPage } from "./pages/TenantSettingsPage";
import { useAuth } from "./auth/useAuth";
import { hasPlatformOperatorRole, resolveTenantId } from "./auth/identity";

function App() {
  const { isAuthenticated, login, isLoading } = useAuth();

  if (isLoading) {
    return (
      <div className="min-h-screen bg-gray-100 flex flex-col items-center justify-center p-4">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600"></div>
        <p className="mt-4 text-gray-600">Checking session...</p>
      </div>
    );
  }

  if (!isAuthenticated) {
    return <SignInScreen onLogin={login} />;
  }

  return (
    <BrowserRouter>
      <NotificationProvider>
        <Layout>
          <AppRoutes />
        </Layout>
      </NotificationProvider>
    </BrowserRouter>
  );
}

export function AppRoutes() {
  const { account } = useAuth();
  const claims = account?.idTokenClaims;
  const isOperator = hasPlatformOperatorRole(claims);
  const tenantId = resolveTenantId(claims);

  return (
    <Routes>
      <Route path="/" element={<Navigate to={isOperator ? "/operations/overview" : "/agents"} replace />} />
      <Route path="/agents" element={<AgentCataloguePage />} />
      <Route path="/invoke/:agentName" element={<InvokePage />} />
      <Route path="/sessions" element={<SessionsPage />} />
      
      <Route path="/tenant" element={<Navigate to="/tenant/overview" replace />} />
      
      <Route path="/tenant/overview" element={
        <RequireTenantContext tenantId={tenantId}>
          <TenantDashboardPage />
        </RequireTenantContext>
      } />
      
      <Route path="/tenant/api-keys" element={
        <RequireTenantContext tenantId={tenantId}>
          <TenantApiKeysPage />
        </RequireTenantContext>
      } />
      
      <Route path="/tenant/access" element={
        <RequireTenantContext tenantId={tenantId}>
          <TenantMembersPage />
        </RequireTenantContext>
      } />
      
      <Route path="/tenant/webhooks" element={
        <RequireTenantContext tenantId={tenantId}>
          <TenantWebhooksPage />
        </RequireTenantContext>
      } />

      <Route path="/tenant/audit" element={
        <RequireTenantContext tenantId={tenantId}>
          <TenantAuditPage />
        </RequireTenantContext>
      } />

      <Route path="/tenant/settings" element={
        <RequireTenantContext tenantId={tenantId}>
          <TenantSettingsPage />
        </RequireTenantContext>
      } />

      <Route path="/admin" element={<Navigate to="/operations/overview" replace />} />
      <Route
        path="/operations/overview"
        element={
          <RequireOperatorRoute isOperator={isOperator}>
            <AdminPage initialSection="overview" />
          </RequireOperatorRoute>
        }
      />
      <Route
        path="/operations/tenants"
        element={
          <RequireOperatorRoute isOperator={isOperator}>
            <AdminPage initialSection="tenants" />
          </RequireOperatorRoute>
        }
      />
      <Route
        path="/operations/quota"
        element={
          <RequireOperatorRoute isOperator={isOperator}>
            <AdminPage initialSection="quota" />
          </RequireOperatorRoute>
        }
      />
      <Route path="*" element={<Navigate to={isOperator ? "/operations/overview" : "/agents"} replace />} />
    </Routes>
  );
}

function RequireTenantContext({
  children,
  tenantId,
}: {
  children: JSX.Element;
  tenantId: string | null;
}) {
  if (!tenantId) {
    return (
      <PageBanner title="Tenant Route Unavailable" severity="warning">
        Your session is authenticated, but the token does not contain tenant context. Use a tenant-scoped account or refresh the session.
      </PageBanner>
    );
  }

  return children;
}

function RequireOperatorRoute({
  children,
  isOperator,
}: {
  children: JSX.Element;
  isOperator: boolean;
}) {
  if (!isOperator) {
    return (
      <PageBanner title="Access Denied" severity="error">
        Platform operator routes require the `Platform.Admin` or `Platform.Operator` role.
      </PageBanner>
    );
  }

  return children;
}

function SignInScreen({ onLogin }: { onLogin: () => Promise<void> }) {
  return (
    <div className="min-h-screen bg-[radial-gradient(circle_at_top,_rgba(34,197,94,0.28),_transparent_28%),linear-gradient(180deg,_#08111f_0%,_#0f172a_55%,_#f8fafc_100%)] px-4 py-10">
      <div className="mx-auto flex min-h-[80vh] max-w-5xl items-center">
        <div className="grid w-full gap-8 lg:grid-cols-[1.3fr_0.9fr]">
          <section className="rounded-[2rem] border border-white/10 bg-slate-950/75 p-8 text-white shadow-2xl backdrop-blur">
            <p className="text-xs font-semibold uppercase tracking-[0.3em] text-cyan-300">Agent Platform</p>
            <h1 className="mt-5 max-w-xl text-4xl font-semibold leading-tight">
              Production shell for tenant operations and platform oversight.
            </h1>
            <p className="mt-4 max-w-2xl text-sm text-slate-300">
              One shell handles agent invocation, tenant administration, and operator visibility while keeping route access explicit and mobile-safe.
            </p>
            <div className="mt-8 grid gap-3 sm:grid-cols-3">
              <SignInFeature title="Tenant Scoped" description="Tenant identity stays visible in the shell and on protected routes." />
              <SignInFeature title="Operator Aware" description="Platform controls appear only when the token carries the right role." />
              <SignInFeature title="Responsive" description="Primary navigation remains usable from phone through desktop." />
            </div>
          </section>
          <section className="rounded-[2rem] bg-white/90 p-8 shadow-2xl ring-1 ring-slate-200">
            <p className="text-sm font-semibold uppercase tracking-[0.2em] text-slate-500">Sign In</p>
            <h2 className="mt-3 text-3xl font-semibold text-slate-950">Use your Entra ID session</h2>
            <p className="mt-4 text-sm text-slate-600">
              The shell uses Entra JWT identity for humans and shows tenant plus role context once the session is established.
            </p>
            <button
              onClick={onLogin}
              className="mt-8 inline-flex w-full justify-center rounded-2xl bg-slate-950 px-4 py-3 text-sm font-semibold text-white transition hover:bg-slate-800"
            >
              Sign In
            </button>
          </section>
        </div>
      </div>
    </div>
  );
}

function SignInFeature({ title, description }: { title: string; description: string }) {
  return (
    <div className="rounded-2xl border border-white/10 bg-white/5 p-4">
      <p className="text-sm font-semibold">{title}</p>
      <p className="mt-2 text-sm text-slate-300">{description}</p>
    </div>
  );
}

export default App;
