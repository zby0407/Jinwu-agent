/** Keep collected decisions until the complete approval batch is accepted. */
export async function submitApprovalDecision(pending, count, index, decision, resume) {
  const next = { ...pending.current, [index]: decision };
  pending.current = next;
  if (!Array.from({ length: count }, (_, i) => i).every((i) => next[i])) return true;
  const accepted = await resume({ decisions: Array.from({ length: count }, (_, i) => next[i]) });
  // A completed request from an older card/session must not erase a newer batch.
  if (accepted && pending.current === next) pending.current = {};
  return accepted;
}
