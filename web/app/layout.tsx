import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'MiniFab MES',
  description: 'Simulated semiconductor fab and MES dashboard'
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
