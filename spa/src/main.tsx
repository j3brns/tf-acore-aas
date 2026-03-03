import React from "react";
import ReactDOM from "react-dom/client";
import { PublicClientApplication, EventType, EventMessage, AuthenticationResult } from "@azure/msal-browser";
import { MsalProvider } from "@azure/msal-react";
import { msalConfig } from "./auth/msalConfig";
import App from "./App";
import "./index.css";

const msalInstance = new PublicClientApplication(msalConfig);

// Default to using the first account if no account is active on page load
if (!msalInstance.getActiveAccount() && msalInstance.getAllAccounts().length > 0) {
    // Account selection logic is app dependent. Custom logic can be implemented here.
    msalInstance.setActiveAccount(msalInstance.getAllAccounts()[0]);
}

msalInstance.addEventCallback((event: EventMessage) => {
    if (event.eventType === EventType.LOGIN_SUCCESS && event.payload) {
        const payload = event.payload as AuthenticationResult;
        const account = payload.account;
        msalInstance.setActiveAccount(account);
    }
});

ReactDOM.createRoot(document.getElementById("root")!).render(
    <React.StrictMode>
        <MsalProvider instance={msalInstance}>
            <App />
        </MsalProvider>
    </React.StrictMode>
);
