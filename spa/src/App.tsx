import { BrowserRouter as Router, Routes, Route, Navigate } from "react-router-dom";
import { Layout } from "./components/Layout";
import { AgentCataloguePage } from "./pages/AgentCataloguePage";
import { InvokePage } from "./pages/InvokePage";
import { SessionsPage } from "./pages/SessionsPage";
import { AdminPage } from "./pages/AdminPage";
import { useAuth } from "./auth/useAuth";

function App() {
  const { isAuthenticated, login } = useAuth();

  if (!isAuthenticated) {
    return (
      <div className="min-h-screen bg-gray-100 flex flex-col items-center justify-center p-4">
        <div className="bg-white p-8 rounded-lg shadow-md max-w-md w-full text-center">
          <h1 className="text-3xl font-bold text-blue-600 mb-6">Agent Platform</h1>
          <p className="text-gray-600 mb-8">Please sign in with your Entra ID to access the platform.</p>
          <button
            onClick={login}
            className="w-full inline-flex justify-center py-3 px-4 border border-transparent shadow-sm text-sm font-medium rounded-md text-white bg-blue-600 hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500"
          >
            Sign In
          </button>
        </div>
      </div>
    );
  }

  return (
    <Router>
      <Layout>
        <Routes>
          <Route path="/" element={<AgentCataloguePage />} />
          <Route path="/invoke/:agentName" element={<InvokePage />} />
          <Route path="/sessions" element={<SessionsPage />} />
          <Route path="/admin" element={<AdminPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Layout>
    </Router>
  );
}

export default App;
