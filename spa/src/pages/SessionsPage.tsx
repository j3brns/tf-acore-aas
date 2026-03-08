import React, { useEffect, useState } from "react";
import { SessionRow, SessionsListResponseDto, toSessionRow } from "../api/contracts";
import { getApiClient } from "../api/client";
import { useAuth } from "../auth/useAuth";

export const SessionsPage: React.FC = () => {
    const [sessions, setSessions] = useState<SessionRow[]>([]);
    const [loading, setLoading] = useState(true);
    const [_error, setError] = useState<string | null>(null);
    const { getAccessToken, isAuthenticated } = useAuth();

    useEffect(() => {
        const fetchSessions = async () => {
            if (!isAuthenticated) return;
            try {
                const client = getApiClient(getAccessToken);
                const data = await client.request<SessionsListResponseDto>("/v1/sessions");
                setSessions((data.items || []).map(toSessionRow));
            } catch (err: any) {
                setError(err.message);
            } finally {
                setLoading(false);
            }
        };
        fetchSessions();
    }, [getAccessToken, isAuthenticated]);

    if (loading) return <div>Loading sessions...</div>;
    if (_error) {
        return (
            <div className="bg-red-50 border-l-4 border-red-400 p-4">
                <p className="text-sm text-red-700">{_error}</p>
            </div>
        );
    }

    return (
        <div>
            <div className="mb-8">
                <h1 className="text-3xl font-bold text-gray-900">Active Sessions</h1>
                <p className="mt-2 text-gray-600">Monitor your active agent sessions.</p>
            </div>

            <div className="bg-white shadow overflow-hidden sm:rounded-lg border border-gray-200">
                <table className="min-w-full divide-y divide-gray-200">
                    <thead className="bg-gray-50">
                        <tr>
                            <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Session ID</th>
                            <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Agent</th>
                            <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Started</th>
                            <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Last Activity</th>
                            <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Status</th>
                        </tr>
                    </thead>
                    <tbody className="bg-white divide-y divide-gray-200">
                        {sessions.map((session) => (
                            <tr key={session.sessionId}>
                                <td className="px-6 py-4 whitespace-nowrap text-sm font-medium text-gray-900 font-mono">{session.sessionId.substring(0, 8)}...</td>
                                <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">{session.agentName}</td>
                                <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">{new Date(session.startedAt).toLocaleString()}</td>
                                <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">{new Date(session.lastActivityAt).toLocaleString()}</td>
                                <td className="px-6 py-4 whitespace-nowrap">
                                    <span className={`px-2 inline-flex text-xs leading-5 font-semibold rounded-full ${
                                        session.status === "active" ? "bg-green-100 text-green-800" : "bg-gray-100 text-gray-800"
                                    }`}>
                                        {session.status}
                                    </span>
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
                {sessions.length === 0 && (
                    <div className="text-center py-12 text-gray-500">No active sessions.</div>
                )}
            </div>
        </div>
    );
};
