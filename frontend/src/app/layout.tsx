import { SpaceOpsProvider } from "@/context/SpaceOpsContext";
import type { Metadata, Viewport } from "next";
import "./globals.css";

import { ThemeProvider } from "@/components/theme-provider";
import { AuthProvider } from "@/components/auth-provider";
import { Toaster } from "@/components/ui/toaster";
import { ConfirmProvider } from "@/components/ui/confirm-dialog";
import { TierProvider } from "@/context/TierContext";
import { PoweredByBadge } from "@/components/licensing/PoweredByBadge";
import { LazyMotion, domAnimation } from "framer-motion";

const siteUrl = (() => {
  const raw = (process.env.NEXT_PUBLIC_APP_URL || "https://grid.trulay.co").trim();
  try {
    return new URL(raw);
  } catch {
    return new URL("https://grid.trulay.co");
  }
})();

export const metadata: Metadata = {
  metadataBase: siteUrl,
  title: {
    default: "Trulay Grid — Free Open-Source Sovereign PaaS",
    template: "%s — Grid",
  },
  description:
    "Deploy apps, PostgreSQL HA, Redis Sentinel, workers, and WireGuard multi-server infrastructure on your own VPS with Trulay Grid—MIT open-source PaaS.",
  alternates: {
    canonical: "/",
  },
  openGraph: {
    type: "website",
    siteName: "Trulay Grid",
    title: "Trulay Grid — Free Open-Source Sovereign PaaS",
    description:
      "Deploy apps, databases, workers, and multi-server WireGuard infrastructure on your own VPS with full ownership and zero vendor lock-in.",
    url: "/",
  },
  twitter: {
    card: "summary_large_image",
    title: "Trulay Grid — Free Open-Source Sovereign PaaS",
    description:
      "Self-hosted open-source PaaS for deploying full-stack infrastructure on any VPS.",
  },
  icons: {
    icon: "/images/logo.svg",
    apple: "/images/logo.svg",
  },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#0a0c10",
};

import { LazyMount } from "@/components/LazyMount";
import { ThreeCompat } from "@/components/three-compat";
import { ReactQueryProvider } from "@/components/react-query-provider";
import { AppBackground } from "@/components/effects/AppBackground";
import { Navbar } from "@/components/layout/Navbar";
import { Footer } from "@/components/layout/Footer";
import FloatingAILoader from "@/components/ai/FloatingAILoader";
import { CommandPalette } from "@/components/CommandPalette";
import { TelemetryProvider } from "@/components/telemetry/TelemetryProvider";

// FloatingAI ships in a deferred chunk so it never blocks first paint.
// ``next/dynamic({ ssr: false })`` is not allowed in Server Components in
// Next.js 15, so the actual ``dynamic()`` call lives in the Client
// Component ``components/ai/FloatingAILoader.tsx`` and we render it here
// inside the existing ``<LazyMount>`` idle-callback boundary.

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className="font-sans antialiased premium-bg">
        <ReactQueryProvider>
        <ThemeProvider
            attribute="class"
            defaultTheme="dark"
            enableSystem
            disableTransitionOnChange
          >
            <AuthProvider>
              <LazyMotion features={domAnimation}>
              <TierProvider>
                <ConfirmProvider>
                  <SpaceOpsProvider>
                  <TelemetryProvider>
                  <a
                    href="#main-content"
                    className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-[100] focus:rounded-lg focus:bg-primary focus:px-4 focus:py-2 focus:text-sm focus:font-semibold focus:text-primary-foreground focus:shadow-lg focus:outline-none focus:ring-2 focus:ring-ring"
                  >
                    Skip to main content
                  </a>
                  <AppBackground />
                  <CommandPalette />
                  <Navbar />
                  <main id="main-content" tabIndex={-1} className="min-h-[calc(100vh-3.5rem)] flex flex-col focus:outline-none">
                    <div className="flex-1">
                      {children}
                    </div>
                    <Footer />
                  </main>
                  <LazyMount>
                    <FloatingAILoader />
                  </LazyMount>
                  <ThreeCompat />
                  <PoweredByBadge />
                  <Toaster />
                  </TelemetryProvider>
                  </SpaceOpsProvider>
                </ConfirmProvider>
              </TierProvider>
              </LazyMotion>
            </AuthProvider>
        </ThemeProvider>
        </ReactQueryProvider>
      </body>
    </html>
  );
}
