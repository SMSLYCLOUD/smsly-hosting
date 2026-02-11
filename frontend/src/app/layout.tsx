import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import "./globals.css";

const inter = Inter({ subsets: ["latin"], variable: "--font-sans", display: "swap" });
const mono = JetBrains_Mono({ subsets: ["latin"], variable: "--font-mono", display: "swap" });
import { ThemeProvider } from "@/components/theme-provider";
import { AuthProvider } from "@/components/auth-provider";
import { Toaster } from "@/components/ui/toaster";

export const metadata: Metadata = {
  title: "CloudNeuron",
  description: "Next-gen hosting platform",
};

import { FloatingAI } from "@/components/ai/FloatingAI";
import { GlobalBackground } from "@/components/effects/GlobalBackground";

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
                <GlobalBackground />
                {children}
                <FloatingAI />
                <Toaster />
            </AuthProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}

