import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import "./globals.css";

const inter = Inter({ subsets: ["latin"], variable: "--font-sans", display: "swap" });
const mono = JetBrains_Mono({ subsets: ["latin"], variable: "--font-mono", display: "swap" });
import { ThemeProvider } from "@/components/theme-provider";
import { AuthProvider } from "@/components/auth-provider";
import { Toaster } from "@/components/ui/toaster";
import { ConfirmProvider } from "@/components/ui/confirm-dialog";

export const metadata: Metadata = {
  title: "CloudNeuron - The Intelligent Cloud Platform",
  description: "Deploy apps in seconds. Scale globally with AI. Experience zero-config deployments and true multi-cloud freedom.",
  icons: {
    icon: "/images/logo.svg",
  },
};

import { FloatingAI } from "@/components/ai/FloatingAI";
import { GlobalBackground } from "@/components/effects/GlobalBackground";
import { Navbar } from "@/components/layout/Navbar";

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className={`${inter.variable} ${mono.variable} font-sans antialiased cloud-bg`}>
        <ThemeProvider
            attribute="class"
            defaultTheme="system"
            enableSystem
            disableTransitionOnChange
          >
            <AuthProvider>
              <ConfirmProvider>
                <GlobalBackground />
                <Navbar />
                {children}
                <FloatingAI />
                <Toaster />
              </ConfirmProvider>
            </AuthProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
