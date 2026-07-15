"use client";

import React from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeRaw from "rehype-raw";
import rehypeKatex from "rehype-katex";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";
import { cn } from "@/lib/utils";
import { CodeBlock } from "./CodeBlock";
import { MermaidDiagram } from "./MermaidDiagram";
import {
  detectFileLink,
  dispatchFileLink,
  FILE_LINK_HREF_PREFIX,
  rehypePathLinks,
} from "@/lib/fileLink";

interface MarkdownContentProps {
  content: string;
  className?: string;
  /** When true, defer expensive renders (e.g. mermaid) until streaming ends. */
  isStreaming?: boolean;
}

const sanitizeSchema = {
  ...defaultSchema,
  attributes: {
    ...defaultSchema.attributes,
    code: [
      ...(defaultSchema.attributes?.code || []),
      ["className", "math-inline", "math-display"],
    ],
  },
};

export const MarkdownContent = React.memo<MarkdownContentProps>(
  ({ content, className = "", isStreaming = false }) => {
    return (
      <div
        className={cn(
          "prose min-w-0 max-w-full overflow-hidden break-words text-sm leading-relaxed text-inherit [&_h1:first-child]:mt-0 [&_h1]:mb-4 [&_h1]:mt-6 [&_h1]:font-semibold [&_h2:first-child]:mt-0 [&_h2]:mb-4 [&_h2]:mt-6 [&_h2]:font-semibold [&_h3:first-child]:mt-0 [&_h3]:mb-4 [&_h3]:mt-6 [&_h3]:font-semibold [&_h4:first-child]:mt-0 [&_h4]:mb-4 [&_h4]:mt-6 [&_h4]:font-semibold [&_h5:first-child]:mt-0 [&_h5]:mb-4 [&_h5]:mt-6 [&_h5]:font-semibold [&_h6:first-child]:mt-0 [&_h6]:mb-4 [&_h6]:mt-6 [&_h6]:font-semibold [&_p:last-child]:mb-0 [&_p]:mb-4",
          className
        )}
      >
        <ReactMarkdown
          remarkPlugins={[remarkGfm, remarkMath]}
          rehypePlugins={[
            rehypeRaw,
            rehypePathLinks,
            [rehypeSanitize, sanitizeSchema],
            [rehypeKatex, { throwOnError: false, strict: false }],
          ]}
          components={{
            code({
              className,
              children,
              ...props
            }: {
              className?: string;
              children?: React.ReactNode;
            }) {
              const match = /language-(\w+)/.exec(className || "");
              const raw = String(children);
              // Fenced blocks always end with \n; inline code does not. Use
              // this to distinguish unlanguaged fenced blocks from inline code
              // so detectFileLink is only called for true inline code.
              const isBlock = raw.endsWith("\n");
              const code = raw.replace(/\n$/, "");
              // react-markdown v9 dropped the `inline` prop. Block code carries
              // a `language-*` className from the fenced ```lang form; inline
              // code does not. Treat absence of a language match as inline.
              if (match?.[1] === "mermaid") {
                return (
                  <MermaidDiagram
                    code={code}
                    isStreaming={isStreaming}
                  />
                );
              }
              if (match) {
                return (
                  <CodeBlock
                    language={match[1]}
                    value={code}
                  />
                );
              }
              // Inline code that names a workspace or memory file becomes a
              // click-to-open link. ChatInterface (or any ancestor) listens
              // for `evosci:open-file` and routes to the right dialog.
              const link = !isBlock ? detectFileLink(code) : null;
              if (link) {
                return (
                  <button
                    type="button"
                    onClick={() => dispatchFileLink(link)}
                    title={`Open ${link.display}`}
                    className="rounded-sm bg-[var(--color-surface)] px-1 py-0.5 font-mono text-[0.9em] text-primary underline underline-offset-2 hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    {link.display}
                  </button>
                );
              }
              return (
                <code
                  className="rounded-sm bg-[var(--color-surface)] px-1 py-0.5 font-mono text-[0.9em]"
                  {...props}
                >
                  {children}
                </code>
              );
            },
            pre({ children }: { children?: React.ReactNode }) {
              return (
                <div className="my-4 max-w-full overflow-hidden last:mb-0">
                  {children}
                </div>
              );
            },
            a({
              href,
              children,
            }: {
              href?: string;
              children?: React.ReactNode;
            }) {
              // Bare-text path matches surfaced by rehypePathLinks — clickable
              // file link, no code box (kept for backticked input via the code
              // handler above).
              if (href && href.startsWith(FILE_LINK_HREF_PREFIX)) {
                const encoded = href.slice(FILE_LINK_HREF_PREFIX.length);
                let raw: string;
                try {
                  raw = decodeURIComponent(encoded);
                } catch {
                  raw = encoded;
                }
                const link = detectFileLink(raw);
                if (!link) {
                  return <span>{children}</span>;
                }
                return (
                  <button
                    type="button"
                    onClick={() => dispatchFileLink(link)}
                    title={`Open ${link.display}`}
                    className="text-primary underline underline-offset-2 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    {children}
                  </button>
                );
              }
              return (
                <a
                  href={href}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-primary no-underline hover:underline"
                >
                  {children}
                </a>
              );
            },
            blockquote({ children }: { children?: React.ReactNode }) {
              return (
                <blockquote className="my-4 border-l-4 border-border pl-4 italic text-[var(--color-text-tertiary)]">
                  {children}
                </blockquote>
              );
            },
            ul({ children }: { children?: React.ReactNode }) {
              return (
                <ul className="my-4 list-disc pl-6 [&>li:last-child]:mb-0 [&>li]:mb-1">
                  {children}
                </ul>
              );
            },
            ol({ children }: { children?: React.ReactNode }) {
              return (
                <ol className="my-4 list-decimal pl-6 [&>li:last-child]:mb-0 [&>li]:mb-1">
                  {children}
                </ol>
              );
            },
            details({ children, ...props }: React.HTMLAttributes<HTMLElement>) {
              return (
                <details
                  {...props}
                  className="my-4 rounded-md border border-border bg-[var(--color-surface)] px-3 py-2 [&[open]>summary]:mb-2"
                >
                  {children}
                </details>
              );
            },
            summary({ children, ...props }: React.HTMLAttributes<HTMLElement>) {
              return (
                <summary
                  {...props}
                  className="cursor-pointer select-none font-medium text-[var(--color-text-primary)] hover:text-[var(--color-text-secondary)]"
                >
                  {children}
                </summary>
              );
            },
            table({ children }: { children?: React.ReactNode }) {
              return (
                <div className="my-4 overflow-x-auto">
                  <table className="w-full border-collapse [&_td]:border [&_td]:border-border [&_td]:p-2 [&_th]:border [&_th]:border-border [&_th]:bg-[var(--color-surface)] [&_th]:p-2 [&_th]:text-left [&_th]:font-semibold">
                    {children}
                  </table>
                </div>
              );
            },
          }}
        >
          {content}
        </ReactMarkdown>
      </div>
    );
  }
);

MarkdownContent.displayName = "MarkdownContent";
