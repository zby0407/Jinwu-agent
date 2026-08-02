"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  ArrowLeft,
  Calendar,
  CalendarClock,
  ChevronRight,
  ClipboardList,
  Clock,
  FlaskConical,
  Loader2,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  Repeat2,
  Search,
  Trash2,
  X,
  type LucideIcon,
} from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Textarea } from "@/components/ui/textarea";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";
import {
  createScheduledTask,
  deleteScheduledTask,
  runScheduledTaskNow,
  updateScheduledTask,
  useScheduledTasks,
  type ScheduledTask,
} from "@/app/hooks/useScheduledTasks";
import {
  cronLabel,
  cronToSpec,
  DAY_NAMES,
  DEFAULT_SCHEDULE_SPEC,
  nextRunLabel,
  specToCron,
  validateCronExpression,
  type Frequency,
  type ScheduleSpec,
} from "@/lib/cronUtils";

interface Template {
  icon: LucideIcon;
  label: string;
  description: string;
  name: string;
  prompt: string;
  schedule: string;
}

const TEMPLATES: Template[] = [
  {
    icon: ClipboardList,
    label: "每日论文",
    description: "根据研究偏好跟踪最新机器学习论文。",
    name: "每日论文",
    prompt:
      "Summarise the latest ML papers from arXiv according to my research preferences with the paper-navigator skill. Focus on papers that are relevant to my current projects, explain why each one matters, and save the summary to ./daily-papers.md in the current workspace.",
    schedule: "0 9 * * *",
  },
  {
    icon: Repeat2,
    label: "每周研究复盘",
    description: "总结本周研究进展与后续方向。",
    name: "每周研究复盘",
    prompt:
      "Summarise this week's research progress across my active projects. Highlight key results, decisions, blockers, open questions, and what changed in my understanding. Then propose future research directions and concrete next steps. Save the review to ./weekly-research-review.md in the current workspace.",
    schedule: "0 17 * * 5",
  },
  {
    icon: Activity,
    label: "每周研究计划",
    description: "制定本周研究重点与周一行动计划。",
    name: "每周研究计划",
    prompt:
      "Draft this week's research plan based on my active projects, recent progress, project files, and open questions. Prioritise the most important research goals, propose concrete experiments or reading tasks, identify risks, and write a practical schedule for the week. Save the plan to ./weekly-research-plan.md in the current workspace.",
    schedule: "0 8 * * 1",
  },
  {
    icon: FlaskConical,
    label: "实验待办清单",
    description: "将开放问题转化为可验证的实验构想。",
    name: "实验待办清单",
    prompt:
      "Review my active project files, recent research notes, and open questions. Turn the most important unresolved ideas into a prioritised experiment backlog with hypotheses, expected signal, required data or code, estimated effort, and success criteria. Save it to ./experiment-backlog.md in the current workspace.",
    schedule: "0 10 * * 2",
  },
];

function formatAbsoluteDate(iso: string | null): string {
  if (!iso) return "未安排";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "未知";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(date);
}

function formatLongDate(iso: string | null): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function formatCreatedDate(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "未知";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "short",
    day: "numeric",
    year: "numeric",
  }).format(date);
}

function taskSearchText(task: ScheduledTask): string {
  return [
    task.name,
    task.prompt,
    task.schedule,
    cronLabel(task.schedule),
    task.next_run_date ? formatAbsoluteDate(task.next_run_date) : "",
  ]
    .join(" ")
    .toLowerCase();
}

function sortTasks(tasks: ScheduledTask[]): ScheduledTask[] {
  return [...tasks].sort((a, b) => {
    const aNext = a.next_run_date ? new Date(a.next_run_date).getTime() : 0;
    const bNext = b.next_run_date ? new Date(b.next_run_date).getTime() : 0;
    if (aNext && bNext && aNext !== bNext) return aNext - bNext;
    if (aNext && !bNext) return -1;
    if (!aNext && bNext) return 1;
    return a.name.localeCompare(b.name);
  });
}

function FieldLabel({
  children,
  htmlFor,
}: {
  children: React.ReactNode;
  htmlFor?: string;
}) {
  return (
    <label
      htmlFor={htmlFor}
      className="text-xs font-medium text-muted-foreground"
    >
      {children}
    </label>
  );
}

function ScheduleBuilder({
  value,
  onChange,
  error,
}: {
  value: ScheduleSpec;
  onChange: (s: ScheduleSpec) => void;
  error?: string | null;
}) {
  const set = (patch: Partial<ScheduleSpec>) =>
    onChange({ ...value, ...patch });

  const selectClass =
    "h-9 rounded-md border border-input bg-background py-1.5 pl-2.5 pr-8 text-sm outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50";
  const frequencySelectClass = cn(selectClass, "w-full sm:min-w-40");
  const daySelectClass = cn(selectClass, "w-full sm:w-28");

  return (
    <div className="space-y-2">
      <div className="grid gap-2 sm:grid-cols-[minmax(9rem,1fr)_auto_auto_auto_auto] sm:items-center">
        <select
          aria-label="执行频率"
          value={value.frequency}
          onChange={(e) => set({ frequency: e.target.value as Frequency })}
          className={frequencySelectClass}
        >
          <option value="daily">每天</option>
          <option value="weekly">每周</option>
          <option value="monthly">每月</option>
          <option value="custom">自定义 Cron</option>
        </select>

        {value.frequency !== "custom" && (
          <>
            {value.frequency === "weekly" && (
              <select
                aria-label="星期"
                value={value.dayOfWeek}
                onChange={(e) => set({ dayOfWeek: Number(e.target.value) })}
                className={daySelectClass}
              >
                {DAY_NAMES.map((day, index) => (
                  <option
                    key={day}
                    value={index}
                  >
                    {day}
                  </option>
                ))}
              </select>
            )}

            {value.frequency === "monthly" && (
              <select
                aria-label="每月日期"
                value={value.dayOfMonth}
                onChange={(e) => set({ dayOfMonth: Number(e.target.value) })}
                className={daySelectClass}
              >
                {Array.from({ length: 28 }, (_, index) => index + 1).map(
                  (day) => (
                    <option
                      key={day}
                      value={day}
                    >
                      {day} 日
                    </option>
                  )
                )}
              </select>
            )}

            <span className="hidden text-xs text-muted-foreground sm:block">
              时间
            </span>
            <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-1 sm:col-span-2 sm:w-32">
              <Input
                aria-label="小时"
                type="number"
                min={0}
                max={23}
                value={value.hour}
                onChange={(e) => set({ hour: Number(e.target.value) })}
                className="h-9 text-center font-mono tabular-nums"
              />
              <span className="font-mono text-muted-foreground">:</span>
              <Input
                aria-label="分钟"
                type="number"
                min={0}
                max={59}
                value={value.minute}
                onChange={(e) => set({ minute: Number(e.target.value) })}
                className="h-9 text-center font-mono tabular-nums"
              />
            </div>
          </>
        )}
      </div>

      {value.frequency === "custom" && (
        <Input
          type="text"
          value={value.custom}
          onChange={(e) => set({ custom: e.target.value })}
          placeholder="0 9 * * 1-5"
          spellCheck={false}
          aria-invalid={Boolean(error)}
          aria-describedby="schedule-cron-help"
          className="h-9 font-mono text-sm"
        />
      )}

      <div
        id="schedule-cron-help"
        className={cn(
          "flex items-center gap-1.5 text-xs",
          error ? "text-destructive" : "text-muted-foreground"
        )}
      >
        <CalendarClock
          className="size-3.5 shrink-0"
          aria-hidden="true"
        />
        <span className="min-w-0 truncate">
          {error ?? cronLabel(specToCron(value))}
        </span>
      </div>
    </div>
  );
}

interface TemplateButtonProps {
  template: Template;
  onSelect: (template: Template) => void;
  compact?: boolean;
}

function TemplateButton({ template, onSelect, compact }: TemplateButtonProps) {
  const Icon = template.icon;

  return (
    <button
      type="button"
      onClick={() => onSelect(template)}
      className={cn(
        "hover:border-[var(--brand)]/40 group flex w-full min-w-0 items-start gap-2 rounded-md border border-border bg-[var(--color-surface)] text-left transition-colors hover:bg-accent/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        compact ? "px-2.5 py-2" : "px-3 py-2.5"
      )}
    >
      <span className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-md bg-muted text-[var(--brand)] group-hover:bg-background">
        <Icon
          className="size-4"
          aria-hidden="true"
        />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-medium">
          {template.label}
        </span>
        {!compact && (
          <span className="mt-0.5 line-clamp-2 text-xs leading-snug text-muted-foreground">
            {template.description}
          </span>
        )}
        <span className="mt-1 block text-[11px] text-muted-foreground">
          {cronLabel(template.schedule)}
        </span>
      </span>
    </button>
  );
}

interface CreateFormProps {
  initialTemplate?: Template;
  initialTask?: ScheduledTask;
  onSaved: (task?: ScheduledTask) => void;
  onCancel: () => void;
}

function TaskForm({
  initialTemplate,
  initialTask,
  onSaved,
  onCancel,
}: CreateFormProps) {
  const isEditing = Boolean(initialTask);
  const [name, setName] = useState(
    initialTask?.name ?? initialTemplate?.name ?? ""
  );
  const [prompt, setPrompt] = useState(
    initialTask?.prompt ?? initialTemplate?.prompt ?? ""
  );
  const [spec, setSpec] = useState<ScheduleSpec>(() =>
    initialTask
      ? cronToSpec(initialTask.schedule)
      : initialTemplate
      ? cronToSpec(initialTemplate.schedule)
      : { ...DEFAULT_SCHEDULE_SPEC }
  );
  const [saving, setSaving] = useState(false);
  const nameRef = useRef<HTMLInputElement>(null);

  const cron = specToCron(spec);
  const cronError =
    spec.frequency === "custom" ? validateCronExpression(cron) : null;
  const canSave = Boolean(name.trim() && prompt.trim() && !cronError);

  useEffect(() => {
    nameRef.current?.focus();
  }, []);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!name.trim()) {
      toast.error("请输入任务名称。" );
      return;
    }
    if (!prompt.trim()) {
      toast.error("请输入任务说明。" );
      return;
    }
    if (cronError) {
      toast.error(cronError);
      return;
    }

    setSaving(true);
    try {
      if (initialTask) {
        const result = await updateScheduledTask({
          cronId: initialTask.cron_id,
          name: name.trim(),
          prompt: prompt.trim(),
          schedule: cron,
        });
        if (result.oldTaskDeleted) {
          toast.success(`“${name.trim()}”已更新。`);
        } else {
          toast.warning(
            `“${name.trim()}”已保存，但旧的定时任务未能移除。`
          );
        }
        onSaved(result.task);
      } else {
        await createScheduledTask({
          name: name.trim(),
          prompt: prompt.trim(),
          schedule: cron,
        });
        toast.success(`“${name.trim()}”已安排。`);
        onSaved();
      }
    } catch (err) {
      toast.error(
        err instanceof Error
          ? err.message
          : isEditing
          ? "更新定时任务失败。"
          : "创建定时任务失败。"
      );
    } finally {
      setSaving(false);
    }
  };

  return (
    <form
      onSubmit={handleSubmit}
      className="flex h-full min-h-0 flex-col"
    >
      <div className="flex flex-shrink-0 items-center gap-2 border-b border-border px-3 py-2.5 sm:px-5">
        <button
          type="button"
          onClick={onCancel}
          aria-label="返回定时任务列表"
          className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring md:hidden"
        >
          <ArrowLeft
            className="size-4"
            aria-hidden="true"
          />
        </button>
        <div className="min-w-0 flex-1">
          <h2 className="truncate text-sm font-semibold">
            {isEditing ? "编辑定时任务" : "新建定时任务"}
          </h2>
          <p className="truncate text-xs text-muted-foreground">
            金乌会按计划自动执行这项任务。
          </p>
        </div>
      </div>

      <ScrollArea className="min-h-0 flex-1">
        <div className="mx-auto flex w-full max-w-3xl flex-col gap-4 p-3 sm:p-5">
          <div className="space-y-1.5">
            <FieldLabel htmlFor="scheduled-task-name">任务名称</FieldLabel>
            <Input
              id="scheduled-task-name"
              ref={nameRef}
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="每日简报"
              autoComplete="off"
            />
          </div>

          <div className="space-y-1.5">
            <FieldLabel htmlFor="scheduled-task-prompt">
              任务说明
            </FieldLabel>
            <Textarea
              id="scheduled-task-prompt"
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder="描述金乌每次运行这项任务时需要完成的工作……"
              rows={9}
              className="min-h-52 resize-y leading-relaxed"
            />
          </div>

          <div className="space-y-1.5">
            <FieldLabel>执行计划</FieldLabel>
            <ScheduleBuilder
              value={spec}
              onChange={setSpec}
              error={cronError}
            />
          </div>
        </div>
      </ScrollArea>

      <div className="flex flex-shrink-0 items-center justify-end gap-2 border-t border-border bg-background px-3 py-2.5 sm:px-5">
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={onCancel}
          disabled={saving}
        >
          取消
        </Button>
        <Button
          type="submit"
          size="sm"
          disabled={saving || !canSave}
        >
          {saving ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : isEditing ? (
            <Pencil className="size-3.5" />
          ) : (
            <Plus className="size-3.5" />
          )}
          {isEditing ? "保存更改" : "创建任务"}
        </Button>
      </div>
    </form>
  );
}

interface TaskDetailProps {
  task: ScheduledTask;
  onBack: () => void;
  onEdit: () => void;
  onDeleted: () => void;
}

function TaskDetail({ task, onBack, onEdit, onDeleted }: TaskDetailProps) {
  const [running, setRunning] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const nextRunTitle = formatLongDate(task.next_run_date);

  const handleRunNow = useCallback(async () => {
    setRunning(true);
    try {
      await runScheduledTaskNow(task.prompt);
      toast.success(`“${task.name}”已启动。`);
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : "启动任务失败。"
      );
    } finally {
      setRunning(false);
    }
  }, [task]);

  const handleDelete = useCallback(async () => {
    setDeleting(true);
    try {
      await deleteScheduledTask(task.cron_id);
      toast.success(`“${task.name}”已删除。`);
      setDeleteOpen(false);
      onDeleted();
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : "删除任务失败。"
      );
    } finally {
      setDeleting(false);
    }
  }, [task, onDeleted]);

  return (
    <>
      <div className="flex h-full min-h-0 flex-col">
        <div className="flex flex-shrink-0 items-center gap-2 border-b border-border px-3 py-2.5 sm:px-5">
          <button
            type="button"
            onClick={onBack}
            aria-label="返回定时任务列表"
            className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring md:hidden"
          >
            <ArrowLeft
              className="size-4"
              aria-hidden="true"
            />
          </button>
          <div className="min-w-0 flex-1">
            <h2 className="truncate text-sm font-semibold">{task.name}</h2>
            <p className="truncate text-xs text-muted-foreground">
              {cronLabel(task.schedule)}
            </p>
          </div>
          <Button
            size="sm"
            onClick={handleRunNow}
            disabled={running || !task.prompt.trim()}
          >
            {running ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : (
              <Play className="size-3.5" />
            )}
            立即运行
          </Button>
        </div>

        <ScrollArea className="min-h-0 flex-1">
          <div className="mx-auto w-full max-w-3xl space-y-4 p-3 sm:p-5">
            <div className="grid gap-2 sm:grid-cols-3">
              <div className="rounded-md border border-border bg-[var(--color-surface)] px-3 py-2">
                <p className="text-[11px] font-medium uppercase text-muted-foreground">
                  下次运行
                </p>
                <p
                  className="mt-1 truncate text-sm font-medium tabular-nums"
                  title={nextRunTitle}
                >
                  {task.next_run_date
                    ? `${nextRunLabel(
                        task.next_run_date
                      )} · ${formatAbsoluteDate(task.next_run_date)}`
                    : "未安排"}
                </p>
              </div>
              <div className="rounded-md border border-border bg-[var(--color-surface)] px-3 py-2">
                <p className="text-[11px] font-medium uppercase text-muted-foreground">
                  Cron
                </p>
                <p className="mt-1 truncate font-mono text-sm">
                  {task.schedule}
                </p>
              </div>
              <div className="rounded-md border border-border bg-[var(--color-surface)] px-3 py-2">
                <p className="text-[11px] font-medium uppercase text-muted-foreground">
                  创建时间
                </p>
                <p className="mt-1 truncate text-sm">
                  {formatCreatedDate(task.created_at)}
                </p>
              </div>
            </div>

            <section className="space-y-1.5">
              <p className="text-xs font-medium text-muted-foreground">
                任务说明
              </p>
              <div className="max-h-[min(38rem,55vh)] overflow-auto rounded-md border border-border bg-[var(--color-surface)] px-3 py-2.5">
                {task.prompt ? (
                  <p className="whitespace-pre-wrap break-words text-sm leading-relaxed">
                    {task.prompt}
                  </p>
                ) : (
                  <p className="text-sm italic text-muted-foreground">
                    未保存任务说明。
                  </p>
                )}
              </div>
            </section>
          </div>
        </ScrollArea>

        <div className="flex flex-shrink-0 items-center justify-between gap-2 border-t border-border bg-background px-3 py-2.5 sm:px-5">
          <p className="hidden truncate text-xs text-muted-foreground sm:block">
            手动运行会立即创建一个调度会话。
          </p>
          <div className="ml-auto flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={onEdit}
              aria-label={`编辑定时任务“${task.name}”`}
            >
              <Pencil className="size-3.5" />
              编辑
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setDeleteOpen(true)}
              aria-label={`删除定时任务“${task.name}”`}
              className="text-destructive hover:border-destructive hover:text-destructive"
            >
              <Trash2 className="size-3.5" />
              删除
            </Button>
          </div>
        </div>
      </div>

      <Dialog
        open={deleteOpen}
        onOpenChange={(open) => {
          if (!deleting) setDeleteOpen(open);
        }}
      >
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>删除定时任务？</DialogTitle>
            <DialogDescription>
              “{task.name}”将停止运行。此操作无法撤销。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setDeleteOpen(false)}
              disabled={deleting}
            >
              取消
            </Button>
            <Button
              variant="destructive"
              size="sm"
              onClick={handleDelete}
              disabled={deleting}
              aria-label={`确认删除定时任务“${task.name}”`}
            >
              {deleting && <Loader2 className="size-3.5 animate-spin" />}
              删除
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

function TaskRow({
  task,
  active,
  onSelect,
}: {
  task: ScheduledTask;
  active: boolean;
  onSelect: () => void;
}) {
  const nextRun = task.next_run_date;

  return (
    <button
      type="button"
      onClick={onSelect}
      aria-current={active}
      className={cn(
        "flex w-full items-start gap-2 rounded-md px-2 py-2 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        active ? "bg-accent" : "hover:bg-accent/60"
      )}
    >
      <span className="mt-1 flex size-2 shrink-0 rounded-full bg-[var(--brand)]" />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-medium">{task.name}</span>
        <span className="mt-0.5 block truncate text-xs text-muted-foreground">
          {cronLabel(task.schedule)}
        </span>
        <span className="mt-1 flex min-w-0 items-center gap-1.5 text-[11px] text-muted-foreground">
          <Clock
            className="size-3 shrink-0"
            aria-hidden="true"
          />
          <span
            className="truncate tabular-nums"
            title={formatLongDate(nextRun)}
          >
            {nextRun ? nextRunLabel(nextRun) : "暂无下次运行"}
          </span>
        </span>
      </span>
      <ChevronRight
        className="mt-1 size-3.5 shrink-0 text-muted-foreground"
        aria-hidden="true"
      />
    </button>
  );
}

type RightPane =
  | { kind: "empty" }
  | { kind: "create"; template?: Template; createId: number }
  | { kind: "edit"; task: ScheduledTask; editId: number }
  | { kind: "detail"; task: ScheduledTask };

export function ScheduledTasksPanel() {
  const { tasks, loading, error, refresh } = useScheduledTasks();
  const [right, setRight] = useState<RightPane>({ kind: "empty" });
  const [query, setQuery] = useState("");
  const createIdRef = useRef(0);
  const editIdRef = useRef(0);
  // Holds the cron_id of a just-saved task while the list refresh is in flight.
  // The sync effect below won't navigate away while this id is pending.
  const pendingTaskIdRef = useRef<string | null>(null);

  const sortedTasks = useMemo(() => sortTasks(tasks), [tasks]);
  const filteredTasks = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (!normalized) return sortedTasks;
    return sortedTasks.filter((task) =>
      taskSearchText(task).includes(normalized)
    );
  }, [query, sortedTasks]);
  const nextTask = sortedTasks.find((task) => task.next_run_date);
  const selectedTaskId = right.kind === "detail" ? right.task.cron_id : null;

  // Keep detail pane in sync with the task list.
  // While a refresh is in flight, hold position so a just-saved task's new
  // cron_id (not yet in the list) doesn't trigger premature navigation to empty.
  // Once loading finishes we clear the pending guard and evaluate normally.
  useEffect(() => {
    if (!selectedTaskId) return;
    if (loading) return;
    const updated = tasks.find((task) => task.cron_id === selectedTaskId);
    if (updated) {
      if (pendingTaskIdRef.current === selectedTaskId) {
        pendingTaskIdRef.current = null;
      }
      setRight({ kind: "detail", task: updated });
      return;
    }
    if (pendingTaskIdRef.current === selectedTaskId && !error) {
      return;
    }
    pendingTaskIdRef.current = null;
    setRight({ kind: "empty" });
  }, [error, loading, selectedTaskId, tasks]);

  const openCreate = useCallback((template?: Template) => {
    createIdRef.current += 1;
    setRight({ kind: "create", template, createId: createIdRef.current });
  }, []);

  const openEdit = useCallback((task: ScheduledTask) => {
    editIdRef.current += 1;
    setRight({ kind: "edit", task, editId: editIdRef.current });
  }, []);

  const handleSaved = useCallback(
    (task?: ScheduledTask) => {
      pendingTaskIdRef.current = task?.cron_id ?? null;
      refresh();
      setRight(task ? { kind: "detail", task } : { kind: "empty" });
    },
    [refresh]
  );

  const handleDeleted = useCallback(() => {
    refresh();
    setRight({ kind: "empty" });
  }, [refresh]);

  return (
    <div className="flex h-full min-h-0 flex-col bg-background">
      <header className="flex flex-shrink-0 items-center justify-between gap-3 border-b border-border px-3 py-2.5 sm:px-4">
        <div className="flex min-w-0 items-center gap-2">
          <span className="flex size-8 shrink-0 items-center justify-center rounded-md bg-accent text-[var(--brand)]">
            <CalendarClock
              className="size-4"
              aria-hidden="true"
            />
          </span>
          <div className="min-w-0">
            <div className="flex min-w-0 items-center gap-2">
              <h1 className="truncate text-sm font-semibold">定时任务</h1>
              {!loading && (
                <span className="rounded-full bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                  {tasks.length}
                </span>
              )}
            </div>
            <p className="truncate text-xs text-muted-foreground">
              {nextTask?.next_run_date
                ? `下次：${nextRunLabel(nextTask.next_run_date)}`
                : "金乌定时任务"}
            </p>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          <button
            type="button"
            onClick={refresh}
            disabled={loading}
            aria-label="刷新定时任务"
            title="刷新定时任务"
            className="rounded-md p-2 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
          >
            <RefreshCw
              className={cn("size-4", loading && "animate-spin")}
              aria-hidden="true"
            />
          </button>
          <Button
            size="sm"
            onClick={() => openCreate()}
          >
            <Plus className="size-3.5" />
            <span className="hidden sm:inline">新建任务</span>
            <span className="sm:hidden">新建</span>
          </Button>
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        <aside
          className={cn(
            "w-full min-w-0 flex-col border-r border-border md:flex md:w-72 md:flex-shrink-0",
            right.kind === "empty" ? "flex" : "hidden"
          )}
        >
          <div className="flex-shrink-0 space-y-2 border-b border-border p-2.5">
            <div className="relative">
              <Search
                className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
                aria-hidden="true"
              />
              <Input
                type="search"
                name="scheduled-search"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="搜索定时任务…"
                aria-label="搜索定时任务"
                autoComplete="off"
                spellCheck={false}
                className="h-9 pl-8 pr-8"
              />
              {query && (
                <button
                  type="button"
                  onClick={() => setQuery("")}
                  aria-label="清空定时任务搜索"
                  className="absolute right-1 top-1/2 -translate-y-1/2 rounded p-1 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <X
                    className="size-3.5"
                    aria-hidden="true"
                  />
                </button>
              )}
            </div>
          </div>

          <ScrollArea className="h-0 flex-1">
            {loading ? (
              <div className="flex items-center justify-center gap-2 py-10 text-sm text-muted-foreground">
                <Loader2
                  className="size-4 animate-spin"
                  aria-hidden="true"
                />
                正在加载任务…
              </div>
            ) : error ? (
              <div
                role="alert"
                className="space-y-3 p-3"
              >
                <p className="text-sm text-destructive">{error}</p>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={refresh}
                >
                  <RefreshCw className="size-3.5" />
                  重试
                </Button>
              </div>
            ) : tasks.length === 0 ? (
              <div className="space-y-4 p-3">
                <div className="rounded-md border border-dashed border-border bg-[var(--color-surface)] px-3 py-5 text-center">
                  <Calendar
                    className="mx-auto size-7 text-muted-foreground/60"
                    aria-hidden="true"
                  />
                  <p className="mt-2 text-sm font-medium">暂无定时任务</p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    可从模板开始，或从头创建任务。
                  </p>
                </div>
                <div className="space-y-2">
                  <p className="px-1 text-[11px] font-semibold uppercase text-muted-foreground">
                    模板
                  </p>
                  {TEMPLATES.map((template) => (
                    <TemplateButton
                      key={template.label}
                      template={template}
                      onSelect={openCreate}
                    />
                  ))}
                </div>
              </div>
            ) : filteredTasks.length === 0 ? (
              <div className="space-y-3 p-4 text-center">
                <p className="text-sm font-medium">没有匹配的任务</p>
                <p className="text-xs text-muted-foreground">
                  请尝试搜索任务名称、计划或说明关键词。
                </p>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setQuery("")}
                >
                  清空搜索
                </Button>
              </div>
            ) : (
              <div className="p-1.5">
                <div className="mb-2 flex items-center justify-between px-2 py-1">
                  <p className="text-[11px] font-semibold uppercase text-muted-foreground">
                    任务
                  </p>
                  <p className="text-[11px] text-muted-foreground">
                    {filteredTasks.length}
                  </p>
                </div>
                <div className="flex flex-col gap-0.5">
                  {filteredTasks.map((task) => {
                    const active =
                      right.kind === "detail" &&
                      right.task.cron_id === task.cron_id;
                    return (
                      <TaskRow
                        key={task.cron_id}
                        task={task}
                        active={active}
                        onSelect={() => setRight({ kind: "detail", task })}
                      />
                    );
                  })}
                </div>
                <div className="mt-3 space-y-2 border-t border-border px-1 py-3">
                  <p className="px-1 text-[11px] font-semibold uppercase text-muted-foreground">
                    模板
                  </p>
                  <div className="grid gap-1.5">
                    {TEMPLATES.map((template) => (
                      <TemplateButton
                        key={template.label}
                        template={template}
                        onSelect={openCreate}
                        compact
                      />
                    ))}
                  </div>
                </div>
              </div>
            )}
          </ScrollArea>
        </aside>

        <section
          className={cn(
            "min-w-0 flex-1 flex-col",
            right.kind === "empty" ? "hidden md:flex" : "flex"
          )}
        >
          {right.kind === "empty" && (
            <div className="flex flex-1 items-center justify-center p-5">
              <div className="w-full max-w-lg space-y-4">
                <div className="space-y-1 text-center">
                  <CalendarClock
                    className="mx-auto size-9 text-muted-foreground/40"
                    aria-hidden="true"
                  />
                  <p className="text-sm font-medium">选择一个定时任务</p>
                  <p className="text-xs text-muted-foreground">
                    选择下面的模板，或在对话中让金乌创建定时任务。
                  </p>
                </div>
                {tasks.length > 0 && (
                  <div className="mx-auto grid w-full max-w-xl gap-2 sm:grid-cols-2">
                    {TEMPLATES.map((template) => (
                      <TemplateButton
                        key={template.label}
                        template={template}
                        onSelect={openCreate}
                        compact
                      />
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}

          {right.kind === "create" && (
            <TaskForm
              key={right.createId}
              initialTemplate={right.template}
              onSaved={handleSaved}
              onCancel={() => setRight({ kind: "empty" })}
            />
          )}

          {right.kind === "edit" && (
            <TaskForm
              key={right.editId}
              initialTask={right.task}
              onSaved={handleSaved}
              onCancel={() => setRight({ kind: "detail", task: right.task })}
            />
          )}

          {right.kind === "detail" && (
            <TaskDetail
              task={right.task}
              onBack={() => setRight({ kind: "empty" })}
              onEdit={() => openEdit(right.task)}
              onDeleted={handleDeleted}
            />
          )}
        </section>
      </div>
    </div>
  );
}
