import type { NextConfig } from "next";

const BACKEND_URL = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  // NVIDIA explanations can take 10-30s; the default proxy timeout is 30s.
  experimental: { proxyTimeout: 90_000 },
  // Browser calls /api/* on this origin; Next proxies them to FastAPI. 127.0.0.1, not localhost: on Windows
  // "localhost" tries IPv6 first and costs ~200 ms per request against a backend listening on IPv4.
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${BACKEND_URL}/api/:path*` }];
  },
};

export default nextConfig;
