/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,

  // The browser calls the API via RELATIVE URLs (same origin), and the dev
  // server proxies /api/* to the FastAPI backend inside the sandbox. This:
  //   * avoids any CORS / cross-host dependency in the preview environment,
  //   * streams request bodies (Next 14.2 dev rewrite proxy pipes the raw
  //     stream — there is no 10 MB body limit on rewrites),
  //   * keeps large uploads chunked (each chunk is still a bounded request).
  //
  // When the frontend is deployed separately with a directly-reachable
  // backend, set NEXT_PUBLIC_API_BASE to the backend origin instead — the
  // browser then talks to the backend directly and this proxy is unused.
  async rewrites() {
    const backend =
      process.env.NEXT_PUBLIC_API_BASE?.replace(/\/$/, "") ||
      "http://127.0.0.1:8000";
    return [
      {
        source: "/api/:path*",
        destination: `${backend}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
