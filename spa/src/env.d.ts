interface ImportMetaEnv {
  readonly VITE_ENTRA_CLIENT_ID: string;
  readonly VITE_ENTRA_AUTHORITY: string;
  readonly VITE_ENTRA_SCOPES: string;
  readonly VITE_API_BASE_URL: string;
  readonly VITE_ENTRA_REDIRECT_URI: string;
  readonly VITE_ENTRA_POST_LOGOUT_REDIRECT_URI: string;
  readonly VITE_ENVIRONMENT_NAME?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
