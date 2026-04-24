import type { Metadata } from 'next';
import { Geist, Geist_Mono } from 'next/font/google';
import { Toaster } from 'sonner';

import { Sidebar } from '@/components/sidebar';

import './globals.css';

const geistSans = Geist({ variable: '--font-geist-sans', subsets: ['latin'] });
const geistMono = Geist_Mono({ variable: '--font-geist-mono', subsets: ['latin'] });

export const metadata: Metadata = {
  title: 'LinkedIn Analyzer',
  description: 'Control panel for the LinkedIn post analyzer',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${geistSans.variable} ${geistMono.variable}`}>
      <body className="min-h-screen">
        <div className="flex min-h-screen">
          <Sidebar />
          <main className="flex-1 px-8 py-10 min-w-0">
            <div className="max-w-6xl mx-auto">{children}</div>
          </main>
        </div>
        <Toaster
          position="bottom-right"
          theme="dark"
          toastOptions={{
            style: {
              background: 'var(--color-panel-2)',
              border: '1px solid var(--color-border)',
              color: 'var(--color-text)',
            },
          }}
        />
      </body>
    </html>
  );
}
