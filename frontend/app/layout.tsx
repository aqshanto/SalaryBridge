import type { Metadata } from "next";
import { Geist, Geist_Mono, Noto_Sans_Bengali } from "next/font/google";

import { Header } from "@/components/Header";
import { LedgerPanel } from "@/components/LedgerPanel";
import { SimProvider } from "@/components/SimProvider";
import { SimulatorBar } from "@/components/SimulatorBar";

import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });
const bengali = Noto_Sans_Bengali({ variable: "--font-bengali", subsets: ["bengali"] });

export const metadata: Metadata = {
  title: "SalaryBridge",
  description: "Earned-wage advance prototype for upay (synthetic data)",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${geistSans.variable} ${geistMono.variable} ${bengali.variable} h-full antialiased`}>
      <body className="min-h-full font-sans">
        <SimProvider>
          <Header />
          <SimulatorBar />
          <div className="mx-auto grid max-w-7xl gap-6 px-4 py-6 lg:grid-cols-[1fr_320px]">
            <main className="min-w-0">{children}</main>
            <LedgerPanel />
          </div>
        </SimProvider>
      </body>
    </html>
  );
}
