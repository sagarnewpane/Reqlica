import { Html, Head, Main, NextScript } from 'next/document';
import Script from 'next/script';
import { themeBootstrap } from '@/lib/theme';

export default function Document() {
  return (
    <Html lang="en">
      <Head>
        <Script
          id="reqlica-theme"
          strategy="beforeInteractive"
          dangerouslySetInnerHTML={{ __html: themeBootstrap }}
        />
      </Head>
      <body className="antialiased">
        <Main />
        <NextScript />
      </body>
    </Html>
  );
}
