"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { MessageCircleQuestion } from "lucide-react";
import { cn } from "@/lib/utils";

interface Choice {
  value: string;
}

export interface AskUserQuestion {
  question: string;
  type: "text" | "multiple_choice";
  choices?: Choice[];
  required?: boolean;
}

interface AskUserInterruptProps {
  questions: AskUserQuestion[];
  onSubmit: (answers: string[]) => void;
  onCancel: () => void;
  isLoading?: boolean;
}

const OTHER = "__other__";

export function AskUserInterrupt({
  questions,
  onSubmit,
  onCancel,
  isLoading,
}: AskUserInterruptProps) {
  // answers[i] = final answer string; picked[i] = selected choice value or
  // OTHER for multiple_choice (null for free-text questions).
  const [answers, setAnswers] = useState<string[]>(() =>
    questions.map(() => "")
  );
  const [picked, setPicked] = useState<(string | null)[]>(() =>
    questions.map(() => null)
  );

  const setAnswer = (i: number, value: string) =>
    setAnswers((prev) => prev.map((a, idx) => (idx === i ? value : a)));
  const setPick = (i: number, value: string | null) =>
    setPicked((prev) => prev.map((p, idx) => (idx === i ? value : p)));

  const isOptional = (q: AskUserQuestion) =>
    q.type === "text" && q.required === false;

  const canSubmit = questions.every(
    (q, i) => isOptional(q) || answers[i].trim() !== ""
  );

  return (
    <div className="w-full rounded-lg border border-border bg-muted/30 p-4">
      <div className="mb-3 flex items-center gap-2 text-foreground">
        <MessageCircleQuestion
          size={16}
          className="text-[var(--brand)]"
          aria-hidden="true"
        />
        <span className="text-xs font-semibold uppercase tracking-wider">
          金乌需要你提供信息
        </span>
      </div>

      <div className="space-y-5">
        {questions.map((q, i) => (
          <div key={i}>
            <p className="mb-2 text-sm font-medium text-foreground">
              {q.question}
              {!isOptional(q) && (
                <span className="ml-1 text-destructive">*</span>
              )}
            </p>

            {q.type === "multiple_choice" ? (
              <div className="flex flex-col gap-2">
                {q.choices?.map((choice) => {
                  const selected = picked[i] === choice.value;
                  return (
                    <button
                      key={choice.value}
                      type="button"
                      disabled={isLoading}
                      onClick={() => {
                        setPick(i, choice.value);
                        setAnswer(i, choice.value);
                      }}
                      className={cn(
                        "rounded-md border px-3 py-2 text-left text-sm transition-colors",
                        selected
                          ? "border-[var(--brand-solid)] bg-[var(--brand-solid)] text-[var(--brand-foreground)]"
                          : "border-border bg-background hover:bg-accent"
                      )}
                    >
                      {choice.value}
                    </button>
                  );
                })}
                <button
                  type="button"
                  disabled={isLoading}
                  onClick={() => {
                    setPick(i, OTHER);
                    setAnswer(i, "");
                  }}
                  className={cn(
                    "rounded-md border px-3 py-2 text-left text-sm transition-colors",
                    picked[i] === OTHER
                      ? "border-[var(--brand)] text-foreground"
                      : "border-border bg-background hover:bg-accent"
                  )}
                >
                  其他…
                </button>
                {picked[i] === OTHER && (
                  <Textarea
                    value={answers[i]}
                    onChange={(e) => setAnswer(i, e.target.value)}
                    placeholder="请输入回答…"
                    className="text-sm"
                    rows={2}
                    disabled={isLoading}
                  />
                )}
              </div>
            ) : (
              <Textarea
                value={answers[i]}
                onChange={(e) => setAnswer(i, e.target.value)}
                placeholder={
                  isOptional(q)
                    ? "请输入回答…（选填）"
                    : "请输入回答…"
                }
                className="text-sm"
                rows={2}
                disabled={isLoading}
              />
            )}
          </div>
        ))}
      </div>

      <div className="mt-4 flex flex-wrap gap-2">
        <Button
          variant="outline"
          size="sm"
          onClick={onCancel}
          disabled={isLoading}
        >
          取消
        </Button>
        <Button
          size="sm"
          onClick={() => onSubmit(answers)}
          disabled={!canSubmit || isLoading}
          className="bg-[var(--brand-solid)] text-[var(--brand-foreground)] hover:opacity-90"
        >
          {isLoading ? "正在提交…" : "提交"}
        </Button>
      </div>
    </div>
  );
}
