import type { NextConfig } from "next";

const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  // NVIDIA explanations can take 10-30s; the default proxy timeout is 30s.
  experimental: { proxyTimeout: 90_000 },
  // Browser calls /api/* on this origin; Next proxies them to FastAPI.
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${BACKEND_URL}/api/:path*` }];
  },
};

export default nextConfig;
