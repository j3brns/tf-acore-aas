import React, { useEffect, useMemo, useState } from "react";
import { getApiClient } from "../api/client";
import type {
    WebhookListItemDto,
    WebhookRegistrationResponseDto,
    WebhooksListResponseDto,
} from "../api/contracts";
import { useAuth } from "../auth/useAuth";

function resolveTenantId(claims: unknown): string | null {
    if (!claims || typeof claims !== "object") {
        return null;
    }
    const map = claims as Record<string, unknown>;
    const tenantId = map.tenantid ?? map.tenantId;
    return typeof tenantId === "string" ? tenantId.trim() : null;
}

export const TenantWebhooksPage: React.FC = () => {
    const { getAccessToken, account, isAuthenticated } = useAuth();
    const tenantId = useMemo(() => resolveTenantId(account?.idTokenClaims), [account?.idTokenClaims]);

    const [webhooks, setWebhooks] = useState<WebhookListItemDto[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [callbackUrl, setCallbackUrl] = useState("");
    const [description, setDescription] = useState("");
    const [selectedEvents, setSelectedEvents] = useState<string[]>(["job.completed"]);
    const [submitting, setSubmitting] = useState(false);
    const [message, setMessage] = useState<{ type: 'success' | 'error', text: string } | null>(null);

    useEffect(() => {
        if (!isAuthenticated || !tenantId) {
            setLoading(false);
            return;
        }

        const run = async () => {
            try {
                const client = getApiClient(getAccessToken);
                const data = await client.request<WebhooksListResponseDto>(`/v1/webhooks`);
                setWebhooks(data.items);
            } catch (err) {
                setError(err instanceof Error ? err.message : "Failed to load webhooks.");
            } finally {
                setLoading(false);
            }
        };
        void run();
    }, [getAccessToken, isAuthenticated, tenantId]);

    const onSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        setSubmitting(true);
        setMessage(null);
        try {
            const client = getApiClient(getAccessToken);
            const response = await client.request<WebhookRegistrationResponseDto>(`/v1/webhooks`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    callbackUrl: callbackUrl.trim(),
                    description: description.trim(),
                    events: selectedEvents
                }),
            });
            setMessage({ type: 'success', text: "Webhook registered successfully." });
            setCallbackUrl("");
            setDescription("");
            setWebhooks(prev => [
                ...prev,
                {
                    ...response,
                    description: description.trim() || undefined,
                    status: "active",
                },
            ]);
        } catch (err) {
            setMessage({ type: 'error', text: err instanceof Error ? err.message : "Failed to register webhook." });
        } finally {
            setSubmitting(false);
        }
    };

    const onDelete = async (webhookId: string) => {
        if (!window.confirm("Are you sure you want to delete this webhook?")) return;
        try {
            const client = getApiClient(getAccessToken);
            await client.request(`/v1/webhooks/${webhookId}`, { method: "DELETE" });
            setWebhooks(prev => prev.filter(w => w.webhookId !== webhookId));
        } catch (err) {
            alert(err instanceof Error ? err.message : "Failed to delete webhook.");
        }
    };

    if (loading) return <div className="p-8">Loading webhooks...</div>;
    if (error) return <div className="p-8 text-red-600">Error: {error}</div>;

    return (
        <div className="space-y-6">
            <header>
                <h1 className="text-2xl font-bold text-gray-900">Webhooks</h1>
                <p className="text-gray-600">Manage async job completion callbacks</p>
            </header>

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                <section className="lg:col-span-2 space-y-4">
                    {webhooks.length === 0 ? (
                        <div className="bg-white p-12 text-center rounded-lg border border-gray-200 shadow-sm text-gray-500">
                            No webhooks configured.
                        </div>
                    ) : (
                        webhooks.map((webhook) => (
                            <div key={webhook.webhookId} className="bg-white p-6 rounded-lg border border-gray-200 shadow-sm flex justify-between items-start">
                                <div className="space-y-1">
                                    <div className="flex items-center space-x-2">
                                        <h3 className="font-semibold text-gray-900">{webhook.description || "Untitled Webhook"}</h3>
                                        <span className="px-2 py-0.5 rounded-full text-xs bg-green-100 text-green-800 uppercase font-medium">
                                            {webhook.status}
                                        </span>
                                    </div>
                                    <p className="text-sm font-mono text-gray-600">{webhook.callbackUrl}</p>
                                    <div className="flex flex-wrap gap-2 mt-2">
                                        {webhook.events.map(event => (
                                            <span key={event} className="px-2 py-0.5 rounded bg-gray-100 text-gray-600 text-xs">
                                                {event}
                                            </span>
                                        ))}
                                    </div>
                                    <p className="text-xs text-gray-400 mt-2">Registered on {new Date(webhook.createdAt).toLocaleString()}</p>
                                </div>
                                <button
                                    onClick={() => onDelete(webhook.webhookId)}
                                    className="text-red-600 hover:text-red-800 text-sm font-medium"
                                >
                                    Delete
                                </button>
                            </div>
                        ))
                    )}
                </section>

                <section className="bg-white shadow sm:rounded-lg border border-gray-200 overflow-hidden h-fit">
                    <div className="px-6 py-4 border-b border-gray-200 bg-gray-50">
                        <h2 className="font-semibold text-gray-900">Register Webhook</h2>
                    </div>
                    <form onSubmit={onSubmit} className="p-6 space-y-4">
                        <div>
                            <label className="block text-sm font-medium text-gray-700">Callback URL</label>
                            <input
                                type="url"
                                required
                                value={callbackUrl}
                                onChange={e => setCallbackUrl(e.target.value)}
                                className="mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:ring-blue-500 focus:border-blue-500 sm:text-sm"
                                placeholder="https://your-api.com/webhooks"
                            />
                        </div>
                        <div>
                            <label className="block text-sm font-medium text-gray-700">Description (Optional)</label>
                            <input
                                type="text"
                                value={description}
                                onChange={e => setDescription(e.target.value)}
                                className="mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:ring-blue-500 focus:border-blue-500 sm:text-sm"
                                placeholder="e.g. Production events"
                            />
                        </div>
                        <div>
                            <label className="block text-sm font-medium text-gray-700 mb-2">Events</label>
                            <div className="space-y-2">
                                {["job.completed", "job.failed"].map(event => (
                                    <label key={event} className="flex items-center">
                                        <input
                                            type="checkbox"
                                            checked={selectedEvents.includes(event)}
                                            onChange={e => {
                                                if (e.target.checked) setSelectedEvents(prev => [...prev, event]);
                                                else setSelectedEvents(prev => prev.filter(ev => ev !== event));
                                            }}
                                            className="rounded border-gray-300 text-blue-600 focus:ring-blue-500 h-4 w-4"
                                        />
                                        <span className="ml-2 text-sm text-gray-600">{event}</span>
                                    </label>
                                ))}
                            </div>
                        </div>
                        <div className="pt-2">
                            <button
                                type="submit"
                                disabled={submitting}
                                className="w-full inline-flex justify-center items-center px-4 py-2 border border-transparent text-sm font-medium rounded-md shadow-sm text-white bg-blue-600 hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 disabled:bg-gray-400"
                            >
                                {submitting ? "Registering..." : "Register Webhook"}
                            </button>
                        </div>
                        {message && (
                            <p className={`text-sm ${message.type === 'error' ? 'text-red-600' : 'text-green-600'}`}>
                                {message.text}
                            </p>
                        )}
                    </form>
                </section>
            </div>
        </div>
    );
};
