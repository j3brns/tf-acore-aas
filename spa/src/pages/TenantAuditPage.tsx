import React, { useMemo, useState } from "react";
import { getApiClient } from "../api/client";
import { useAuth } from "../auth/useAuth";

type AuditResponse = {
    tenantId: string;
    downloadUrl: string;
    expiresAt: string;
};

function resolveTenantId(claims: unknown): string | null {
    if (!claims || typeof claims !== "object") {
        return null;
    }
    const map = claims as Record<string, unknown>;
    const tenantId = map.tenantid ?? map.tenantId;
    return typeof tenantId === "string" ? tenantId.trim() : null;
}

export const TenantAuditPage: React.FC = () => {
    const { getAccessToken, account } = useAuth();
    const tenantId = useMemo(() => resolveTenantId(account?.idTokenClaims), [account?.idTokenClaims]);

    const [startDate, setStartDate] = useState("");
    const [endDate, setEndDate] = useState("");
    const [exporting, setExporting] = useState(false);
    const [result, setResult] = useState<AuditResponse | null>(null);
    const [error, setError] = useState<string | null>(null);

    const onExport = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!tenantId) return;
        setExporting(true);
        setError(null);
        setResult(null);
        try {
            const client = getApiClient(getAccessToken);
            let url = `/v1/tenants/${tenantId}/audit-export`;
            const params = new URLSearchParams();
            if (startDate) params.append("start", new Date(startDate).toISOString());
            if (endDate) params.append("end", new Date(endDate).toISOString());
            if (params.toString()) url += `?${params.toString()}`;

            const response = await client.request<AuditResponse>(url);
            setResult(response);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to generate audit export.");
        } finally {
            setExporting(false);
        }
    };

    return (
        <div className="space-y-6">
            <header>
                <h1 className="text-2xl font-bold text-gray-900">Audit Exports</h1>
                <p className="text-gray-600">Export invocation logs for compliance and review</p>
            </header>

            <section className="bg-white shadow sm:rounded-lg border border-gray-200 overflow-hidden">
                <div className="px-6 py-4 border-b border-gray-200 bg-gray-50">
                    <h2 className="font-semibold text-gray-900">Request New Export</h2>
                </div>
                <form onSubmit={onExport} className="p-6 space-y-4 max-w-md">
                    <div className="grid grid-cols-2 gap-4">
                        <div>
                            <label className="block text-sm font-medium text-gray-700">Start Date (Optional)</label>
                            <input
                                type="date"
                                value={startDate}
                                onChange={e => setStartDate(e.target.value)}
                                className="mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:ring-blue-500 focus:border-blue-500 sm:text-sm"
                            />
                        </div>
                        <div>
                            <label className="block text-sm font-medium text-gray-700">End Date (Optional)</label>
                            <input
                                type="date"
                                value={endDate}
                                onChange={e => setEndDate(e.target.value)}
                                className="mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:ring-blue-500 focus:border-blue-500 sm:text-sm"
                            />
                        </div>
                    </div>
                    <p className="text-xs text-gray-500 italic">
                        If dates are omitted, the full history (up to 90-day retention) will be exported.
                    </p>
                    <div className="pt-2">
                        <button
                            type="submit"
                            disabled={exporting}
                            className="inline-flex justify-center items-center px-4 py-2 border border-transparent text-sm font-medium rounded-md shadow-sm text-white bg-blue-600 hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 disabled:bg-gray-400"
                        >
                            {exporting ? "Generating..." : "Generate Export"}
                        </button>
                    </div>
                    {error && <p className="text-sm text-red-600">{error}</p>}
                </form>
            </section>

            {result && (
                <section className="bg-green-50 border border-green-200 rounded-lg p-6">
                    <h3 className="text-green-900 font-semibold mb-2">Export Ready</h3>
                    <p className="text-green-800 text-sm mb-4">
                        Your audit export has been generated and is ready for download.
                        The link will expire at {new Date(result.expiresAt).toLocaleString()}.
                    </p>
                    <a
                        href={result.downloadUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center px-4 py-2 border border-transparent text-sm font-medium rounded-md shadow-sm text-white bg-green-600 hover:bg-green-700"
                    >
                        Download JSON Export
                    </a>
                </section>
            )}

            <section className="bg-gray-50 p-6 rounded-lg border border-gray-200">
                <h3 className="text-gray-900 font-semibold mb-2">Retention Policy</h3>
                <p className="text-gray-700 text-sm">
                    Invocation logs are retained for **90 days**. Exports generated here are available for download for 1 hour.
                    For longer retention, please schedule regular exports to your own storage.
                </p>
            </section>
        </div>
    );
};
