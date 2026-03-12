import React, { useEffect, useState } from "react";
import {
  HealthResponseDto,
  PlatformQuotaResponseDto,
  TenantAdminRow,
  TenantsListResponseDto,
  toTenantAdminRow,
} from "../api/contracts";
import { getApiClient } from "../api/client";
import { useAuth } from "../auth/useAuth";
import { hasPlatformOperatorRole } from "../auth/identity";
import { PageBanner } from "../components/PageBanner";

type AdminPageProps = {
  initialSection?: "overview" | "tenants" | "quota";
};

const sectionBannerCopy: Record<NonNullable<AdminPageProps["initialSection"]>, string> = {
  overview: "Platform health and cross-region runtime posture are in view.",
  tenants: "Tenant portfolio data is surfaced here for operator review.",
  quota: "Quota headroom is highlighted here before runtime saturation becomes an incident.",
};

export const AdminPage: React.FC<AdminPageProps> = ({ initialSection = "overview" }) => {
  const [health, setHealth] = useState<HealthResponseDto | null>(null);
  const [tenants, setTenants] = useState<TenantAdminRow[]>([]);
  const [quota, setQuota] = useState<PlatformQuotaResponseDto["utilisation"]>([]);
  const [loading, setLoading] = useState(true);
  const [_error, setError] = useState<string | null>(null);
  const { getAccessToken, account, isAuthenticated } = useAuth();

  const isAdmin = hasPlatformOperatorRole(account?.idTokenClaims);

  useEffect(() => {
    if (!isAdmin || !isAuthenticated) {
      if (!isAdmin) {
        setLoading(false);
      }
      return;
    }

    const fetchAdminData = async () => {
      try {
        const client = getApiClient(getAccessToken);

        const [healthData, tenantsData, quotaData] = await Promise.all([
          client.request<HealthResponseDto>("/v1/health"),
          client.request<TenantsListResponseDto>("/v1/tenants"),
          client
            .request<PlatformQuotaResponseDto>("/v1/platform/quota")
            .catch(() => ({ utilisation: [] })),
        ]);

        setHealth(healthData);
        setTenants((tenantsData.items || []).map(toTenantAdminRow));
        setQuota(quotaData.utilisation || []);
      } catch (err: unknown) {
        setError(err instanceof Error ? err.message : "Failed to load admin data.");
      } finally {
        setLoading(false);
      }
    };

    void fetchAdminData();
  }, [getAccessToken, isAdmin, isAuthenticated]);

  if (loading) {
    return <div>Loading admin data...</div>;
  }

  if (!isAdmin) {
    return (
      <PageBanner title="Access Denied" severity="error">
        Platform operator role required.
      </PageBanner>
    );
  }

  if (_error) {
    return (
      <PageBanner title="Admin Request Failed" severity="error">
        {_error}
      </PageBanner>
    );
  }

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-3xl font-bold text-gray-900">Platform Admin</h1>
        <p className="mt-2 text-gray-600">Global platform health and tenant management.</p>
      </div>

      <PageBanner title={`Operations / ${initialSection}`} severity="info">
        {sectionBannerCopy[initialSection]}
      </PageBanner>

      <section className="bg-white shadow sm:rounded-lg border border-gray-200 overflow-hidden">
        <div className="px-4 py-5 sm:px-6 bg-gray-50 border-b border-gray-200">
          <h3 className="text-lg leading-6 font-medium text-gray-900">Platform Health</h3>
        </div>
        <div className="px-4 py-5 sm:p-6 flex items-center space-x-4">
          <div className={`h-4 w-4 rounded-full ${health?.status === "ok" ? "bg-green-400" : "bg-red-400"} animate-pulse`} />
          <span className="text-lg font-semibold uppercase">{health?.status || "Unknown"}</span>
          <span className="text-sm text-gray-500">Version: {health?.version}</span>
          <span className="text-sm text-gray-500">
            Last Check: {health?.timestamp ? new Date(health.timestamp).toLocaleTimeString() : "N/A"}
          </span>
        </div>
      </section>

      <section className="bg-white shadow sm:rounded-lg border border-gray-200 overflow-hidden">
        <div className="px-4 py-5 sm:px-6 bg-gray-50 border-b border-gray-200">
          <h3 className="text-lg leading-6 font-medium text-gray-900">AgentCore Quota Utilisation</h3>
        </div>
        <div className="px-4 py-5 sm:p-6">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            {quota.map((q) => (
              <div key={q.region} className="border rounded-md p-4">
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
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Region</th>
              </tr>
            </thead>
            <tbody className="bg-white divide-y divide-gray-200">
              {tenants.map((tenant) => (
                <tr key={tenant.tenantId}>
                  <td className="px-6 py-4 whitespace-nowrap text-sm font-medium text-gray-900">{tenant.tenantId}</td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">{tenant.displayName}</td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500 capitalize">{tenant.tier}</td>
                  <td className="px-6 py-4 whitespace-nowrap">
                    <span className={`px-2 inline-flex text-xs leading-5 font-semibold rounded-full ${tenant.status === "active" ? "bg-green-100 text-green-800" : "bg-red-100 text-red-800"}`}>
                      {tenant.status}
                    </span>
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">{tenant.runtimeRegion ?? "N/A"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
};
