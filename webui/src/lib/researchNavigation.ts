export const RESEARCH_MESSAGE_NAVIGATE_EVENT = "jw:research-message-navigate";

export interface ResearchMessageNavigateDetail {
  messageId: string;
  branch?: string;
}

export function dispatchResearchMessageNavigation(
  detail: ResearchMessageNavigateDetail
): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(
    new CustomEvent<ResearchMessageNavigateDetail>(
      RESEARCH_MESSAGE_NAVIGATE_EVENT,
      { detail }
    )
  );
}
