import "./globals.css";
import type { ReactNode } from "react";

export const metadata = { title: "Knowledge Assistant", description: "Ask questions across Confluence and Jira" };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
