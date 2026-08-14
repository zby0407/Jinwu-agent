const TERMINAL_PREFIX = "[RESEARCH REVIEW TERMINAL]";

/**
 * @typedef {Object} ResearchTerminal
 * @property {"blocked"} status
 * @property {string} reasonCode
 * @property {string} [stage]
 * @property {string} [producer]
 * @property {number} [failureCount]
 * @property {string} [summary]
 * @property {"new_task_after_fix"} [recovery]
 * @property {string} [raw]
 */

/**
 * Parse persisted protocol text emitted by older backends.
 * @param {string} content
 * @returns {ResearchTerminal | null}
 */
export function parseResearchTerminalMessage(content) {
  if (typeof content !== "string") return null;
  const raw = content.trim();
  if (!raw.startsWith(TERMINAL_PREFIX)) return null;
  const statusMatch = raw.match(/\bstatus=([^;.]+)/i);
  const reasonMatch = raw.match(/\breason=([^;.]+)/i);
  const status = statusMatch?.[1]?.trim();
  if (status !== "blocked") return null;
  return {
    status,
    reasonCode: normalizeReasonCode(reasonMatch?.[1]),
    recovery: "new_task_after_fix",
    raw,
  };
}

function normalizeReasonCode(value) {
  const reason = String(value || "UNRESOLVED_REVIEW_GATE").trim();
  if (/^[A-Z][A-Z0-9_]+$/.test(reason)) return reason;
  return "UNRESOLVED_REVIEW_GATE";
}

const STAGE_LABELS = {
  planning: "研究规划",
  data: "数据处理",
  hypothesis: "科学假设",
  experiment_design: "实验设计",
  experiment_result: "实验执行",
  integration: "综合审查",
  final_release: "发布审查",
};

/**
 * Build user-facing Chinese copy from stable terminal codes.
 * @param {ResearchTerminal} terminal
 */
export function describeResearchTerminal(terminal) {
  const stageLabel =
    STAGE_LABELS[terminal.stage] || terminal.stage || "科研流程";
  if (terminal.reasonCode === "REQUIRED_SPECIALIST_FAILED_TWICE") {
    const producer = terminal.producer || "必需的专业 Agent";
    const count = terminal.failureCount || 2;
    const failureStageLabel = terminal.stage ? stageLabel : "数据处理";
    return {
      title: `${failureStageLabel}未完成`,
      description: `${producer} 连续 ${count} 次未生成可审查的任务产物，系统已停止本轮，避免重复执行或伪造结果。`,
      action:
        "修复数据产物流程后，请新建一个任务重新运行；当前失败记录会继续保留用于审计。",
      tone: "blocked",
    };
  }
  if (terminal.reasonCode === "RESEARCH_ACTION_BUDGET_EXHAUSTED") {
    return {
      title: "科研动作预算已用完",
      description: "本轮已达到允许的最大动作次数，系统停止继续调用工具。",
      action: "请缩小任务范围，或新建任务后分阶段执行。",
      tone: "blocked",
    };
  }
  return {
    title: `${stageLabel}已停止`,
    description:
      terminal.summary || "科研审查闸门尚未满足，系统没有宣称任务已经完成。",
    action: "请查看技术详情和科研证据审查面板，修复原因后新建任务重试。",
    tone: "blocked",
  };
}
