import '@/styles/colors.css';
import '@/styles/globals.css';
import { SidebarProvider } from '@/components/ui/shadcn/sidebar';
import { useEffect, useState } from 'react';
import Head from 'next/head';
import type { AppProps } from 'next/app';
import { applyTheme } from '@/lib/theme';

export default function App({ Component, pageProps }: AppProps) {
  const [themeColor, setThemeColor] = useState('#f7faf8');
  useEffect(() => {
    const media = matchMedia('(prefers-color-scheme: dark)');
    const apply = () => setThemeColor(applyTheme());
    apply();
    media.addEventListener('change', apply);
    window.addEventListener('storage', apply);
    window.addEventListener('reqlica:theme', apply);
    return () => {
      media.removeEventListener('change', apply);
      window.removeEventListener('storage', apply);
      window.removeEventListener('reqlica:theme', apply);
    };
  }, []);
  return (
    <>
      <Head>
        <title>Reqlica · Local API workspace</title>
        <meta name="application-name" content="Reqlica" />
        <meta
          name="description"
          content="Build, test, and inspect local mock APIs with Reqlica."
        />
        <meta name="theme-color" content={themeColor} />
        <link rel="icon" href="/favicon.ico" sizes="16x16 32x32 48x48" />
        <link rel="icon" href="/favicon.svg" type="image/svg+xml" />
        <link
          rel="apple-touch-icon"
          href="/apple-touch-icon.png"
          sizes="180x180"
        />
        <link rel="manifest" href="/site.webmanifest" />
      </Head>
      <SidebarProvider>
        <Component {...pageProps} />
      </SidebarProvider>
    </>
  );
}
