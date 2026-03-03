import React, { useEffect, useState } from "react";
import { apiClient } from "../api/client";
import { useAuth } from "../auth/useAuth";

export const AdminPage: React.FC = () => {
    const [health, setHealth] = useState<any>(null);
    const [tenants, setTenants] = useState<any[]>([]);
    const [quota, setQuota] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [_error, setError] = useState<string | null>(null);
    const { getToken, account } = useAuth();

    const isAdmin = account?.idTokenClaims?.roles?.some((role: string) => 
        role === "Platform.Admin" || role === "Platform.Operator"
    );

    useEffect(() => {
        if (!isAdmin) {
            setLoading(false);
            return;
        }
        const fetchAdminData = async () => {
            try {
                const token = await getToken();
                if (!token) return;

                const [healthData, tenantsData, quotaData] = await Promise.all([
                    apiClient.fetch("/v1/health", { token }),
                    apiClient.fetch("/v1/tenants", { token }),
                    apiClient.fetch("/v1/platform/quota", { token }).catch(() => ({ utilisation: [] }))
                ]);

                setHealth(healthData);
                setTenants(tenantsData.items || []);
                setQuota(quotaData.utilisation || []);
            } catch (err: any) {
                setError(err.message);
            } finally {
                setLoading(false);
            }
        };

        fetchAdminData();
    }, [getToken]);

    if (loading) return <div>Loading admin data...</div>;

    if (!isAdmin) {
        return (
            <div className="bg-red-50 border-l-4 border-red-400 p-4">
                <div className="flex">
                    <div className="ml-3">
                        <p className="text-sm text-red-700">Access Denied: Platform.Operator role required.</p>
                    </div>
                </div>
            </div>
        );
    }

    return (
        <div className="space-y-8">
            <div>
                <h1 className="text-3xl font-bold text-gray-900">Platform Admin</h1>
                <p className="mt-2 text-gray-600">Global platform health and tenant management.</p>
            </div>

            {/* Health Section */}
            <section className="bg-white shadow sm:rounded-lg border border-gray-200 overflow-hidden">
                <div className="px-4 py-5 sm:px-6 bg-gray-50 border-b border-gray-200">
                    <h3 className="text-lg leading-6 font-medium text-gray-900">Platform Health</h3>
                </div>
                <div className="px-4 py-5 sm:p-6 flex items-center space-x-4">
                    <div className={`h-4 w-4 rounded-full ${
                        health?.status === "ok" ? "bg-green-400" : "bg-red-400"
                    } animate-pulse`}></div>
                    <span className="text-lg font-semibold uppercase">{health?.status || "Unknown"}</span>
                    <span className="text-sm text-gray-500">Version: {health?.version}</span>
                    <span className="text-sm text-gray-500">Last Check: {health?.timestamp ? new Date(health.timestamp).toLocaleTimeString() : "N/A"}</span>
                </div>
            </section>

            {/* Quota Section */}
            <section className="bg-white shadow sm:rounded-lg border border-gray-200 overflow-hidden">
                <div className="px-4 py-5 sm:px-6 bg-gray-50 border-b border-gray-200">
                    <h3 className="text-lg leading-6 font-medium text-gray-900">AgentCore Quota Utilisation</h3>
                </div>
                <div className="px-4 py-5 sm:p-6">
                    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                        {quota.map((q: any) => (
                            <div key={q.region} className="border rounded-md p-4">
                                <div className="flex justify-between mb-1">
                                    <span className="text-sm font-medium text-gray-700">{q.region} - {q.quotaName}</span>
                                    <span className="text-sm font-medium text-gray-700">{q.utilisationPercentage}%</span>
                                </div>
                                <div className="w-full bg-gray-200 rounded-full h-2.5">
                                    <div 
                                        className={`h-2.5 rounded-full ${
                                            q.utilisationPercentage > 80 ? "bg-red-600" : "bg-blue-600"
                                        }`} 
                                        style={{ width: `${q.utilisationPercentage}%` }}
                                    ></div>
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
                                        <span className={`px-2 inline-flex text-xs leading-5 font-semibold rounded-full ${
                                            tenant.status === "active" ? "bg-green-100 text-green-800" : "bg-red-100 text-red-800"
                                        }`}>
                                            {tenant.status}
                                        </span>
                                    </td>
                                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">{tenant.runtimeRegion || "N/A"}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            </section>
        </div>
    );
};
