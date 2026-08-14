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

export const metadata: Metadata = {
  title: "Grid — Free Open-Source PaaS for Ecosystem Deployment",
  description: "Grid is a free, open-source PaaS powered by Grid. Deploy apps, services, databases, workers, queues, SSL, backups, and multi-server infrastructure on your own VPS.",
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
import { OperationalMesh } from "@/components/effects/OperationalMesh";
import { Navbar } from "@/components/layout/Navbar";
import { Footer } from "@/components/layout/Footer";
import FloatingAILoader from "@/components/ai/FloatingAILoader";
import { CommandPalette } from "@/components/CommandPalette";

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
                  <OperationalMesh />
                  <CommandPalette />
                  <Navbar />
                  <main className="min-h-[calc(100vh-3.5rem)] flex flex-col">
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
