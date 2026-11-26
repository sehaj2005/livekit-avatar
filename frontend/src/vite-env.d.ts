/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_URL?: string;
  readonly VITE_GUEST_JOIN_URL?: string;
  readonly VITE_HOSTED_AUTH?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
