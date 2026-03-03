import React, { useEffect, useState } from "react";
import { apiClient } from "../api/client";
import { useAuth } from "../auth/useAuth";
import { Session } from "../types";

export const SessionsPage: React.FC = () => {
    const [sessions, setSessions] = useState<Session[]>([]);
    const [loading, setLoading] = useState(true);
    const [_error, setError] = useState<string | null>(null);
    const { getToken } = useAuth();

    useEffect(() => {
        const fetchSessions = async () => {
            try {
                const token = await getToken();
                if (!token) return;
                const data = await apiClient.fetch("/v1/sessions", { token });
                setSessions(data.sessions || []);
            } catch (err: any) {
                setError(err.message);
            } finally {
                setLoading(false);
            }
        };
        fetchSessions();
    }, [getToken]);

    if (loading) return <div>Loading sessions...</div>;

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
                            <tr key={session.session_id}>
                                <td className="px-6 py-4 whitespace-nowrap text-sm font-medium text-gray-900 font-mono">{session.session_id.substring(0, 8)}...</td>
                                <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">{session.agent_name}</td>
                                <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">{new Date(session.started_at).toLocaleString()}</td>
                                <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">{new Date(session.last_activity_at).toLocaleString()}</td>
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
