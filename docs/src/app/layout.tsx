import { Vazirmatn } from 'next/font/google';
import { Provider } from '@/components/provider';
import type { Metadata } from 'next';
import './global.css';

const vazirmatn = Vazirmatn({
  subsets: ['arabic', 'latin'],
  variable: '--font-vazirmatn',
});

export const metadata: Metadata = {
  metadataBase: new URL('https://mdjes.github.io/mdjeBot'),
  title: {
    default: 'MdjeBot',
    template: '%s | MdjeBot',
  },
  description: 'مستندات ربات فروش وی‌پی‌ان MdjeBot، ساخته و نگهداری‌شده توسط Mdje',
};

export default function Layout({ children }: LayoutProps<'/'>) {
  return (
    <html lang="fa" dir="rtl" className={vazirmatn.variable} suppressHydrationWarning>
      <body className={`${vazirmatn.className} flex min-h-screen flex-col antialiased`}>
        <Provider>{children}</Provider>
      </body>
    </html>
  );
}
