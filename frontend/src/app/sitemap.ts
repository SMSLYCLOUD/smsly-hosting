import type { MetadataRoute } from "next";

function getSiteOrigin(): string {
  const raw = (process.env.NEXT_PUBLIC_APP_URL || "https://grid.trulay.co").trim();
  try {
    return new URL(raw).origin;
  } catch {
    return "https://grid.trulay.co";
  }
}

const PUBLIC_ROUTES = [
  "",
  "/pricing",
  "/compare",
  "/contact",
  "/store",
  "/status",
  "/get-started",
  "/legal/privacy",
  "/legal/terms",
  "/docs",
  "/docs/install",
  "/docs/getting-started",
  "/docs/github-app",
  "/docs/migration",
  "/docs/migration/from-vercel",
  "/docs/migration/from-railway",
  "/docs/migration/from-heroku",
  "/docs/migration/from-render",
  "/docs/cli",
  "/docs/api",
  "/docs/transfers",
  "/docs/multi-server",
  "/docs/deployments",
  "/docs/ai",
  "/docs/intelligence",
  "/docs/functions",
  "/docs/autoscaling",
  "/docs/addons",
  "/docs/security",
  "/docs/changelog",
];

export default function sitemap(): MetadataRoute.Sitemap {
  const origin = getSiteOrigin();
  const lastModified = new Date();
  return PUBLIC_ROUTES.map((route) => ({
    url: `${origin}${route || "/"}`,
    lastModified,
    changeFrequency: route === "" || route === "/store" ? "weekly" : "monthly",
    priority: route === "" ? 1.0 : route.startsWith("/docs") ? 0.7 : 0.8,
  }));
}
