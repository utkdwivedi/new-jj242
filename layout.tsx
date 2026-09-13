import type { Metadata } from 'next';
import LenisProvider from '@/components/ui/LenisProvider';
import '@/styles/globals.css';

export const metadata: Metadata = {
  title: 'Portfolio — Software Engineer',
  description:
    'portfolio showcasing precision-engineered digital experiences. Every pixel measured. Every frame rendered at speed.',
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link
          rel="preconnect"
          href="https://fonts.gstatic.com"
          crossOrigin=""
        />
      </head>
      <body>
        <LenisProvider>
          {/* Global Noise Overlay */}
          <div className="noise-overlay" aria-hidden="true" />
          {children}
        </LenisProvider>
      </body>
    </html>
  );
}
