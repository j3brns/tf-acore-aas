import React, { useEffect } from "react";
import { Link, useLocation } from "react-router-dom";
import { useAuth } from "../auth/useAuth";
import { getApiClient } from "../api/client";

export const Layout: React.FC<{ children: React.ReactNode }> = ({ children }) => {
    const { account, logout, isAuthenticated, getAccessToken } = useAuth();
    const location = useLocation();

    // Initialize ApiClient globally
    useEffect(() => {
        if (isAuthenticated) {
            getApiClient(getAccessToken);
        }
    }, [isAuthenticated, getAccessToken]);

    const navItems = [
        { name: "Catalogue", path: "/" },
        { name: "Tenant Portal", path: "/tenant" },
        { name: "Sessions", path: "/sessions" },
        { name: "Admin", path: "/admin", adminOnly: true },
    ];

    const tenantNavItems = [
        { name: "Dashboard", path: "/tenant" },
        { name: "API Keys", path: "/tenant/api-keys" },
        { name: "Members", path: "/tenant/members" },
        { name: "Webhooks", path: "/tenant/webhooks" },
        { name: "Audit Exports", path: "/tenant/audit" },
        { name: "Settings", path: "/tenant/settings" },
    ];

    const isAdmin = account?.idTokenClaims?.roles?.some((role: string) => 
        role === "Platform.Admin" || role === "Platform.Operator"
    );

    const isTenantRoute = location.pathname.startsWith("/tenant");

    return (
        <div className="min-h-screen bg-gray-50 flex flex-col">
            <header className="bg-white border-b border-gray-200 sticky top-0 z-30">
                <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <div className="flex justify-between h-16 items-center">
                        <div className="flex items-center">
                            <Link to="/" className="text-xl font-bold text-blue-600 mr-8">Agent Platform</Link>
                            <nav className="hidden md:flex space-x-8">
                                {navItems.map((item) => (
                                    (!item.adminOnly || isAdmin) && (
                                        <Link
                                            key={item.path}
                                            to={item.path}
                                            className={`${
                                                (location.pathname === item.path || (item.path === "/tenant" && isTenantRoute))
                                                    ? "border-blue-500 text-gray-900"
                                                    : "border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300"
                                            } inline-flex items-center px-1 pt-1 border-b-2 text-sm font-medium h-16`}
                                        >
                                            {item.name}
                                        </Link>
                                    )
                                ))}
                            </nav>
                        </div>
                        <div className="flex items-center space-x-4">
                            {isAuthenticated && account ? (
                                <>
                                    <span className="text-sm text-gray-600 hidden sm:inline">{account.name}</span>
                                    <button
                                        onClick={logout}
                                        className="text-sm font-medium text-gray-700 hover:text-blue-600"
                                    >
                                        Logout
                                    </button>
                                </>
                            ) : (
                                <button className="text-sm font-medium text-blue-600">Login</button>
                            )}
                        </div>
                    </div>
                </div>
            </header>

            <div className={`flex-1 flex flex-col md:flex-row ${isTenantRoute ? '' : 'max-w-7xl w-full mx-auto px-4 sm:px-6 lg:px-8'}`}>
                {isTenantRoute && (
                    <aside className="w-full md:w-64 bg-white border-b md:border-b-0 md:border-r border-gray-200">
                        <nav className="p-4 space-y-1">
                            <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-4 px-3">
                                Tenant Self-Service
                            </p>
                            {tenantNavItems.map((item) => (
                                <Link
                                    key={item.path}
                                    to={item.path}
                                    className={`${
                                        location.pathname === item.path
                                            ? "bg-blue-50 text-blue-700"
                                            : "text-gray-600 hover:bg-gray-50 hover:text-gray-900"
                                    } group flex items-center px-3 py-2 text-sm font-medium rounded-md`}
                                >
                                    {item.name}
                                </Link>
                            ))}
                        </nav>
                    </aside>
                )}
                
                <main className={`flex-1 py-8 ${isTenantRoute ? 'px-4 sm:px-6 lg:px-8' : ''}`}>
                    {children}
                </main>
            </div>

            <footer className="bg-white border-t border-gray-200 py-4 mt-auto">
                <div className="max-w-7xl mx-auto px-4 text-center text-sm text-gray-500">
                    &copy; 2026 Agent Platform. All rights reserved.
                </div>
            </footer>
        </div>
    );
};
