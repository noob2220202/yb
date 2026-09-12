import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "YB Arbitrage Scanner",
  description: "Cross-bookmaker arbitrage and value-edge dashboard",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
