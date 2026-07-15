"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism";
import { Check, Copy } from "lucide-react";
import { toast } from "sonner";
import { copyText } from "@/lib/clipboard";

interface CodeBlockProps {
  language: string;
  value: string;
}

export const CodeBlock = React.memo<CodeBlockProps>(({ language, value }) => {
  const [copied, setCopied] = useState(false);
  // Clear the "copied" timer on unmount so we don't setState on a gone component.
  const copyResetTimer = useRef<ReturnType<typeof setTimeout> | undefined>(
    undefined
  );
  useEffect(
    () => () => {
      if (copyResetTimer.current) clearTimeout(copyResetTimer.current);
    },
    []
  );
  useEffect(() => {
    setCopied(false);
    if (copyResetTimer.current) clearTimeout(copyResetTimer.current);
  }, [value]);
  const handleCopy = useCallback(async () => {
    if (await copyText(value)) {
      setCopied(true);
      if (copyResetTimer.current) clearTimeout(copyResetTimer.current);
      copyResetTimer.current = setTimeout(() => setCopied(false), 2000);
    } else {
      toast.error("Couldn't copy to clipboard.");
    }
  }, [value]);

  return (
    <div className="group relative max-w-full">
      <button
        type="button"
        onClick={handleCopy}
        aria-label={copied ? "Copied" : "Copy code"}
        className="absolute right-2 top-2 z-10 rounded-md border border-border bg-[var(--color-surface)] p-1.5 text-[var(--color-text-tertiary)] opacity-0 backdrop-blur-sm transition-opacity hover:text-[var(--color-text-primary)] focus-visible:opacity-100 focus-visible:ring-2 focus-visible:ring-ring group-hover:opacity-100"
      >
        {copied ? (
          <Check
            className="h-3.5 w-3.5"
            aria-hidden="true"
          />
        ) : (
          <Copy
            className="h-3.5 w-3.5"
            aria-hidden="true"
          />
        )}
      </button>
      <SyntaxHighlighter
        style={oneDark}
        language={language}
        PreTag="div"
        className="max-w-full rounded-md text-sm"
        wrapLines={true}
        wrapLongLines={true}
        lineProps={{
          style: {
            wordBreak: "break-all",
            whiteSpace: "pre-wrap",
            overflowWrap: "break-word",
          },
        }}
        customStyle={{
          margin: 0,
          maxWidth: "100%",
          overflowX: "auto",
          fontSize: "0.875rem",
        }}
      >
        {value}
      </SyntaxHighlighter>
    </div>
  );
});
CodeBlock.displayName = "CodeBlock";
