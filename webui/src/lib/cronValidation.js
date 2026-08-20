const FIELD_NAMES = ["分钟", "小时", "日期", "月份", "星期"];
const FIELD_RANGES = [
  [0, 59],
  [0, 23],
  [1, 31],
  [1, 12],
  [0, 7],
];

function validateAtom(atom, min, max) {
  if (atom === "*") return true;

  const [base, stepText] = atom.split("/");
  if (atom.split("/").length > 2) return false;
  if (stepText !== undefined) {
    const step = Number(stepText);
    if (!Number.isInteger(step) || step < 1 || step > max - min + 1) {
      return false;
    }
  }

  if (base === "*") return true;
  const range = base.split("-");
  if (range.length > 2) return false;
  return range.every((value) => {
    if (!/^\d+$/.test(value)) return false;
    const parsed = Number(value);
    return parsed >= min && parsed <= max;
  }) && (range.length === 1 || Number(range[0]) <= Number(range[1]));
}

function validateField(field, min, max) {
  if (!field || field.startsWith(",") || field.endsWith(",")) return false;
  return field.split(",").every((atom) => validateAtom(atom, min, max));
}

export function validateFivePartCron(cron) {
  const trimmed = cron.trim();
  if (!trimmed) return "请输入 Cron 表达式。";
  const parts = trimmed.split(/\s+/);
  if (parts.length !== 5) {
    return "请使用五段 Cron 字段：分钟、小时、日期、月份、星期。";
  }

  for (let index = 0; index < parts.length; index += 1) {
    const [min, max] = FIELD_RANGES[index];
    if (!validateField(parts[index], min, max)) {
      return `${FIELD_NAMES[index]}字段无效，应在 ${min}–${max} 范围内，并仅使用数字、*、逗号、连字符或步长。`;
    }
  }
  return null;
}
