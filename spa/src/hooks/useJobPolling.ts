import { useEffect, useState, useRef } from "react";
import { getApiClient, AccessTokenProvider } from "../api/client";

export function useJobPolling(jobId: string | null, getAccessToken: AccessTokenProvider, interval = 2000) {
    const [status, setStatus] = useState<any>(null);
    const [_loading, setLoading] = useState(false);
    const [_error, setError] = useState<string | null>(null);
    const timerRef = useRef<number | null>(null);

    useEffect(() => {
        if (!jobId) return;

        const poll = async () => {
            try {
                const client = getApiClient(getAccessToken);
                const data = await client.request<any>(`/v1/jobs/${jobId}`);
                setStatus(data);

                if (data.status === "completed" || data.status === "failed") {
                    if (timerRef.current) window.clearInterval(timerRef.current);
                }
            } catch (err: any) {
                setError(err.message);
                if (timerRef.current) window.clearInterval(timerRef.current);
            }
        };

        setLoading(true);
        poll();
        timerRef.current = window.setInterval(poll, interval);

        return () => {
            if (timerRef.current) window.clearInterval(timerRef.current);
        };
    }, [jobId, getAccessToken, interval]);

    return { status, loading: _loading, error: _error };
}
