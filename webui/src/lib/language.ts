/** Lightweight language guess for literature titles/abstracts. */

export interface LanguageGuess {
  code: "zh" | "ja" | "en" | "other";
  label: string;
}

export function detectTextLanguage(text: string): LanguageGuess {
  if (!text.trim()) return { code: "other", label: "未知" };
  const kana = (text.match(/[\u3040-\u30ff]/g) ?? []).length;
  const han = (text.match(/[\u4e00-\u9fff]/g) ?? []).length;
  const latin = (text.match(/[a-zA-Z]/g) ?? []).length;
  if (kana > 0 && kana >= han * 0.2) return { code: "ja", label: "日语" };
  if (han > 0 && han >= latin * 0.5) return { code: "zh", label: "中文" };
  if (latin > 0) return { code: "en", label: "英文" };
  return { code: "other", label: "其他" };
}
