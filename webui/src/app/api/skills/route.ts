import { NextRequest, NextResponse } from "next/server";
import { join, resolve, sep } from "path";
import { promises as fs } from "fs";
import {
  SKILL_DIRS,
  recordUninstall,
  isValidSkillName,
} from "@/lib/server/skills";
import {
  buildPrimaryAgentGroups,
  importLocalSkills,
  readBundledSkills,
  readLocalSkillCandidates,
  readSkillTopology,
} from "@/lib/server/builtinSkills.js";

// SKILL_DIRS (the global ~/.jw/skills tier + legacy ~/.config
// fallback) is the single source of truth, shared with the install route.

interface SkillCard {
  /** Directory name — the install/uninstall identity (matches the catalog). */
  name: string;
  /** Frontmatter name for display; falls back to the directory name. */
  title: string;
  description: string;
  dir: string;
  source: "installed" | "builtin";
}

// Minimal frontmatter parse — we only need name + description. Avoids pulling
// in a YAML dependency.
function parseFrontmatter(md: string): { name?: string; description?: string } {
  const match = md.match(/^---\s*\n([\s\S]*?)\n---/);
  if (!match) return {};
  const fm = match[1];
  const get = (key: string) => {
    const m = fm.match(new RegExp(`^${key}\\s*:\\s*(.+?)\\s*$`, "m"));
    if (!m) return undefined;
    return m[1].replace(/^["']|["']$/g, "").trim();
  };
  return { name: get("name"), description: get("description") };
}

async function readSkills(): Promise<SkillCard[]> {
  const skills: SkillCard[] = [];
  const seen = new Set<string>();
  for (const dir of SKILL_DIRS) {
    let entries: string[] = [];
    let realRoot: string;
    try {
      entries = await fs.readdir(dir);
      realRoot = await fs.realpath(dir);
    } catch {
      continue; // dir doesn't exist
    }
    for (const entry of entries) {
      if (entry.startsWith(".")) continue;
      const skillDir = join(dir, entry);
      try {
        // Canonicalize so a symlinked skill dir / SKILL.md can't read outside
        // the tier (consistent with getSkillDetail's guard).
        const realDir = await fs.realpath(skillDir);
        if (realDir !== realRoot && !realDir.startsWith(realRoot + sep)) {
          continue;
        }
        const stat = await fs.stat(realDir);
        if (!stat.isDirectory()) continue;
        const md = await fs.readFile(join(realDir, "SKILL.md"), "utf-8");
        const { name, description } = parseFrontmatter(md);
        // Identity is the DIRECTORY name (what install/uninstall/dedup key on);
        // the frontmatter name is display-only.
        if (seen.has(entry)) continue;
        seen.add(entry);
        skills.push({
          name: entry,
          title: name || entry,
          description: description || "",
          dir: skillDir,
          source: "installed",
        });
      } catch {
        // no SKILL.md or unreadable — skip
      }
    }
  }
  const projectRoots = [resolve(process.cwd(), ".."), resolve(process.cwd())];
  const bundled = [];
  for (const projectRoot of projectRoots) {
    const entries = await readBundledSkills(projectRoot);
    if (entries.length > 0) {
      bundled.push(...entries);
      break;
    }
  }
  const bundledSeen = new Set(skills.map((skill) => skill.name));
  for (const skill of bundled) {
    if (!bundledSeen.has(skill.name)) {
      skills.push({ ...skill, source: "builtin" });
      bundledSeen.add(skill.name);
    }
  }
  return skills.sort((a, b) => {
    if (a.source !== b.source) return a.source === "builtin" ? -1 : 1;
    return a.name.localeCompare(b.name);
  });
}

export async function GET() {
  try {
    const skills = await readSkills();
    const projectRoots = [resolve(process.cwd(), ".."), resolve(process.cwd())];
    let bundled: Awaited<ReturnType<typeof readBundledSkills>> = [];
    let localCandidates: Awaited<ReturnType<typeof readLocalSkillCandidates>> =
      [];
    let topology = {
      primaryAgents: [] as Array<{
        name: string;
        title: string;
        description: string;
      }>,
      supportAgents: [] as Array<{
        name: string;
        title: string;
        description: string;
      }>,
    };
    for (const projectRoot of projectRoots) {
      bundled = await readBundledSkills(projectRoot);
      if (bundled.length > 0) {
        localCandidates = await readLocalSkillCandidates(projectRoot);
        topology = await readSkillTopology(projectRoot);
        break;
      }
    }
    const byAgent = new Map<string, SkillCard[]>();
    for (const skill of bundled) {
      for (const agent of skill.assignment?.agents || []) {
        if (agent === "all") continue;
        const list = byAgent.get(agent) || [];
        list.push({ ...skill, source: "builtin" as const });
        byAgent.set(agent, list);
      }
    }
    return NextResponse.json({
      skills,
      sharedSkills: bundled.filter((skill) => skill.assignment?.shared),
      agents: buildPrimaryAgentGroups(topology.primaryAgents, byAgent),
      primaryAgents: topology.primaryAgents.map((agent) => agent.name),
      primaryAgentProfiles: topology.primaryAgents,
      supportAgents: topology.supportAgents.map((agent) => agent.name),
      supportAgentProfiles: topology.supportAgents,
      localCandidates: localCandidates.map(
        ({ sourceRoot, ...candidate }) => candidate
      ),
    });
  } catch (e) {
    return NextResponse.json(
      { error: e instanceof Error ? e.message : "读取 Skills 失败" },
      { status: 500 }
    );
  }
}

/** Import selected local skills after deterministic JW adaptation/assignment. */
export async function POST(req: NextRequest) {
  try {
    const body = await req.json().catch(() => ({}));
    const names = Array.isArray(body?.names)
      ? body.names.filter((name: unknown) => typeof name === "string")
      : [];
    const projectRoots = [resolve(process.cwd(), ".."), resolve(process.cwd())];
    for (const projectRoot of projectRoots) {
      if ((await readBundledSkills(projectRoot)).length === 0) continue;
      const imported = await importLocalSkills(projectRoot, names);
      return NextResponse.json({ ok: true, imported });
    }
    return NextResponse.json(
      { error: "未找到 JW 项目技能目录" },
      { status: 404 }
    );
  } catch (e) {
    return NextResponse.json(
      { error: e instanceof Error ? e.message : "导入技能失败" },
      { status: 400 }
    );
  }
}

// Uninstall = remove the skill directory. Guard against path traversal and
// only delete inside the known skill dirs.
export async function DELETE(req: NextRequest) {
  const name = req.nextUrl.searchParams.get("name");
  // Strict name check (blocks dotfiles like `.installed.yaml`, traversal, odd
  // chars) — must match install-side validation, not the old slash/`..`-only one.
  if (!name || !isValidSkillName(name)) {
    return NextResponse.json({ error: "Skill 名称无效" }, { status: 400 });
  }
  for (const dir of SKILL_DIRS) {
    const target = resolve(join(dir, name));
    if (target !== resolve(dir) && !target.startsWith(resolve(dir) + sep)) {
      continue;
    }
    try {
      // Only ever remove an actual skill directory, never a stray file.
      const stat = await fs.stat(target);
      if (!stat.isDirectory()) continue;
    } catch {
      continue; // not here
    }
    await fs.rm(target, { recursive: true, force: true });
    // Keep JW's manifest in sync — drop the entry so onboard/CLI no
    // longer list it. Best-effort: don't fail the uninstall on a manifest error.
    await recordUninstall(name).catch(() => {});
    return NextResponse.json({ ok: true });
  }
  return NextResponse.json({ error: "未找到 Skill" }, { status: 404 });
}
