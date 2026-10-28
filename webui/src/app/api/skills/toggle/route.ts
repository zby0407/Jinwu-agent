import { NextRequest, NextResponse } from "next/server";
import { isValidSkillName } from "@/lib/server/skills";
import { setSkillEnabled } from "@/lib/server/skillSettings";

export const runtime = "nodejs";

/**
 * 开关单个 skill。状态写入 DATA_DIR/skill_settings.json（与 Python runtime
 * 共享），`/api/skills` GET 会把结果以 `enabled` 字段暴露给前端。
 */
export async function POST(req: NextRequest) {
  const body = (await req.json().catch(() => ({}))) as {
    name?: unknown;
    enabled?: unknown;
  };
  const { name, enabled } = body;
  if (typeof name !== "string" || !isValidSkillName(name)) {
    return NextResponse.json({ error: "Skill 名称无效。" }, { status: 400 });
  }
  if (typeof enabled !== "boolean") {
    return NextResponse.json(
      { error: "缺少有效的开关状态（enabled 必须是布尔值）。" },
      { status: 400 }
    );
  }
  try {
    await setSkillEnabled(name, enabled);
    // 只回 ok/name/enabled：不把 DATA_DIR 的绝对路径暴露给浏览器。
    return NextResponse.json({ ok: true, name, enabled });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return NextResponse.json(
      { error: `写入 Skills 开关失败: ${message}` },
      { status: 500 }
    );
  }
}
