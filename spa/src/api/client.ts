interface RequestOptions extends RequestInit {
    token?: string;
}

export const apiClient = {
    async fetch(path: string, options: RequestOptions = {}) {
        const { token, ...fetchOptions } = options;
        const headers = new Headers(fetchOptions.headers || {});

        if (token) {
            headers.set("Authorization", `Bearer ${token}`);
        }

        const response = await fetch(`${import.meta.env.VITE_API_BASE_URL}${path}`, {
            ...fetchOptions,
            headers,
        });

        if (response.status === 401) {
            // Potential token refresh logic handled by AuthProvider/useAuth wrapper
            throw new Error("UNAUTHORISED");
        }

        if (!response.ok) {
            const error = await response.json().catch(() => ({ message: "Unknown error" }));
            throw new Error(error.message || response.statusText);
        }

        return response.json();
    },

    async fetchStream(path: string, options: RequestOptions = {}) {
        const { token, ...fetchOptions } = options;
        const headers = new Headers(fetchOptions.headers || {});

        if (token) {
            headers.set("Authorization", `Bearer ${token}`);
        }

        const response = await fetch(`${import.meta.env.VITE_API_BASE_URL}${path}`, {
            ...fetchOptions,
            headers,
        });

        if (response.status === 401) {
            throw new Error("UNAUTHORISED");
        }

        if (!response.ok) {
            const error = await response.json().catch(() => ({ message: "Unknown error" }));
            throw new Error(error.message || response.statusText);
        }

        return response.body; // ReadableStream
    }
};
