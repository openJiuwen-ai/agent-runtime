/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE?: string;
  readonly VITE_IDP_BASE?: string;
  readonly VITE_PRODUCT_NAME?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

interface Window {
  /** Deploy-time flag injected into index.html (WORKSPACE_QUOTA_ENABLED). */
  __WORKSPACE_QUOTA_ENABLED__?: boolean | string;
  /** Deploy-time flag injected into index.html (SKILL_UPLOAD_APPROVAL_ENABLED). */
  __SKILL_UPLOAD_APPROVAL_ENABLED__?: boolean | string;
}
