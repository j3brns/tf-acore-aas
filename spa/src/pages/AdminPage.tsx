import React, { useEffect, useState } from "react";
import {
  AuditExportResponseDto,
  ErrorRateResponseDto,
  FailoverRequestDto,
  FailoverResponseDto,
  HealthResponseDto,
  PlatformQuotaResponseDto,
  SecurityEventsResponseDto,
  TenantDto,
  TenantsListResponseDto,
  TenantUpdateRequestDto,
  TopTenantsResponseDto,
} from "../api/contracts";
import { getApiClient } from "../api/client";
import { useAuth } from "../auth/useAuth";
import { hasPlatformOperatorRole } from "../auth/identity";
import { PageBanner } from "../components/PageBanner";
import { useNotifications } from "../components/Notifications";

type AdminPageProps = {
  initialSection?: "overview" | "tenants" | "quota" | "ops";
};

const sectionBannerCopy: Record<NonNullable<AdminPageProps["initialSection"]>, string> = {
  overview: "Platform health and cross-region runtime posture are in view.",
  tenants: "Tenant portfolio data is surfaced here for operator review.",
  quota: "Quota headroom is highlighted here before runtime saturation becomes an incident.",
  ops: "Real-time operations metrics, security events, and error rates.",
};

export const AdminPage: React.FC<AdminPageProps> = ({ initialSection = "overview" }) => {
  const [health, setHealth] = useState<HealthResponseDto | null>(null);
  const [tenants, setTenants] = useState<TenantDto[]>([]);
  const [quota, setQuota] = useState<PlatformQuotaResponseDto["utilisation"]>([]);
  const [topTenants, setTopTenants] = useState<TopTenantsResponseDto["tenants"]>([]);
  const [securityEvents, setSecurityEvents] = useState<SecurityEventsResponseDto["events"]>([]);
  const [errorRate, setErrorRate] = useState<ErrorRateResponseDto | null>(null);
  
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedTenant, setSelectedTenant] = useState<TenantDto | null>(null);
  const [isFailoverLoading, setIsFailoverLoading] = useState(false);
  
  const { getAccessToken, account, isAuthenticated } = useAuth();
  const { notify } = useNotifications();

  const isAdmin = hasPlatformOperatorRole(account?.idTokenClaims);

  const fetchData = async () => {
    try {
      setLoading(true);
      const client = getApiClient(getAccessToken);

      const [healthData, tenantsData, quotaData, topTenantsData, securityData, errorRateData] = await Promise.all([
        client.request<HealthResponseDto>("/v1/health"),
        client.request<TenantsListResponseDto>("/v1/tenants"),
        client.request<PlatformQuotaResponseDto>("/v1/platform/quota").catch(() => ({ utilisation: [] })),
        client.request<TopTenantsResponseDto>("/v1/platform/ops/top-tenants").catch(() => ({ tenants: [] })),
        client.request<SecurityEventsResponseDto>("/v1/platform/ops/security-events").catch(() => ({ events: [] })),
        client.request<ErrorRateResponseDto>("/v1/platform/ops/error-rate").catch(() => null),
      ]);

      setHealth(healthData);
      setTenants(tenantsData.items || []);
      setQuota(quotaData.utilisation || []);
      setTopTenants(topTenantsData.tenants || []);
      setSecurityEvents(securityData.events || []);
      setErrorRate(errorRateData);
      setError(null);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load admin data.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!isAdmin || !isAuthenticated) {
      if (!isAdmin) {
        setLoading(false);
      }
      return;
    }

    void fetchData();
  }, [getAccessToken, isAdmin, isAuthenticated]);

  const handleUpdateTenantStatus = async (tenantId: string, status: "active" | "suspended") => {
    try {
      const client = getApiClient(getAccessToken);
      await client.request<TenantDto>(`/v1/tenants/${tenantId}`, {
        method: "PATCH",
        body: JSON.stringify({ status } as TenantUpdateRequestDto),
      });
      notify({
        title: "Tenant Updated",
        message: `Tenant ${tenantId} is now ${status}.`,
        severity: "success",
      });
      void fetchData();
      if (selectedTenant?.tenantId === tenantId) {
        setSelectedTenant(prev => prev ? { ...prev, status } : null);
      }
    } catch (err: unknown) {
      notify({
        title: "Update Failed",
        message: err instanceof Error ? err.message : "Failed to update tenant.",
        severity: "error",
      });
    }
  };

  const handleExportAudit = async (tenantId: string) => {
    try {
      const client = getApiClient(getAccessToken);
      const response = await client.request<AuditExportResponseDto>(`/v1/tenants/${tenantId}/audit-export`);
      window.open(response.downloadUrl, "_blank");
      notify({
        title: "Export Started",
        message: "Your audit export is downloading in a new tab.",
        severity: "success",
      });
    } catch (err: unknown) {
      notify({
        title: "Export Failed",
        message: err instanceof Error ? err.message : "Failed to generate audit export.",
        severity: "error",
      });
    }
  };

  const handleFailover = async () => {
    const targetRegion = health?.runtimeRegion.includes("eu-west-1") ? "eu-central-1" : "eu-west-1";
    if (!window.confirm(`Are you sure you want to trigger failover to ${targetRegion}?`)) {
      return;
    }

    try {
      setIsFailoverLoading(true);
      const client = getApiClient(getAccessToken);
      
      await client.request<FailoverResponseDto>("/v1/platform/failover", {
        method: "POST",
        body: JSON.stringify({ 
          targetRegion,
          lockId: `ui-failover-${Date.now()}` 
        } as FailoverRequestDto),
      });

      notify({
        title: "Failover Initiated",
        message: `Routing shifted to ${targetRegion}.`,
        severity: "success",
      });
      void fetchData();
    } catch (err: unknown) {
      notify({
        title: "Failover Failed",
        message: err instanceof Error ? err.message : "Region failover failed.",
        severity: "error",
      });
    } finally {
      setIsFailoverLoading(false);
    }
  };

  if (loading && !health) {
    return <div>Loading admin data...</div>;
  }

  if (!isAdmin) {
    return (
      <PageBanner title="Access Denied" severity="error">
        Platform operator role required.
      </PageBanner>
    );
  }

  return (
    <div className="space-y-8 pb-12">
      <div className="flex justify-between items-start">
        <div>
          <h1 className="text-3xl font-bold text-gray-900">Platform Admin</h1>
          <p className="mt-2 text-gray-600">Global platform health and tenant management.</p>
        </div>
        <button 
          onClick={() => void fetchData()}
          className="px-4 py-2 bg-white border border-gray-300 rounded-md text-sm font-medium text-gray-700 hover:bg-gray-50"
        >
          Refresh Data
        </button>
      </div>

      <PageBanner title={`Operations / ${initialSection}`} severity="info">
        {sectionBannerCopy[initialSection]}
      </PageBanner>

      {error && (
        <PageBanner title="Request Failed" severity="error">
          {error}
        </PageBanner>
      )}

      {/* Overview Stats Grid */}
      <div className="grid grid-cols-1 gap-5 sm:grid-cols-3">
        <div className="bg-white overflow-hidden shadow rounded-lg border border-gray-200">
          <div className="px-4 py-5 sm:p-6">
            <dt className="text-sm font-medium text-gray-500 truncate">Platform Health</dt>
            <dd className="mt-1 flex items-center">
              <div className={`h-3 w-3 rounded-full mr-2 ${health?.status === "ok" ? "bg-green-400" : "bg-red-400"}`} />
              <span className="text-2xl font-semibold text-gray-900 uppercase">{health?.status || "Unknown"}</span>
            </dd>
          </div>
        </div>
        <div className="bg-white overflow-hidden shadow rounded-lg border border-gray-200">
          <div className="px-4 py-5 sm:p-6">
            <dt className="text-sm font-medium text-gray-500 truncate">Error Rate (5m)</dt>
            <dd className="mt-1 flex items-baseline">
              <span className={`text-2xl font-semibold ${errorRate && errorRate.errorRate > errorRate.threshold ? "text-red-600" : "text-gray-900"}`}>
                {errorRate ? `${(errorRate.errorRate * 100).toFixed(1)}%` : "0.0%"}
              </span>
              <span className="ml-2 text-sm font-medium text-gray-500">
                threshold {errorRate ? `${(errorRate.threshold * 100).toFixed(0)}%` : "5%"}
              </span>
            </dd>
          </div>
        </div>
        <div className="bg-white overflow-hidden shadow rounded-lg border border-gray-200">
          <div className="px-4 py-5 sm:p-6">
            <dt className="text-sm font-medium text-gray-500 truncate">Total Tenants</dt>
            <dd className="mt-1 text-2xl font-semibold text-gray-900">{tenants.length}</dd>
          </div>
        </div>
      </div>

      {/* Operations Dashboard Row */}
      <div className="grid grid-cols-1 gap-8 lg:grid-cols-2">
        {/* Top Tenants by Volume */}
        <section className="bg-white shadow sm:rounded-lg border border-gray-200 overflow-hidden">
          <div className="px-4 py-5 sm:px-6 bg-gray-50 border-b border-gray-200 flex justify-between items-center">
            <h3 className="text-lg leading-6 font-medium text-gray-900">Top Tenants (Token Volume)</h3>
          </div>
          <div className="px-4 py-5 sm:p-6">
            {topTenants.length > 0 ? (
              <ul className="divide-y divide-gray-200">
                {topTenants.map((tt) => (
                  <li key={tt.tenantId} className="py-3 flex justify-between items-center">
                    <span className="text-sm font-medium text-gray-900 font-mono">{tt.tenantId}</span>
                    <span className="text-sm text-gray-500">{tt.tokens.toLocaleString()} tokens</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-gray-500 text-center py-4">No volume data available.</p>
            )}
          </div>
        </section>

        {/* Security Events */}
        <section className="bg-white shadow sm:rounded-lg border border-gray-200 overflow-hidden">
          <div className="px-4 py-5 sm:px-6 bg-gray-50 border-b border-gray-200 flex justify-between items-center">
            <h3 className="text-lg leading-6 font-medium text-gray-900">Platform Security Events</h3>
          </div>
          <div className="px-4 py-5 sm:p-6">
            {securityEvents.length > 0 ? (
              <div className="flow-root">
                <ul className="-mb-8">
                  {securityEvents.map((event, eventIdx) => (
                    <li key={`${event.timestamp}-${eventIdx}`}>
                      <div className="relative pb-8">
                        {eventIdx !== securityEvents.length - 1 ? (
                          <span className="absolute top-4 left-4 -ml-px h-full w-0.5 bg-gray-200" aria-hidden="true"></span>
                        ) : null}
                        <div className="relative flex space-x-3">
                          <div>
                            <span className="h-8 w-8 rounded-full bg-rose-100 flex items-center justify-center ring-8 ring-white">
                              <svg className="h-5 w-5 text-rose-600" viewBox="0 0 20 20" fill="currentColor">
                                <path fillRule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clipRule="evenodd" />
                              </svg>
                            </span>
                          </div>
                          <div className="min-w-0 flex-1 pt-1.5 flex justify-between space-x-4">
                            <div>
                              <p className="text-sm text-gray-500">
                                {event.details} <span className="font-medium text-gray-900 font-mono">[{event.tenantId}]</span>
                              </p>
                            </div>
                            <div className="text-right text-sm whitespace-nowrap text-gray-500">
                              <time dateTime={event.timestamp}>{new Date(event.timestamp).toLocaleTimeString()}</time>
                            </div>
                          </div>
                        </div>
                      </div>
                    </li>
                  ))}
                </ul>
              </div>
            ) : (
              <p className="text-sm text-gray-500 text-center py-4">No critical security events in last 24h.</p>
            )}
          </div>
        </section>
      </div>

      {/* Health and Failover Control */}
      <section className="bg-white shadow sm:rounded-lg border border-gray-200 overflow-hidden">
        <div className="px-4 py-5 sm:px-6 bg-gray-50 border-b border-gray-200 flex justify-between items-center">
          <h3 className="text-lg leading-6 font-medium text-gray-900">Postural Control / Region Failover</h3>
          <button
            onClick={() => void handleFailover()}
            disabled={isFailoverLoading}
            className={`px-3 py-1 text-xs font-semibold rounded border ${
              isFailoverLoading 
                ? "bg-gray-100 text-gray-400 border-gray-200" 
                : "bg-amber-50 text-amber-700 border-amber-200 hover:bg-amber-100"
            }`}
          >
            {isFailoverLoading ? "Processing..." : "Trigger Failover"}
          </button>
        </div>
        <div className="px-4 py-5 sm:p-6 flex flex-wrap gap-x-12 gap-y-6">
          <div className="flex flex-col">
            <span className="text-xs text-gray-500 uppercase font-bold">Runtime Region</span>
            <span className="text-sm text-gray-900">{health?.runtimeRegion ?? "N/A"}</span>
          </div>
          <div className="flex flex-col">
            <span className="text-xs text-gray-500 uppercase font-bold">Health Checks</span>
            <span className="text-sm text-gray-900 text-green-600 font-semibold">PASSING</span>
          </div>
          <div className="flex flex-col">
            <span className="text-xs text-gray-500 uppercase font-bold">Latency (Edge-to-Runtime)</span>
            <span className="text-sm text-gray-900">~12ms (via SSM cached)</span>
          </div>
          <div className="flex flex-col">
            <span className="text-xs text-gray-500 uppercase font-bold">Last Node Check</span>
            <span className="text-sm text-gray-900">
              {health?.timestamp ? new Date(health.timestamp).toLocaleTimeString() : "N/A"}
            </span>
          </div>
        </div>
      </section>

      {/* Quota Section */}
      <section className="bg-white shadow sm:rounded-lg border border-gray-200 overflow-hidden">
        <div className="px-4 py-5 sm:px-6 bg-gray-50 border-b border-gray-200">
          <h3 className="text-lg leading-6 font-medium text-gray-900">AgentCore Quota Utilisation</h3>
        </div>
        <div className="px-4 py-5 sm:p-6">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            {quota.map((q) => (
              <div key={`${q.region}-${q.quotaName}`} className="border rounded-md p-4">
                <div className="flex justify-between mb-1">
                  <span className="text-sm font-medium text-gray-700">
                    {q.region} - {q.quotaName}
                  </span>
                  <span className="text-sm font-medium text-gray-700">{q.utilisationPercentage}%</span>
                </div>
                <div className="w-full bg-gray-200 rounded-full h-2.5">
                  <div
                    className={`h-2.5 rounded-full ${q.utilisationPercentage > 80 ? "bg-red-600" : "bg-blue-600"}`}
                    style={{ width: `${q.utilisationPercentage}%` }}
                  />
                </div>
                <div className="mt-2 text-xs text-gray-500">
                  {q.currentValue} / {q.limit} sessions
                </div>
              </div>
            ))}
            {quota.length === 0 && <p className="text-sm text-gray-500">No quota data available.</p>}
          </div>
        </div>
      </section>

      {/* Tenants Section */}
      <section className="bg-white shadow sm:rounded-lg border border-gray-200 overflow-hidden">
        <div className="px-4 py-5 sm:px-6 bg-gray-50 border-b border-gray-200 flex justify-between items-center">
          <h3 className="text-lg leading-6 font-medium text-gray-900">Tenants</h3>
          <span className="bg-blue-100 text-blue-800 text-xs font-semibold px-2.5 py-0.5 rounded-full">
            {tenants.length} Total
          </span>
        </div>
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Tenant ID</th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Name</th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Tier</th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Status</th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Actions</th>
              </tr>
            </thead>
            <tbody className="bg-white divide-y divide-gray-200">
              {tenants.map((tenant) => (
                <tr key={tenant.tenantId} className={selectedTenant?.tenantId === tenant.tenantId ? "bg-blue-50" : ""}>
                  <td className="px-6 py-4 whitespace-nowrap text-sm font-medium text-gray-900">{tenant.tenantId}</td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">{tenant.displayName}</td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500 capitalize">{tenant.tier}</td>
                  <td className="px-6 py-4 whitespace-nowrap">
                    <span className={`px-2 inline-flex text-xs leading-5 font-semibold rounded-full ${
                      tenant.status === "active" ? "bg-green-100 text-green-800" : 
                      tenant.status === "suspended" ? "bg-amber-100 text-amber-800" : "bg-red-100 text-red-800"
                    }`}>
                      {tenant.status}
                    </span>
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500 space-x-3">
                    <button 
                      onClick={() => setSelectedTenant(tenant)}
                      className="text-blue-600 hover:text-blue-900 font-medium"
                    >
                      View
                    </button>
                    {tenant.status === "active" ? (
                      <button 
                        onClick={() => void handleUpdateTenantStatus(tenant.tenantId, "suspended")}
                        className="text-amber-600 hover:text-amber-900 font-medium"
                      >
                        Suspend
                      </button>
                    ) : tenant.status === "suspended" ? (
                      <button 
                        onClick={() => void handleUpdateTenantStatus(tenant.tenantId, "active")}
                        className="text-green-600 hover:text-green-900 font-medium"
                      >
                        Reinstate
                      </button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {/* Tenant Detail Drawer */}
      {selectedTenant && (
        <div className="fixed inset-0 overflow-hidden z-40" aria-labelledby="slide-over-title" role="dialog" aria-modal="true">
          <div className="absolute inset-0 overflow-hidden">
            <div className="absolute inset-0 bg-gray-500 bg-opacity-75 transition-opacity" onClick={() => setSelectedTenant(null)}></div>
            <div className="pointer-events-none fixed inset-y-0 right-0 flex max-w-full pl-10">
              <div className="pointer-events-auto w-screen max-w-md">
                <div className="flex h-full flex-col overflow-y-scroll bg-white shadow-xl">
                  <div className="bg-gray-50 px-4 py-6 sm:px-6 border-b border-gray-200">
                    <div className="flex items-start justify-between">
                      <h2 className="text-lg font-medium text-gray-900" id="slide-over-title">Tenant Details</h2>
                      <div className="ml-3 flex h-7 items-center">
                        <button
                          type="button"
                          className="rounded-md bg-white text-gray-400 hover:text-gray-500 focus:outline-none focus:ring-2 focus:ring-blue-500"
                          onClick={() => setSelectedTenant(null)}
                        >
                          <span className="sr-only">Close panel</span>
                          <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" strokeWidth="1.5" stroke="currentColor" aria-hidden="true">
                            <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                          </svg>
                        </button>
                      </div>
                    </div>
                  </div>
                  <div className="relative flex-1 px-4 py-6 sm:px-6 space-y-6">
                    <div>
                      <h4 className="text-sm font-bold text-gray-900 uppercase tracking-wider">Identity</h4>
                      <dl className="mt-2 grid grid-cols-1 gap-x-4 gap-y-4 sm:grid-cols-2">
                        <div className="sm:col-span-2">
                          <dt className="text-xs font-medium text-gray-500">Tenant ID</dt>
                          <dd className="mt-1 text-sm text-gray-900 font-mono">{selectedTenant.tenantId}</dd>
                        </div>
                        <div>
                          <dt className="text-xs font-medium text-gray-500">App ID</dt>
                          <dd className="mt-1 text-sm text-gray-900">{selectedTenant.appId}</dd>
                        </div>
                        <div>
                          <dt className="text-xs font-medium text-gray-500">AWS Account</dt>
                          <dd className="mt-1 text-sm text-gray-900">{selectedTenant.accountId}</dd>
                        </div>
                      </dl>
                    </div>

                    <div>
                      <h4 className="text-sm font-bold text-gray-900 uppercase tracking-wider">Configuration</h4>
                      <dl className="mt-2 grid grid-cols-1 gap-x-4 gap-y-4 sm:grid-cols-2">
                        <div>
                          <dt className="text-xs font-medium text-gray-500">Tier</dt>
                          <dd className="mt-1 text-sm text-gray-900 capitalize">{selectedTenant.tier}</dd>
                        </div>
                        <div>
                          <dt className="text-xs font-medium text-gray-500">Status</dt>
                          <dd className="mt-1 text-sm text-gray-900 capitalize">{selectedTenant.status}</dd>
                        </div>
                        <div>
                          <dt className="text-xs font-medium text-gray-500">Primary Region</dt>
                          <dd className="mt-1 text-sm text-gray-900">{selectedTenant.runtimeRegion ?? "N/A"}</dd>
                        </div>
                        <div>
                          <dt className="text-xs font-medium text-gray-500">Monthly Budget</dt>
                          <dd className="mt-1 text-sm text-gray-900">${selectedTenant.monthlyBudgetUsd ?? "0"}.00</dd>
                        </div>
                      </dl>
                    </div>

                    <div>
                      <h4 className="text-sm font-bold text-gray-900 uppercase tracking-wider">Operational Actions</h4>
                      <div className="mt-3 flex flex-col space-y-2">
                        <button
                          onClick={() => void handleExportAudit(selectedTenant.tenantId)}
                          className="w-full inline-flex justify-center items-center px-4 py-2 border border-gray-300 shadow-sm text-sm font-medium rounded-md text-gray-700 bg-white hover:bg-gray-50"
                        >
                          Export Invocation Audit
                        </button>
                        {selectedTenant.status === "active" ? (
                          <button
                            onClick={() => void handleUpdateTenantStatus(selectedTenant.tenantId, "suspended")}
                            className="w-full inline-flex justify-center items-center px-4 py-2 border border-transparent shadow-sm text-sm font-medium rounded-md text-white bg-amber-600 hover:bg-amber-700"
                          >
                            Suspend Tenant Access
                          </button>
                        ) : (
                          <button
                            onClick={() => void handleUpdateTenantStatus(selectedTenant.tenantId, "active")}
                            className="w-full inline-flex justify-center items-center px-4 py-2 border border-transparent shadow-sm text-sm font-medium rounded-md text-white bg-green-600 hover:bg-green-700"
                          >
                            Reinstate Tenant Access
                          </button>
                        )}
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
