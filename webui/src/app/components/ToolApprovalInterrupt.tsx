"use client";

import { useMemo, useState } from "react";
import { flushSync } from "react-dom";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { AlertCircle, Check, X, Pencil } from "lucide-react";
import type { ActionRequest, ReviewConfig } from "@/app/types/types";
import { cn } from "@/lib/utils";
import { stringifyUnknown } from "@/app/utils/utils";

interface ToolApprovalInterruptProps {
  actionRequest: ActionRequest;
  reviewConfig?: ReviewConfig;
  onResume: (value: any) => void;
  isLoading?: boolean;
  onSubmitted?: () => void;
}

function argsToRecord(args: unknown): Record<string, unknown> {
  return args && typeof args === "object"
    ? (args as Record<string, unknown>)
    : {};
}

function cloneArgs(args: Record<string, unknown>): Record<string, unknown> {
  try {
    return JSON.parse(stringifyUnknown(args)) as Record<string, unknown>;
  } catch {
    return { ...args };
  }
}

function formatValue(value: unknown): string {
  return stringifyUnknown(value);
}

export function ToolApprovalInterrupt({
  actionRequest,
  reviewConfig,
  onResume,
  isLoading,
  onSubmitted,
}: ToolApprovalInterruptProps) {
  const [rejectionMessage, setRejectionMessage] = useState("");
  const [isEditing, setIsEditing] = useState(false);
  const [editedArgs, setEditedArgs] = useState<Record<string, unknown>>({});
  const [showRejectionInput, setShowRejectionInput] = useState(false);
  const [submitted, setSubmitted] = useState(false);

  const actionArgs = useMemo(
    () => argsToRecord(actionRequest.args),
    [actionRequest.args]
  );
  const allowedDecisions = reviewConfig?.allowedDecisions ??
    reviewConfig?.allowed_decisions ?? ["approve", "reject", "edit"];

  const submitDecision = (value: any) => {
    flushSync(() => {
      setSubmitted(true);
      if (onSubmitted) {
        onSubmitted();
      }
    });
    onResume(value);
  };

  const handleApprove = () => {
    submitDecision({
      decisions: [{ type: "approve" }],
    });
  };

  const handleReject = () => {
    if (showRejectionInput) {
      submitDecision({
        decisions: [
          {
            type: "reject",
            message: rejectionMessage.trim(),
          },
        ],
      });
    } else {
      setShowRejectionInput(true);
    }
  };

  const handleRejectConfirm = () => {
    submitDecision({
      decisions: [
        {
          type: "reject",
          message: rejectionMessage.trim(),
        },
      ],
    });
  };

  const handleEdit = () => {
    if (isEditing) {
      submitDecision({
        decisions: [
          {
            type: "edit",
            edited_action: {
              name: actionRequest.name,
              args: editedArgs,
            },
          },
        ],
      });
      setIsEditing(false);
      setEditedArgs({});
    }
  };

  const startEditing = () => {
    setIsEditing(true);
    setEditedArgs(cloneArgs(actionArgs));
    setShowRejectionInput(false);
  };

  const cancelEditing = () => {
    setIsEditing(false);
    setEditedArgs({});
  };

  const updateEditedArg = (key: string, value: string) => {
    try {
      const parsedValue =
        value.trim().startsWith("{") || value.trim().startsWith("[")
          ? JSON.parse(value)
          : value;
      setEditedArgs((prev) => ({ ...prev, [key]: parsedValue }));
    } catch {
      setEditedArgs((prev) => ({ ...prev, [key]: value }));
    }
  };

  if (submitted) {
    return null;
  }

  return (
    <div className="w-full rounded-md border border-border bg-muted/30 p-4">
      {/* Header */}
      <div className="mb-3 flex items-center gap-2 text-foreground">
        <AlertCircle
          size={16}
          className="text-yellow-600 dark:text-yellow-400"
          aria-hidden="true"
        />
        <span className="text-xs font-semibold uppercase tracking-wider">
          {"\u9700\u8981\u6279\u51c6"}
        </span>
      </div>

      {/* Description */}
      {actionRequest.description && (
        <p className="mb-3 text-sm text-muted-foreground">
          {actionRequest.description}
        </p>
      )}

      {/* Tool Info Card */}
      <div className="mb-4 rounded-sm border border-border bg-background p-3">
        <div className="mb-2">
          <span className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
            {"\u5de5\u5177"}
          </span>
          <p className="mt-1 font-mono text-sm font-medium text-foreground">
            {actionRequest.name}
          </p>
        </div>

        {isEditing ? (
          <div>
            <span className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
              {"\u7f16\u8f91\u53c2\u6570"}
            </span>
            <div className="mt-2 space-y-3">
              {Object.entries(actionArgs).map(([key, value]) => (
                <div key={key}>
                  <label className="mb-1 block text-xs font-medium text-foreground">
                    {key}
                  </label>
                  <Textarea
                    value={
                      editedArgs[key] !== undefined
                        ? typeof editedArgs[key] === "string"
                          ? (editedArgs[key] as string)
                          : formatValue(editedArgs[key])
                        : typeof value === "string"
                        ? value
                        : formatValue(value)
                    }
                    onChange={(e) => updateEditedArg(key, e.target.value)}
                    className="font-mono text-xs"
                    rows={
                      typeof value === "string" && value.length < 100 ? 2 : 4
                    }
                    disabled={isLoading}
                  />
                </div>
              ))}
            </div>
          </div>
        ) : (
          <div>
            <span className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
              {"\u53c2\u6570"}
            </span>
            <pre className="mt-2 overflow-x-auto whitespace-pre-wrap break-all rounded-sm border border-border bg-muted/40 p-2 font-mono text-xs text-foreground">
              {formatValue(actionArgs)}
            </pre>
          </div>
        )}
      </div>

      {/* Rejection Message Input */}
      {showRejectionInput && !isEditing && (
        <div className="mb-4">
          <label className="mb-2 block text-xs font-medium text-foreground">
            {"\u62d2\u7edd\u539f\u56e0\uff08\u53ef\u9009\uff09"}
          </label>
          <Textarea
            value={rejectionMessage}
            onChange={(e) => setRejectionMessage(e.target.value)}
            placeholder={"\u8bf4\u660e\u4f60\u62d2\u7edd\u8fd9\u4e2a\u64cd\u4f5c\u7684\u539f\u56e0..."}
            className="text-sm"
            rows={2}
            disabled={isLoading}
          />
        </div>
      )}

      {/* Actions */}
      <div className="flex flex-wrap gap-2">
        {isEditing ? (
          <>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={cancelEditing}
              disabled={isLoading}
            >
              {"\u53d6\u6d88"}
            </Button>
            <Button
              type="button"
              size="sm"
              onClick={handleEdit}
              disabled={isLoading}
              className="bg-green-600 text-white hover:bg-green-700 dark:bg-green-600 dark:hover:bg-green-700"
            >
              <Check
                size={14}
                aria-hidden="true"
              />
              {isLoading ? "\u4fdd\u5b58\u4e2d..." : "\u4fdd\u5b58\u5e76\u6279\u51c6"}
            </Button>
          </>
        ) : showRejectionInput ? (
          <>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => {
                setShowRejectionInput(false);
                setRejectionMessage("");
              }}
              disabled={isLoading}
            >
              {"\u53d6\u6d88"}
            </Button>
            <Button
              type="button"
              variant="destructive"
              size="sm"
              onClick={handleRejectConfirm}
              disabled={isLoading}
            >
              {isLoading ? "\u62d2\u7edd\u4e2d..." : "\u786e\u8ba4\u62d2\u7edd"}
            </Button>
          </>
        ) : (
          <>
            {allowedDecisions.includes("reject") && (
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={handleReject}
                disabled={isLoading}
                className="text-destructive hover:bg-destructive/10"
              >
                <X
                  size={14}
                  aria-hidden="true"
                />
                {"\u62d2\u7edd"}
              </Button>
            )}
            {allowedDecisions.includes("edit") && (
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={startEditing}
                disabled={isLoading}
              >
                <Pencil
                  size={14}
                  aria-hidden="true"
                />
                {"\u7f16\u8f91"}
              </Button>
            )}
            {allowedDecisions.includes("approve") && (
              <Button
                type="button"
                size="sm"
                onClick={handleApprove}
                disabled={isLoading}
                className={cn(
                  "bg-green-600 text-white hover:bg-green-700",
                  "dark:bg-green-600 dark:hover:bg-green-700"
                )}
              >
                <Check
                  size={14}
                  aria-hidden="true"
                />
                {isLoading ? "\u6279\u51c6\u4e2d..." : "\u6279\u51c6"}
              </Button>
            )}
          </>
        )}
      </div>
    </div>
  );
}
