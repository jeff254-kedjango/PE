/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Base URL of the Weespas API that receives InSAR commercial-usage telemetry,
   *  e.g. http://localhost:8000/api/v1. Unset ⇒ telemetry is inert (see lib/telemetry.ts). */
  readonly VITE_WEESPAS_API?: string;
  /** Weespas login page an unauthenticated InSAR visitor is redirected to, e.g.
   *  http://localhost:5174/login. InSAR is free but login-required (see lib/access.ts).
   *  Its ORIGIN also backs the "Back to Weespas" breadcrumb (see lib/telemetry.ts).
   *  Defaults to the local Weespas dev frontend (:5174; the InSAR FE itself owns :5173). */
  readonly VITE_WEESPAS_LOGIN_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
