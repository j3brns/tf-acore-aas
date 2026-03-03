import React, { createContext, useContext, useEffect, useState } from "react";
import { PublicClientApplication, IPublicClientApplication } from "@azure/msal-browser";
import { msalConfig, loginRequest } from "./msalConfig";

interface AuthContextType {
    instance: IPublicClientApplication;
    account: any;
    isAuthenticated: boolean;
    login: () => Promise<void>;
    logout: () => void;
    getToken: () => Promise<string | null>;
}

const AuthContext = createContext<AuthContextType | null>(null);

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
    const [pca] = useState(() => new PublicClientApplication(msalConfig));
    const [account, setAccount] = useState<any>(null);
    const [initialized, setInitialized] = useState(false);

    useEffect(() => {
        const init = async () => {
            try {
                await pca.initialize();
                const accounts = pca.getAllAccounts();
                if (accounts.length > 0) {
                    pca.setActiveAccount(accounts[0]);
                    setAccount(accounts[0]);
                }
                setInitialized(true);
            } catch (error) {
                console.error("MSAL initialization failed", error);
            }
        };
        init();
    }, [pca]);

    const login = async () => {
        try {
            const result = await pca.loginPopup(loginRequest);
            pca.setActiveAccount(result.account);
            setAccount(result.account);
        } catch (error) {
            console.error("Login failed", error);
        }
    };

    const logout = () => {
        pca.logoutPopup();
        setAccount(null);
    };

    const getToken = async (): Promise<string | null> => {
        const activeAccount = pca.getActiveAccount();
        if (!activeAccount) return null;

        try {
            const result = await pca.acquireTokenSilent({
                ...loginRequest,
                account: activeAccount,
            });
            return result.accessToken;
        } catch (error) {
            console.warn("Silent token acquisition failed, attempting popup", error);
            try {
                const result = await pca.acquireTokenPopup(loginRequest);
                return result.accessToken;
            } catch (popupError) {
                console.error("Popup token acquisition failed", popupError);
                return null;
            }
        }
    };

    if (!initialized) {
        return <div>Loading authentication...</div>;
    }

    return (
        <AuthContext.Provider
            value={{
                instance: pca,
                account,
                isAuthenticated: !!account,
                login,
                logout,
                getToken,
            }}
        >
            {children}
        </AuthContext.Provider>
    );
};

export const useAuth = () => {
    const context = useContext(AuthContext);
    if (!context) {
        throw new Error("useAuth must be used within an AuthProvider");
    }
    return context;
};
