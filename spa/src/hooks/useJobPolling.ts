import { useEffect, useState, useRef } from "react";

export function useJobPolling(jobId: string | null, token: string | null, interval = 2000) {
    const [status, setStatus] = useState<any>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const timerRef = useRef<number | null>(null);

    useEffect(() => {
        if (!jobId || !token) return;

        const poll = async () => {
            try {
                const response = await fetch(`${import.meta.env.VITE_API_BASE_URL}/v1/jobs/${jobId}`, {
                    headers: {
                        Authorization: `Bearer ${token}`
                    }
                });
                
                if (!response.ok) throw new Error("Failed to poll job status");
                
                const data = await response.json();
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
    }, [jobId, token, interval]);

    return { status, loading, error };
}
