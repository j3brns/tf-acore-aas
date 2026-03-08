import React from "react";
import { Link, useLocation } from "react-router-dom";
import { useAuth } from "../auth/useAuth";

export const Layout: React.FC<{ children: React.ReactNode }> = ({ children }) => {
    const { account, logout, isAuthenticated } = useAuth();
    const location = useLocation();

    const navItems = [
        { name: "Catalogue", path: "/" },
        { name: "Tenant Portal", path: "/tenant" },
        { name: "Sessions", path: "/sessions" },
        { name: "Admin", path: "/admin", adminOnly: true },
    ];

    const isAdmin = account?.idTokenClaims?.roles?.some((role: string) => 
        role === "Platform.Admin" || role === "Platform.Operator"
    );

    return (
        <div className="min-h-screen bg-gray-50 flex flex-col">
            <header className="bg-white border-b border-gray-200">
                <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <div className="flex justify-between h-16 items-center">
                        <div className="flex items-center">
                            <span className="text-xl font-bold text-blue-600 mr-8">Agent Platform</span>
                            <nav className="hidden md:flex space-x-8">
                                {navItems.map((item) => (
                                    (!item.adminOnly || isAdmin) && (
                                        <Link
                                            key={item.path}
                                            to={item.path}
                                            className={`${
                                                location.pathname === item.path
                                                    ? "border-blue-500 text-gray-900"
                                                    : "border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300"
                                            } inline-flex items-center px-1 pt-1 border-b-2 text-sm font-medium`}
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
                                    <span className="text-sm text-gray-600">{account.name}</span>
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
            <main className="flex-1 max-w-7xl w-full mx-auto px-4 sm:px-6 lg:px-8 py-8">
                {children}
            </main>
            <footer className="bg-white border-t border-gray-200 py-4">
                <div className="max-w-7xl mx-auto px-4 text-center text-sm text-gray-500">
                    &copy; 2026 Agent Platform. All rights reserved.
                </div>
            </footer>
        </div>
    );
};
