import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = { title: "Task Tools", description: "Tasks and tools console" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen antialiased">
        <header className="border-b border-line bg-surface">
          <nav className="mx-auto flex max-w-5xl items-center gap-6 px-4 py-3 text-sm">
            <span className="font-semibold tracking-tight">Task Tools</span>
            <Link href="/tasks" className="text-muted hover:text-fg">Tasks</Link>
            <Link href="/tools" className="text-muted hover:text-fg">Tools</Link>
          </nav>
        </header>
        <main className="mx-auto max-w-5xl px-4 py-6">{children}</main>
      </body>
    </html>
  );
}
