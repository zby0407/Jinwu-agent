"use client";

import React, {
  useState,
  useEffect,
  useRef,
  useMemo,
  useCallback,
} from "react";
import {
  ChevronDown,
  ChevronUp,
  Terminal,
  AlertCircle,
  Loader2,
  CircleCheckBigIcon,
  StopCircle,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { ToolCall, ActionRequest, ReviewConfig } from "@/app/types/types";
import { cn } from "@/lib/utils";
import { LoadExternalComponent } from "@langchain/langgraph-sdk/react-ui";
import { ToolApprovalInterrupt } from "@/app/components/ToolApprovalInterrupt";
import { formatToolLabel } from "@/lib/toolLabel";
import { stringifyUnknown } from "@/app/utils/utils";

// One-line preview of a tool-call argument value, shown next to the key in
// the collapsed args row. Strings have newlines collapsed; non-strings are
// JSON.stringify'd. Both truncated so the row stays one line.
function previewArgValue(value: unknown): string {
  const MAX = 80;
  const s = formatValue(value, { pretty: false }).replace(/\s+/g, " ").trim();
  return s.length > MAX ? s.slice(0, MAX - 1) + "…" : s;
}

function formatValue(value: unknown, options?: { pretty?: boolean }): string {
  return stringifyUnknown(value, options?.pretty ? 2 : 0);
}

interface ToolCallBoxProps {
  toolCall: ToolCall;
  uiComponent?: any;
  stream?: any;
  graphId?: string;
  actionRequest?: ActionRequest;
  actionRequestKey?: string;
  actionRequestSubmitted?: boolean;
  onActionRequestSubmitted?: (key: string) => void;
  reviewConfig?: ReviewConfig;
  onResume?: (value: any) => void;
  isLoading?: boolean;
  autoApprove?: boolean;
  compact?: boolean;
}

export const ToolCallBox = React.memo<ToolCallBoxProps>(
  ({
    toolCall,
    uiComponent,
    stream,
    graphId,
    actionRequest,
    actionRequestKey: externalActionRequestKey,
    actionRequestSubmitted,
    onActionRequestSubmitted,
    reviewConfig,
    onResume,
    isLoading,
    autoApprove,
    compact = false,
  }) => {
    const [isExpanded, setIsExpanded] = useState(
      () => !!uiComponent || (!!actionRequest && !autoApprove)
    );
    const [expandedArgs, setExpandedArgs] = useState<Record<string, boolean>>(
      {}
    );
    const fallbackActionRequestKey = useMemo(() => {
      if (!actionRequest) return null;
      return `${toolCall.id}:${actionRequest.name}:${formatValue(
        actionRequest.args,
        { pretty: false }
      )}`;
    }, [actionRequest, toolCall.id]);
    const actionRequestKey =
      externalActionRequestKey ?? fallbackActionRequestKey;
    const [submittedActionRequestKey, setSubmittedActionRequestKey] = useState<
      string | null
    >(null);

    useEffect(() => {
      if (!actionRequestKey) {
        setSubmittedActionRequestKey(null);
        return;
      }
      setSubmittedActionRequestKey((current) =>
        current && current !== actionRequestKey ? null : current
      );
    }, [actionRequestKey]);

    // Generative UI: expand to show it.
    useEffect(() => {
      if (uiComponent) setIsExpanded(true);
    }, [uiComponent]);

    // Approval lifecycle: expand when an approval appears (skip if auto-approve
    // is on), and collapse again once it's resolved.
    const prevActionRequestRef = useRef(actionRequest);
    useEffect(() => {
      const had = !!prevActionRequestRef.current;
      const has = !!actionRequest;
      if (has && !had && !autoApprove) setIsExpanded(true);
      else if (!has && had) setIsExpanded(false);
      prevActionRequestRef.current = actionRequest;
    }, [actionRequest, autoApprove]);

    const { name, args, result, status } = useMemo(() => {
      // Streaming can deliver args as a (possibly partial) JSON string, not an
      // object — treat it as `unknown` and only expose a real object to the
      // args view, so Object.keys/entries never run on a string.
      const rawArgs: unknown = toolCall.args;
      return {
        // Targeted label overrides only (read_file/write_file/edit_file on
        // /memories → "Reading memory"/"Updating memory", think_tool →
        // "Reflection"); every other tool keeps its raw name.
        name: formatToolLabel(toolCall.name, rawArgs),
        args:
          rawArgs && typeof rawArgs === "object"
            ? (rawArgs as Record<string, unknown>)
            : {},
        result: toolCall.result,
        status: toolCall.status || "completed",
      };
    }, [toolCall]);

    const statusIcon = useMemo(() => {
      switch (status) {
        case "completed":
          return <CircleCheckBigIcon />;
        case "error":
          return (
            <AlertCircle
              size={14}
              className="text-destructive"
            />
          );
        case "pending":
          return (
            <Loader2
              size={14}
              className="animate-spin"
            />
          );
        case "interrupted":
          return (
            <StopCircle
              size={14}
              className="text-orange-500"
            />
          );
        default:
          return (
            <Terminal
              size={14}
              className="text-muted-foreground"
            />
          );
      }
    }, [status]);

    const toggleExpanded = useCallback(() => {
      setIsExpanded((prev) => !prev);
    }, []);

    const toggleArgExpanded = useCallback((argKey: string) => {
      setExpandedArgs((prev) => ({
        ...prev,
        [argKey]: !prev[argKey],
      }));
    }, []);

    const hasResult = result !== undefined && result !== null;
    const hasContent = hasResult || Object.keys(args).length > 0;
    const isActionRequestSubmitted =
      actionRequestSubmitted ?? submittedActionRequestKey === actionRequestKey;
    const showApproval =
      actionRequest &&
      onResume &&
      (!actionRequestKey || !isActionRequestSubmitted);

    return (
      <div
        className={cn(
          "w-full overflow-hidden rounded-lg border-none shadow-none outline-none transition-colors duration-200 focus-within:ring-2 focus-within:ring-ring hover:bg-accent",
          compact && "rounded-md",
          isExpanded && hasContent && "bg-accent"
        )}
      >
        <Button
          variant="ghost"
          size="sm"
          onClick={toggleExpanded}
          className={cn(
            "flex w-full items-center justify-between gap-2 border-none px-2 py-2 text-left shadow-none outline-none disabled:cursor-default",
            compact && "h-auto min-h-8 py-1.5"
          )}
          disabled={!hasContent}
        >
          <div className="flex w-full items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              {statusIcon}
              <span
                className={cn(
                  "text-[15px] font-medium tracking-[-0.6px] text-foreground",
                  compact && "text-xs tracking-normal"
                )}
              >
                {name}
              </span>
            </div>
            {hasContent &&
              (isExpanded ? (
                <ChevronUp
                  size={14}
                  className="shrink-0 text-muted-foreground"
                />
              ) : (
                <ChevronDown
                  size={14}
                  className="shrink-0 text-muted-foreground"
                />
              ))}
          </div>
        </Button>

        {isExpanded && hasContent && (
          <div className={cn("px-4 pb-4", compact && "px-2 pb-2")}>
            {uiComponent && stream && graphId ? (
              <div className="mt-4">
                <LoadExternalComponent
                  key={uiComponent.id}
                  stream={stream}
                  message={uiComponent}
                  namespace={graphId}
                  meta={{ status, args, result: result ?? "No Result Yet" }}
                />
              </div>
            ) : showApproval ? (
              // Show tool approval UI when there's an action request but no GenUI
              <div className="mt-4">
                <ToolApprovalInterrupt
                  actionRequest={actionRequest}
                  reviewConfig={reviewConfig}
                  onResume={onResume}
                  isLoading={isLoading}
                  onSubmitted={() => {
                    setSubmittedActionRequestKey(actionRequestKey);
                    if (actionRequestKey) {
                      onActionRequestSubmitted?.(actionRequestKey);
                    }
                    setIsExpanded(false);
                  }}
                />
              </div>
            ) : (
              <>
                {Object.keys(args).length > 0 && (
                  <div className={cn("mt-4", compact && "mt-2")}>
                    <h4 className="mb-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                      Arguments
                    </h4>
                    <div className="space-y-2">
                      {Object.entries(args).map(([key, value]) => {
                        const preview = previewArgValue(value);
                        const fullText = formatValue(value, { pretty: true });
                        // No fold when the preview already shows the value
                        // verbatim — saves a click on short single-line args.
                        const isFoldable = preview !== fullText;
                        const isOpen = isFoldable && !!expandedArgs[key];
                        const rowInner = (
                          <span className="flex min-w-0 flex-1 items-baseline gap-2">
                            <span className="shrink-0 font-mono">{key}:</span>
                            <span className="truncate font-mono font-normal text-muted-foreground">
                              {preview}
                            </span>
                          </span>
                        );
                        return (
                          <div
                            key={key}
                            className="rounded-sm border border-border"
                          >
                            {isFoldable ? (
                              <button
                                type="button"
                                onClick={() => toggleArgExpanded(key)}
                                className="flex w-full items-center justify-between gap-2 bg-muted/30 p-2 text-left text-xs font-medium transition-colors hover:bg-muted/50 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
                              >
                                {rowInner}
                                {isOpen ? (
                                  <ChevronUp
                                    size={12}
                                    className="shrink-0 text-muted-foreground"
                                  />
                                ) : (
                                  <ChevronDown
                                    size={12}
                                    className="shrink-0 text-muted-foreground"
                                  />
                                )}
                              </button>
                            ) : (
                              <div className="flex items-center gap-2 bg-muted/30 p-2 text-xs font-medium">
                                {rowInner}
                              </div>
                            )}
                            {isOpen && (
                              <div
                                className={cn(
                                  "border-t border-border bg-muted/20 p-2",
                                  compact && "p-1.5"
                                )}
                              >
                                <pre
                                  className={cn(
                                    "m-0 overflow-x-auto whitespace-pre-wrap break-all font-mono text-xs leading-6 text-foreground",
                                    compact && "text-[11px] leading-5"
                                  )}
                                >
                                  {fullText}
                                </pre>
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}
                {hasResult && (
                  <div className={cn("mt-4", compact && "mt-2")}>
                    <h4 className="mb-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                      Result
                    </h4>
                    <pre
                      className={cn(
                        "m-0 overflow-x-auto whitespace-pre-wrap break-all rounded-sm border border-border bg-muted/40 p-2 font-mono text-xs leading-7 text-foreground",
                        compact && "p-1.5 text-[11px] leading-5"
                      )}
                    >
                      {formatValue(result, { pretty: true })}
                    </pre>
                  </div>
                )}
              </>
            )}
          </div>
        )}
      </div>
    );
  }
);

ToolCallBox.displayName = "ToolCallBox";
