import type { NextConfig } from "next";

// Operator configuration only. Browser input never selects a proxy destination.
let apiOrigin: URL;
try {
  apiOrigin = new URL(
    process.env.RISKLAB_API_ORIGIN ?? "http://127.0.0.1:8000",
  );
} catch {
  throw new Error("RISKLAB_API_ORIGIN must be a valid HTTP(S) origin");
}
if (
  !["http:", "https:"].includes(apiOrigin.protocol) ||
  apiOrigin.username ||
  apiOrigin.password ||
  apiOrigin.pathname !== "/" ||
  apiOrigin.search ||
  apiOrigin.hash
) {
  throw new Error(
    "RISKLAB_API_ORIGIN must be an HTTP(S) origin without credentials or a path",
  );
}

const config: NextConfig = {
  poweredByHeader: false,
  output: "standalone",
  async rewrites() {
    return [
      {
        source: "/api/v1/:path*",
        destination: `${apiOrigin.origin}/api/v1/:path*`,
      },
    ];
  },
};
export default config;
