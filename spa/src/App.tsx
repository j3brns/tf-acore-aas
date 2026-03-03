import { AuthenticatedTemplate, UnauthenticatedTemplate, useMsal } from "@azure/msal-react";
import { loginRequest } from "./auth/msalConfig";

function App() {
  const { instance } = useMsal();

  const handleLogin = () => {
    instance.loginPopup(loginRequest).catch((e) => {
      console.error(e);
    });
  };

  const handleLogout = () => {
    instance.logoutPopup().catch((e) => {
      console.error(e);
    });
  };

  return (
    <div className="min-h-screen bg-background flex flex-col items-center justify-center p-4">
      <h1 className="text-4xl font-bold mb-8">Platform SPA</h1>
      
      <AuthenticatedTemplate>
        <div className="text-center">
          <p className="mb-4">You are authenticated!</p>
          <button 
            onClick={handleLogout}
            className="px-4 py-2 bg-primary text-primary-foreground rounded-md hover:opacity-90 transition-opacity"
          >
            Logout
          </button>
        </div>
      </AuthenticatedTemplate>

      <UnauthenticatedTemplate>
        <div className="text-center">
          <p className="mb-4">Please log in to continue.</p>
          <button 
            onClick={handleLogin}
            className="px-4 py-2 bg-primary text-primary-foreground rounded-md hover:opacity-90 transition-opacity"
          >
            Login
          </button>
        </div>
      </UnauthenticatedTemplate>
    </div>
  );
}

export default App;
