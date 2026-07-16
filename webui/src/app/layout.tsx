import type { Metadata, Viewport } from "next";
import { NuqsAdapter } from "nuqs/adapters/next/app";
import { ThemeProvider, ThemedToaster } from "@/providers/ThemeProvider";
import "katex/dist/katex.min.css";
import "./globals.css";

export const metadata: Metadata = {
  title: "\u91d1\u4e4c\u79d1\u7814\u52a9\u624b",
  description:
    "\u91d1\u4e4c\u79d1\u7814\u52a9\u624b WebUI - \u57fa\u4e8e DeepAgents/LangGraph \u7684\u81ea\u4e3b\u79d1\u7814\u667a\u80fd\u4f53\u754c\u9762\u3002",
};

export const viewport: Viewport = {
  themeColor: "#212121",
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
      suppressHydrationWarning
    >
      <body
        className="font-sans"
        suppressHydrationWarning
      >
        <NuqsAdapter>
          <ThemeProvider>
            {children}
            <ThemedToaster />
          </ThemeProvider>
        </NuqsAdapter>
      </body>
    </html>
  );
}
