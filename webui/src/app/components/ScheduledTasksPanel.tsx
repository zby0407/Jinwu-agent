"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  Calendar,
  CalendarClock,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  ClipboardList,
  Clock,
  FlaskConical,
  Globe2,
  Loader2,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  Repeat2,
  Search,
  Square,
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
import { MarkdownContent } from "@/app/components/MarkdownContent";
import {
  archiveScheduledTask,
  createScheduledTask,
  listScheduledTaskRuns,
  listUnassignedScheduledRuns,
  runScheduledTaskNow,
  updateScheduledTask,
  useScheduledTasks,
  type ScheduledTask,
  type ScheduledTaskRun,
} from "@/app/hooks/useScheduledTasks";
import { browserTimezone } from "@/lib/scheduledTaskUtils.js";
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

function formatAbsoluteDate(
  iso: string | null,
  timezone?: string
): string {
  if (!iso) return "未安排";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "未知";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZone: timezone,
  }).format(date);
}

function formatLongDate(iso: string | null, timezone?: string): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: timezone,
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

function isValidTimezone(timezone: string): boolean {
  try {
    new Intl.DateTimeFormat("zh-CN", { timeZone: timezone }).format();
    return true;
  } catch {
    return false;
  }
}

function taskSearchText(task: ScheduledTask): string {
  return [
    task.name,
    task.prompt,
    task.schedule,
    cronLabel(task.schedule),
    task.next_run_date
      ? formatAbsoluteDate(task.next_run_date, task.timezone)
      : "",
    task.timezone,
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

interface TimezoneOption {
  value: string;
  label: string;
  keywords: string;
}

const COMMON_TIMEZONES: TimezoneOption[] = [
  { value: "Asia/Shanghai", label: "中国 · 北京时间", keywords: "中国 北京 上海 china" },
  { value: "Asia/Hong_Kong", label: "中国香港", keywords: "香港 hong kong" },
  { value: "Asia/Taipei", label: "中国台北", keywords: "台北 taipei" },
  { value: "Asia/Tokyo", label: "日本 · 东京", keywords: "日本 东京 japan tokyo" },
  { value: "Asia/Seoul", label: "韩国 · 首尔", keywords: "韩国 首尔 korea seoul" },
  { value: "Asia/Singapore", label: "新加坡", keywords: "新加坡 singapore" },
  { value: "Asia/Kolkata", label: "印度 · 加尔各答", keywords: "印度 india kolkata" },
  { value: "Asia/Dubai", label: "阿联酋 · 迪拜", keywords: "阿联酋 迪拜 uae dubai" },
  { value: "Europe/London", label: "英国 · 伦敦", keywords: "英国 伦敦 uk london" },
  { value: "Europe/Paris", label: "法国 · 巴黎", keywords: "法国 巴黎 france paris" },
  { value: "Europe/Berlin", label: "德国 · 柏林", keywords: "德国 柏林 germany berlin" },
  { value: "Europe/Moscow", label: "俄罗斯 · 莫斯科", keywords: "俄罗斯 莫斯科 russia moscow" },
  { value: "America/New_York", label: "美国 · 纽约（东部）", keywords: "美国 纽约 美东 usa new york eastern" },
  { value: "America/Chicago", label: "美国 · 芝加哥（中部）", keywords: "美国 芝加哥 usa chicago central" },
  { value: "America/Denver", label: "美国 · 丹佛（山地）", keywords: "美国 丹佛 usa denver mountain" },
  { value: "America/Los_Angeles", label: "美国 · 洛杉矶（西部）", keywords: "美国 洛杉矶 美西 usa los angeles pacific" },
  { value: "America/Toronto", label: "加拿大 · 多伦多", keywords: "加拿大 多伦多 canada toronto" },
  { value: "America/Vancouver", label: "加拿大 · 温哥华", keywords: "加拿大 温哥华 canada vancouver" },
  { value: "America/Mexico_City", label: "墨西哥 · 墨西哥城", keywords: "墨西哥 mexico city" },
  { value: "America/Sao_Paulo", label: "巴西 · 圣保罗", keywords: "巴西 圣保罗 brazil sao paulo" },
  { value: "America/Argentina/Buenos_Aires", label: "阿根廷 · 布宜诺斯艾利斯", keywords: "阿根廷 argentina buenos aires" },
  { value: "Australia/Sydney", label: "澳大利亚 · 悉尼", keywords: "澳大利亚 悉尼 australia sydney" },
  { value: "Australia/Perth", label: "澳大利亚 · 珀斯", keywords: "澳大利亚 珀斯 australia perth" },
  { value: "Pacific/Auckland", label: "新西兰 · 奥克兰", keywords: "新西兰 奥克兰 new zealand auckland" },
  { value: "Africa/Cairo", label: "埃及 · 开罗", keywords: "埃及 开罗 egypt cairo" },
  { value: "Africa/Johannesburg", label: "南非 · 约翰内斯堡", keywords: "南非 south africa johannesburg" },
  { value: "UTC", label: "协调世界时 UTC", keywords: "世界时 utc gmt" },
];

function TimezoneCombobox({
  value,
  onChange,
  invalid,
}: {
  value: string;
  onChange: (value: string) => void;
  invalid: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const selectedIsCommon = COMMON_TIMEZONES.some(
    (option) => option.value === value.trim()
  );
  const normalizedQuery = selectedIsCommon ? "" : value.trim().toLowerCase();
  const filtered = COMMON_TIMEZONES.filter((option) => {
    if (!normalizedQuery) return true;
    return `${option.label} ${option.value} ${option.keywords}`
      .toLowerCase()
      .includes(normalizedQuery);
  });

  useEffect(() => {
    const handlePointerDown = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handlePointerDown);
    return () => document.removeEventListener("mousedown", handlePointerDown);
  }, []);

  const choose = (option: TimezoneOption) => {
    onChange(option.value);
    setOpen(false);
    setActiveIndex(0);
  };

  return (
    <div
      ref={rootRef}
      className="relative"
    >
      <Globe2 className="pointer-events-none absolute left-3 top-1/2 z-10 size-4 -translate-y-1/2 text-muted-foreground" />
      <Input
        id="scheduled-task-timezone"
        role="combobox"
        aria-autocomplete="list"
        aria-expanded={open}
        aria-controls="scheduled-task-timezone-options"
        aria-invalid={invalid}
        value={value}
        onFocus={() => setOpen(true)}
        onChange={(event) => {
          onChange(event.target.value);
          setOpen(true);
          setActiveIndex(0);
        }}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown") {
            event.preventDefault();
            setOpen(true);
            setActiveIndex((index) =>
              Math.min(index + 1, Math.max(filtered.length - 1, 0))
            );
          } else if (event.key === "ArrowUp") {
            event.preventDefault();
            setActiveIndex((index) => Math.max(index - 1, 0));
          } else if (event.key === "Enter" && open && filtered[activeIndex]) {
            event.preventDefault();
            choose(filtered[activeIndex]);
          } else if (event.key === "Escape") {
            setOpen(false);
          }
        }}
        placeholder="搜索国家、城市或输入 IANA 时区"
        spellCheck={false}
        autoComplete="off"
        className="pl-9 pr-10 font-mono"
      />
      <button
        type="button"
        onClick={() => setOpen((current) => !current)}
        aria-label="展开常用时区"
        className="absolute right-1 top-1/2 z-10 -translate-y-1/2 rounded p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"
      >
        <ChevronDown
          className={cn("size-4 transition-transform", open && "rotate-180")}
        />
      </button>

      {open && (
        <div
          id="scheduled-task-timezone-options"
          role="listbox"
          className="absolute z-50 mt-1 max-h-64 w-full overflow-auto rounded-md border border-border bg-popover p-1 text-popover-foreground shadow-lg"
        >
          {filtered.length > 0 ? (
            filtered.map((option, index) => (
              <button
                key={option.value}
                type="button"
                role="option"
                aria-selected={option.value === value.trim()}
                onMouseDown={(event) => event.preventDefault()}
                onMouseEnter={() => setActiveIndex(index)}
                onClick={() => choose(option)}
                className={cn(
                  "flex w-full items-center gap-2 rounded-sm px-2.5 py-2 text-left",
                  index === activeIndex && "bg-accent",
                  option.value === value.trim() && "text-[var(--brand)]"
                )}
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium">
                    {option.label}
                  </span>
                  <span className="block truncate font-mono text-xs text-muted-foreground">
                    {option.value}
                  </span>
                </span>
                {option.value === value.trim() && (
                  <Check className="size-4 shrink-0" />
                )}
              </button>
            ))
          ) : (
            <div className="px-3 py-3 text-sm text-muted-foreground">
              没有匹配的常用时区；可继续输入完整 IANA 时区名称。
            </div>
          )}
        </div>
      )}
    </div>
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
  const [timezone, setTimezone] = useState(
    initialTask?.timezone || browserTimezone()
  );
  const [saving, setSaving] = useState(false);
  const nameRef = useRef<HTMLInputElement>(null);

  const cron = specToCron(spec);
  const cronError =
    spec.frequency === "custom" ? validateCronExpression(cron) : null;
  const timezoneError = isValidTimezone(timezone.trim())
    ? null
    : "请输入有效的 IANA 时区，例如 Asia/Shanghai。";
  const canSave = Boolean(
    name.trim() && prompt.trim() && !cronError && !timezoneError
  );

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
    if (timezoneError) {
      toast.error(timezoneError);
      return;
    }

    setSaving(true);
    try {
      if (initialTask) {
        const task = await updateScheduledTask({
          cronId: initialTask.cron_id,
          taskKey: initialTask.task_key,
          name: name.trim(),
          prompt: prompt.trim(),
          schedule: cron,
          timezone: timezone.trim(),
        });
        toast.success(`“${name.trim()}”已更新。`);
        onSaved(task);
      } else {
        await createScheduledTask({
          name: name.trim(),
          prompt: prompt.trim(),
          schedule: cron,
          timezone: timezone.trim(),
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

          <div className="space-y-1.5">
            <FieldLabel htmlFor="scheduled-task-timezone">时区</FieldLabel>
            <TimezoneCombobox
              value={timezone}
              onChange={setTimezone}
              invalid={Boolean(timezoneError)}
            />
            <p
              className={cn(
                "text-xs",
                timezoneError ? "text-destructive" : "text-muted-foreground"
              )}
            >
              {timezoneError ?? `计划将按 ${timezone.trim()} 的当地时间执行。`}
            </p>
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
  tasks: ScheduledTask[];
  onBack: () => void;
  onEdit: () => void;
  onDeleted: () => void;
}

const RUN_STATUS_LABEL: Record<ScheduledTaskRun["status"], string> = {
  pending: "等待中",
  running: "运行中",
  completed: "已完成",
  failed: "失败",
  timeout: "超时",
  interrupted: "已中断",
  unknown: "状态未知",
};

function formatDuration(record: ScheduledTaskRun): string {
  if (!record.completed_at) return "正在计时";
  const duration =
    Date.parse(record.completed_at) - Date.parse(record.started_at);
  if (!Number.isFinite(duration) || duration < 0) return "耗时未知";
  const seconds = Math.round(duration / 1000);
  if (seconds < 60) return `${seconds} 秒`;
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  if (minutes < 60) return `${minutes} 分 ${remainder} 秒`;
  return `${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分`;
}

function RunStatusIcon({ status }: { status: ScheduledTaskRun["status"] }) {
  if (status === "running" || status === "pending") {
    return <Loader2 className="size-4 animate-spin text-[var(--brand)]" />;
  }
  if (status === "completed") {
    return <CheckCircle2 className="size-4 text-emerald-500" />;
  }
  if (status === "interrupted") {
    return <Square className="size-4 text-muted-foreground" />;
  }
  return <AlertTriangle className="size-4 text-destructive" />;
}

function RunRecordCard({
  record,
  timezone,
  defaultExpanded,
}: {
  record: ScheduledTaskRun;
  timezone: string;
  defaultExpanded: boolean;
}) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  return (
    <div className="rounded-md border border-border bg-[var(--color-surface)]">
      <button
        type="button"
        onClick={() => setExpanded((value) => !value)}
        aria-expanded={expanded}
        className="flex w-full items-start gap-2 px-3 py-2.5 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
      >
        <span className="mt-0.5">
          <RunStatusIcon status={record.status} />
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm font-medium">
            <span>{RUN_STATUS_LABEL[record.status]}</span>
            <span className="text-xs font-normal text-muted-foreground">
              {record.trigger === "manual"
                ? "手动触发"
                : record.trigger === "scheduled"
                  ? "定时触发"
                  : "触发方式未知"}
            </span>
          </span>
          <span className="mt-0.5 block text-xs tabular-nums text-muted-foreground">
            {formatLongDate(record.started_at, timezone)} · {timezone} · {formatDuration(record)}
          </span>
        </span>
        <ChevronDown
          className={cn(
            "mt-1 size-4 shrink-0 text-muted-foreground transition-transform",
            expanded && "rotate-180"
          )}
        />
      </button>
      {expanded && (
        <div className="border-t border-border px-3 py-3">
          {record.feedback ? (
            <MarkdownContent content={record.feedback} />
          ) : record.status === "running" || record.status === "pending" ? (
            <p className="text-sm text-muted-foreground">
              Agent 正在工作，最终反馈尚未生成。
            </p>
          ) : (
            <p className="text-sm text-muted-foreground">
              本次运行没有生成可展示的最终反馈。
            </p>
          )}
        </div>
      )}
    </div>
  );
}

function TaskExecutionHistory({
  task,
  tasks,
  refreshRevision,
}: {
  task: ScheduledTask;
  tasks: ScheduledTask[];
  refreshRevision: number;
}) {
  const [records, setRecords] = useState<ScheduledTaskRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pageSize, setPageSize] = useState(20);
  const [hasMore, setHasMore] = useState(false);
  const hasActiveRun = records.some(
    (record) => record.status === "running" || record.status === "pending"
  );

  const loadRecords = useCallback(async () => {
    try {
      const page = await listScheduledTaskRuns({
        task,
        tasks,
        limit: pageSize,
      });
      setRecords(page.records);
      setHasMore(page.hasMore);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载执行记录失败。");
    } finally {
      setLoading(false);
    }
  }, [pageSize, task, tasks]);

  useEffect(() => {
    void loadRecords();
    const timer = window.setInterval(
      () => void loadRecords(),
      hasActiveRun ? 2_000 : 30_000
    );
    return () => window.clearInterval(timer);
  }, [hasActiveRun, loadRecords, refreshRevision]);

  return (
    <section className="space-y-2">
      <div className="flex items-center justify-between gap-2">
        <div>
          <p className="text-sm font-semibold">执行记录</p>
          <p className="text-xs text-muted-foreground">
            每次运行使用独立会话，结果按完成时间保留。
          </p>
        </div>
        <button
          type="button"
          onClick={() => void loadRecords()}
          disabled={loading}
          className="rounded-md p-2 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:opacity-50"
          aria-label="刷新执行记录"
        >
          <RefreshCw className={cn("size-4", loading && "animate-spin")} />
        </button>
      </div>

      {loading && records.length === 0 ? (
        <div className="flex items-center gap-2 rounded-md border border-border px-3 py-4 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" />
          正在加载执行记录…
        </div>
      ) : error ? (
        <div className="rounded-md border border-destructive/40 bg-destructive/5 p-3">
          <p className="text-sm text-destructive">{error}</p>
          <Button
            variant="outline"
            size="sm"
            className="mt-2"
            onClick={() => void loadRecords()}
          >
            重试
          </Button>
        </div>
      ) : records.length === 0 ? (
        <div className="rounded-md border border-dashed border-border px-3 py-5 text-center">
          <p className="text-sm font-medium">暂无执行结果</p>
          <p className="mt-1 text-xs text-muted-foreground">
            自动运行或点击“立即运行”后，Agent 反馈会显示在这里。
          </p>
        </div>
      ) : (
        <div className="space-y-2">
          {records.map((record, index) => (
            <RunRecordCard
              key={record.run_id}
              record={record}
              timezone={task.timezone}
              defaultExpanded={index === 0}
            />
          ))}
          {hasMore && (
            <Button
              variant="outline"
              size="sm"
              className="w-full"
              onClick={() => {
                setLoading(true);
                setPageSize((size) => size + 20);
              }}
            >
              加载更多
            </Button>
          )}
        </div>
      )}
    </section>
  );
}

function TaskDetail({
  task,
  tasks,
  onBack,
  onEdit,
  onDeleted,
}: TaskDetailProps) {
  const [running, setRunning] = useState(false);
  const [runRefreshRevision, setRunRefreshRevision] = useState(0);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const nextRunTitle = formatLongDate(task.next_run_date, task.timezone);

  const handleRunNow = useCallback(async () => {
    setRunning(true);
    try {
      await runScheduledTaskNow(task);
      toast.success(`“${task.name}”已启动。`);
      setRunRefreshRevision((revision) => revision + 1);
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
      await archiveScheduledTask(task);
      toast.success(`“${task.name}”已停止调度，历史结果已保留。`);
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
              {task.archived
                ? "历史任务 · 已停止调度"
                : `${cronLabel(task.schedule)} · ${task.timezone}`}
            </p>
          </div>
          <Button
            size="sm"
            onClick={handleRunNow}
            disabled={task.archived || running || !task.prompt.trim()}
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
            <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
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
                      )} · ${formatAbsoluteDate(
                        task.next_run_date,
                        task.timezone
                      )}`
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
                  时区
                </p>
                <p className="mt-1 truncate text-sm">{task.timezone}</p>
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

            <TaskExecutionHistory
              task={task}
              tasks={tasks}
              refreshRevision={runRefreshRevision}
            />
          </div>
        </ScrollArea>

        <div className="flex flex-shrink-0 items-center justify-between gap-2 border-t border-border bg-background px-3 py-2.5 sm:px-5">
          <p className="hidden truncate text-xs text-muted-foreground sm:block">
            关闭浏览器不影响任务；关闭本地后端或电脑后将不会执行。
          </p>
          <div className="ml-auto flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={onEdit}
              disabled={task.archived}
              aria-label={`编辑定时任务“${task.name}”`}
            >
              <Pencil className="size-3.5" />
              编辑
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setDeleteOpen(true)}
              disabled={task.archived}
              aria-label={`删除定时任务“${task.name}”`}
              className="text-destructive hover:border-destructive hover:text-destructive"
            >
              <Trash2 className="size-3.5" />
              停止调度
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
            <DialogTitle>停止这项定时任务？</DialogTitle>
            <DialogDescription>
              “{task.name}”将不再自动运行，已有执行记录会保留在“历史任务”中。
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
              停止调度
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

function UnassignedRunsDetail({
  tasks,
  onBack,
}: {
  tasks: ScheduledTask[];
  onBack: () => void;
}) {
  const [records, setRecords] = useState<ScheduledTaskRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const timezone = browserTimezone();

  const load = useCallback(async () => {
    try {
      setRecords(await listUnassignedScheduledRuns(tasks));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载未归属记录失败。");
    } finally {
      setLoading(false);
    }
  }, [tasks]);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 30_000);
    return () => window.clearInterval(timer);
  }, [load]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center gap-2 border-b border-border px-3 py-2.5 sm:px-5">
        <button
          type="button"
          onClick={onBack}
          aria-label="返回定时任务列表"
          className="rounded-md p-1.5 text-muted-foreground hover:bg-accent md:hidden"
        >
          <ArrowLeft className="size-4" />
        </button>
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-semibold">未归属记录</h2>
          <p className="text-xs text-muted-foreground">
            提示词无法唯一对应任务的旧调度会话
          </p>
        </div>
        <button
          type="button"
          onClick={() => void load()}
          className="rounded-md p-2 text-muted-foreground hover:bg-accent"
          aria-label="刷新未归属记录"
        >
          <RefreshCw className={cn("size-4", loading && "animate-spin")} />
        </button>
      </div>
      <ScrollArea className="min-h-0 flex-1">
        <div className="mx-auto w-full max-w-3xl space-y-2 p-3 sm:p-5">
          {error ? (
            <p className="rounded-md border border-destructive/40 p-3 text-sm text-destructive">
              {error}
            </p>
          ) : loading && records.length === 0 ? (
            <p className="text-sm text-muted-foreground">正在加载…</p>
          ) : records.length === 0 ? (
            <div className="rounded-md border border-dashed border-border p-5 text-center">
              <p className="text-sm font-medium">没有未归属记录</p>
              <p className="mt-1 text-xs text-muted-foreground">
                旧记录只有在提示词唯一匹配时才会自动归入任务。
              </p>
            </div>
          ) : (
            records.map((record, index) => (
              <RunRecordCard
                key={record.run_id}
                record={record}
                timezone={timezone}
                defaultExpanded={index === 0}
              />
            ))
          )}
        </div>
      </ScrollArea>
    </div>
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
      <span
        className={cn(
          "mt-1 flex size-2 shrink-0 rounded-full",
          task.archived ? "bg-muted-foreground/50" : "bg-[var(--brand)]"
        )}
      />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-medium">{task.name}</span>
        <span className="mt-0.5 block truncate text-xs text-muted-foreground">
          {task.archived
            ? "已停止调度"
            : `${cronLabel(task.schedule)} · ${task.timezone}`}
        </span>
        <span className="mt-1 flex min-w-0 items-center gap-1.5 text-[11px] text-muted-foreground">
          <Clock
            className="size-3 shrink-0"
            aria-hidden="true"
          />
          <span
            className="truncate tabular-nums"
            title={formatLongDate(nextRun, task.timezone)}
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
  | { kind: "unassigned" }
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
  const activeFilteredTasks = filteredTasks.filter((task) => !task.archived);
  const archivedFilteredTasks = filteredTasks.filter((task) => task.archived);
  const activeTasks = tasks.filter((task) => !task.archived);
  const nextTask = sortedTasks.find(
    (task) => !task.archived && task.next_run_date
  );
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
                  {activeTasks.length}
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
                {activeFilteredTasks.length > 0 && (
                  <>
                    <div className="mb-2 flex items-center justify-between px-2 py-1">
                      <p className="text-[11px] font-semibold uppercase text-muted-foreground">
                        任务
                      </p>
                      <p className="text-[11px] text-muted-foreground">
                        {activeFilteredTasks.length}
                      </p>
                    </div>
                    <div className="flex flex-col gap-0.5">
                      {activeFilteredTasks.map((task) => (
                        <TaskRow
                          key={task.cron_id}
                          task={task}
                          active={
                            right.kind === "detail" &&
                            right.task.cron_id === task.cron_id
                          }
                          onSelect={() =>
                            setRight({ kind: "detail", task })
                          }
                        />
                      ))}
                    </div>
                  </>
                )}
                {archivedFilteredTasks.length > 0 && (
                  <div className="mt-3 border-t border-border pt-2">
                    <div className="mb-1 flex items-center justify-between px-2 py-1">
                      <p className="text-[11px] font-semibold uppercase text-muted-foreground">
                        历史任务
                      </p>
                      <p className="text-[11px] text-muted-foreground">
                        {archivedFilteredTasks.length}
                      </p>
                    </div>
                    <div className="flex flex-col gap-0.5">
                      {archivedFilteredTasks.map((task) => (
                        <TaskRow
                          key={task.cron_id}
                          task={task}
                          active={
                            right.kind === "detail" &&
                            right.task.cron_id === task.cron_id
                          }
                          onSelect={() =>
                            setRight({ kind: "detail", task })
                          }
                        />
                      ))}
                    </div>
                  </div>
                )}
                <div className="mt-3 border-t border-border pt-2">
                  <button
                    type="button"
                    onClick={() => setRight({ kind: "unassigned" })}
                    className={cn(
                      "flex w-full items-center gap-2 rounded-md px-2 py-2 text-left text-sm transition-colors hover:bg-accent/60",
                      right.kind === "unassigned" && "bg-accent"
                    )}
                  >
                    <AlertTriangle className="size-4 text-muted-foreground" />
                    <span className="min-w-0 flex-1">未归属记录</span>
                    <ChevronRight className="size-3.5 text-muted-foreground" />
                  </button>
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

          {right.kind === "unassigned" && (
            <UnassignedRunsDetail
              tasks={tasks}
              onBack={() => setRight({ kind: "empty" })}
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
              tasks={tasks}
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
