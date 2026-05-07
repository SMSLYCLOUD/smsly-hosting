import { SpaceOpsProvider } from "@/context/SpaceOpsContext";
import type { Metadata } from "next";
import "./globals.css";

import { ThemeProvider } from "@/components/theme-provider";
import { AuthProvider } from "@/components/auth-provider";
import { Toaster } from "@/components/ui/toaster";
import { ConfirmProvider } from "@/components/ui/confirm-dialog";
import { TierProvider } from "@/context/TierContext";
import { PoweredByBadge } from "@/components/licensing/PoweredByBadge";

export const metadata: Metadata = {
  title: "Grid — Free Open-Source PaaS for Ecosystem Deployment",
  description: "Grid is a free, open-source PaaS powered by CloudNeuron. Deploy apps, services, databases, workers, queues, SSL, backups, and multi-server infrastructure on your own VPS.",
  icons: {
    icon: "/images/mini_logo.png",
    apple: "/images/mini_logo.png",
  },
};

import { FloatingAI } from "@/components/ai/FloatingAI";
import { SpaceOpsBackground } from "@/components/effects/SpaceOpsBackground";
import { Navbar } from "@/components/layout/Navbar";
import { Footer } from "@/components/layout/Footer";

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className="font-sans antialiased cloud-bg">
        <ThemeProvider
            attribute="class"
            defaultTheme="system"
            enableSystem
            disableTransitionOnChange
          >
            <AuthProvider>
              <TierProvider>
                <ConfirmProvider>
                  <SpaceOpsProvider>
                  <SpaceOpsBackground />
                  <Navbar />
                  <main className="min-h-[calc(100vh-3.5rem)] flex flex-col">
                    <div className="flex-1">
                      {children}
                    </div>
                    <Footer />
                  </main>
                  <FloatingAI />
                  <PoweredByBadge />
                  <Toaster />
                  </SpaceOpsProvider>
                </ConfirmProvider>
              </TierProvider>
            </AuthProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
