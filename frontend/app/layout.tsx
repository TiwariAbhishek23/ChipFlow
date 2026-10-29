import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "ChipFlow Agent",
  description: "Long-horizon multi-agent RTL design automation demo"
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body>{children}</body>
    </html>
  );
}

