import '@/styles/colors.css';
import '@/styles/globals.css';
import { SidebarProvider } from '@/components/ui/shadcn/sidebar';
import { useEffect } from 'react';
import type { AppProps } from 'next/app';

export default function App({ Component, pageProps }: AppProps) {
  useEffect(() => {
    const media = matchMedia('(prefers-color-scheme: dark)');
    const apply = () => {
      let theme = 'system';
      try {
        theme = localStorage.getItem('reqlica:theme') || 'system';
      } catch {}
      document.documentElement.classList.toggle(
        'dark',
        theme === 'dark' || (theme === 'system' && media.matches),
      );
    };
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
    <SidebarProvider>
      <Component {...pageProps} />
    </SidebarProvider>
  );
}
