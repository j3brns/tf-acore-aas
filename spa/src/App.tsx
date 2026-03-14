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
import { Loading } from "./components/ui/loading";
import { Button } from "./components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./components/ui/card";
import { Typography } from "./components/ui/typography";
import { Globe, Shield, Zap, Lock, ArrowRight } from "lucide-react";

function App() {
  const { isAuthenticated, login, isLoading } = useAuth();

  if (isLoading) {
    return (
      <div className="min-h-screen bg-slate-950 flex flex-col items-center justify-center p-4">
        <Loading message="Authenticating session..." size="lg" />
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
      <Route path="/jobs" element={<SessionsPage />} /> {/* Placeholder until JobsPage exists */}
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
      <PageBanner title="Provisioning Required" severity="warning">
        Your account is authenticated but has not yet been assigned to a tenant. 
        Please contact your administrator to provision your <code>tenantid</code> claim.
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
      <PageBanner title="Unauthorized Access" severity="error">
        You do not have the required <code>Platform.Admin</code> or <code>Platform.Operator</code> role 
        to access this operational surface.
      </PageBanner>
    );
  }

  return children;
}

function SignInScreen({ onLogin }: { onLogin: () => Promise<void> }) {
  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 selection:bg-cyan-500/30 overflow-hidden relative">
      {/* Decorative background elements */}
      <div className="absolute inset-0 -z-10 bg-[radial-gradient(circle_at_top_left,_rgba(34,197,94,0.15),_transparent_40%),radial-gradient(circle_at_bottom_right,_rgba(56,189,248,0.15),_transparent_40%)]" />
      <div className="absolute top-1/4 -left-20 w-96 h-96 bg-cyan-500/10 rounded-full blur-[120px]" />
      <div className="absolute bottom-1/4 -right-20 w-96 h-96 bg-blue-600/10 rounded-full blur-[120px]" />

      <div className="mx-auto flex min-h-screen max-w-7xl flex-col items-center justify-center px-4 py-12 sm:px-6 lg:px-8">
        <div className="w-full grid gap-12 lg:grid-cols-[1.2fr_0.8fr] items-center">
          <section className="space-y-8">
            <div className="flex items-center gap-3">
              <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-gradient-to-br from-cyan-500 to-blue-600 shadow-xl shadow-cyan-500/20">
                <Globe className="h-7 w-7 text-white" />
              </div>
              <Typography variant="h3" className="font-bold tracking-tight text-white">LoopaaS Platform</Typography>
            </div>
            
            <div className="space-y-4">
              <Typography variant="h1" className="text-5xl lg:text-6xl font-extrabold tracking-tight text-white leading-[1.1]">
                Enterprise AI <span className="text-transparent bg-clip-text bg-gradient-to-r from-cyan-400 to-blue-500">Orchestration</span> at Scale.
              </Typography>
              <Typography variant="lead" className="max-w-xl text-slate-400">
                Securely invoke, monitor, and manage AI agents across your organization with production-grade isolation and observability.
              </Typography>
            </div>

            <div className="grid gap-6 sm:grid-cols-2">
              <SignInFeature 
                icon={Shield} 
                title="Tenant Isolation" 
                description="Defense-in-depth isolation ensures your data never crosses tenant boundaries." 
              />
              <SignInFeature 
                icon={Zap} 
                title="Instant Deployment" 
                description="Push new agents in seconds with our optimized serverless runtime pipeline." 
              />
              <SignInFeature 
                icon={Lock} 
                title="Entra Integrated" 
                description="Native OIDC integration with Microsoft Entra for seamless enterprise SSO." 
              />
              <SignInFeature 
                icon={Globe} 
                title="EU Residency" 
                description="Strict data residency controls keep all platform data within the European Union." 
              />
            </div>
          </section>

          <Card className="border-white/10 bg-slate-900/50 backdrop-blur-xl shadow-2xl p-2 sm:p-4 ring-1 ring-white/10">
            <CardHeader className="space-y-1 pb-8">
              <CardTitle className="text-2xl font-bold text-white">Sign In</CardTitle>
              <CardDescription className="text-slate-400">
                Authorized personnel only. Access is logged and audited.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
              <div className="space-y-4 rounded-2xl bg-white/5 p-4 border border-white/5">
                <Typography variant="small" className="text-slate-300 font-semibold uppercase tracking-widest flex items-center gap-2">
                  <Shield className="h-3 w-3 text-cyan-400" />
                  Security Protocol
                </Typography>
                <Typography variant="muted" className="text-xs leading-relaxed">
                  By signing in, you agree to the platform's security policies. 
                  Your session will be granted scope-limited access based on your Entra ID role assignments.
                </Typography>
              </div>

              <Button 
                onClick={onLogin} 
                size="lg" 
                className="w-full rounded-xl bg-gradient-to-r from-cyan-500 to-blue-600 hover:from-cyan-400 hover:to-blue-500 text-white font-bold h-14 shadow-lg shadow-cyan-500/20 group"
              >
                Continue with Microsoft Entra
                <ArrowRight className="ml-2 h-5 w-5 transition-transform group-hover:translate-x-1" />
              </Button>

              <div className="text-center pt-4">
                <Typography variant="muted" className="text-[10px] uppercase tracking-[0.2em]">
                  Platform Version 1.2.4-stable
                </Typography>
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
      
      <footer className="absolute bottom-8 left-0 right-0 text-center px-4">
        <Typography variant="muted" className="text-[11px]">
          © 2026 LoopaaS Platform. Part of the AgentCore franchise. All rights reserved.
        </Typography>
      </footer>
    </div>
  );
}

function SignInFeature({ icon: Icon, title, description }: { icon: any; title: string; description: string }) {
  return (
    <div className="group relative rounded-2xl border border-white/5 bg-white/5 p-5 transition-all hover:bg-white/10 hover:border-white/10">
      <div className="mb-3 flex h-10 w-10 items-center justify-center rounded-xl bg-cyan-500/10 text-cyan-400 group-hover:scale-110 transition-transform">
        <Icon className="h-5 w-5" />
      </div>
      <Typography variant="small" className="font-bold text-white mb-1 block">{title}</Typography>
      <Typography variant="muted" className="text-xs leading-relaxed line-clamp-2">{description}</Typography>
    </div>
  );
}

export default App;
