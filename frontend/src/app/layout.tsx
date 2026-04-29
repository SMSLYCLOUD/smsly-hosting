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
  title: "CloudNeuron - The Intelligent Cloud Platform",
  description: "Deploy apps in seconds. Scale globally with AI. Experience zero-config deployments and true multi-cloud freedom.",
  icons: {
    icon: "/images/logo.svg",
  },
};

import { FloatingAI } from "@/components/ai/FloatingAI";
import { SpaceOpsBackground } from "@/components/effects/SpaceOpsBackground";
import { Navbar } from "@/components/layout/Navbar";

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
                  {children}
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
