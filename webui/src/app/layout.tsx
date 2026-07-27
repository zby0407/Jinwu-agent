import type { Metadata, Viewport } from "next";
import { NuqsAdapter } from "nuqs/adapters/next/app";
import { ThemedToaster } from "@/providers/ThemeProvider";
import "katex/dist/katex.min.css";
import "./globals.css";

export const metadata: Metadata = {
  title: "金乌科研助手",
  description:
    "金乌科研助手 WebUI — 基于 DeepAgents/LangGraph 的自主科研智能体界面。",
  icons: {
    icon: [
      {
        url: "/jinwu-favicon-v2.png?v=2",
        type: "image/png",
        sizes: "256x256",
      },
    ],
    shortcut: "/jinwu-favicon-v2.png?v=2",
    apple: "/jinwu-favicon-v2.png?v=2",
  },
};

export const viewport: Viewport = {
  themeColor: "#23211d",
  colorScheme: "dark",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html
      lang="zh-CN"
      className="dark"
      style={{ colorScheme: "dark" }}
    >
      <body className="font-sans">
        <NuqsAdapter>
          {children}
          <ThemedToaster />
        </NuqsAdapter>
      </body>
    </html>
  );
}
