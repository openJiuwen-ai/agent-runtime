interface ImportMetaEnv {
  readonly VITE_API_BASE?: string;
  readonly VITE_IDP_BASE?: string;
  readonly VITE_CHAT_BASE_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
