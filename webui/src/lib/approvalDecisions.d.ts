export function submitApprovalDecision(
  pending: { current: Record<number, unknown> },
  count: number,
  index: number,
  decision: unknown,
  resume: (batch: { decisions: unknown[] }) => Promise<boolean>
): Promise<boolean>;
