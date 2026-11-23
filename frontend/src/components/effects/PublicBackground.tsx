"use client";

/**
 * PublicBackground — Lightweight CSS-only radial glow overlay for public routes.
 * Renders cleanly above body.premium-bg without mounting the interactive canvas.
 */

const PUBLIC_ROUTES = [
  "/",
  "/pricing",
  "/compare",
  "/contact",
  "/docs",
  "/legal",
  "/status",
  "/notice",
  "/get-started",
  "/store",
  "/templates",
  "/login",
  "/register",
  "/logout",
  "/forgot-password",
];

export function isPublicRoute(pathname: string): boolean {
  if (!pathname) return true;
  if (pathname === "/") return true;
  return PUBLIC_ROUTES.some(
    (route) => pathname === route || pathname.startsWith(route + "/"),
  );
}

export function PublicBackground() {
  return (
    <div
      className="fixed inset-0 pointer-events-none"
      style={{
        zIndex: 0,
        backgroundImage:
          "radial-gradient(circle at 50% 0%, rgba(16, 185, 129, 0.05), transparent 55%), radial-gradient(circle at 80% 80%, rgba(59, 130, 246, 0.03), transparent 45%)",
      }}
      aria-hidden="true"
    />
  );
}
