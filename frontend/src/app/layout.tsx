import type { Metadata } from "next";
import "./globals.css";
import { ThemeProvider } from "@/components/theme-provider";
import { AuthProvider } from "@/components/auth-provider";
import { Toaster } from "@/components/ui/toaster";

export const metadata: Metadata = {
  title: "SMSly Hosting",
  description: "Next-gen hosting platform",
};

import { FloatingAI } from "@/components/ai/FloatingAI";

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body>
        <ThemeProvider
            attribute="class"
            defaultTheme="system"
            enableSystem
            disableTransitionOnChange
          >
            <AuthProvider>
                {children}
                <FloatingAI />
                <Toaster />
            </AuthProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
