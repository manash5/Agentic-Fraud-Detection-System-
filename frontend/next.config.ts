import type { NextConfig } from "next";

// The client always calls same-origin `/api/*`; the Next server rewrites those
// to the backend. Locally that's 127.0.0.1:8000; inside Docker the compose
// build passes BACKEND_INTERNAL_URL=http://backend:8000 (the service name), so
// the proxy target resolves on the internal network — never localhost.
const backendUrl =
  process.env.BACKEND_INTERNAL_URL ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  // Emit a self-contained server bundle (.next/standalone) so the runtime image
  // ships only what's needed to `node server.js` — no full node_modules.
  output: "standalone",
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${backendUrl}/:path*`,
      },
    ];
  },
};

export default nextConfig;
