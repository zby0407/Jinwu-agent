import {
  mkdir,
  readdir,
  readFile,
  realpath,
  stat,
  writeFile,
} from "node:fs/promises";
import { homedir } from "node:os";
import { isAbsolute, join, relative, resolve, sep } from "node:path";

function parseFrontmatter(md) {
  const match = md.match(/^---\s*\n([\s\S]*?)\n---/);
  if (!match) return {};
  const get = (key) => {
    const value = match[1].match(new RegExp(`^${key}\\s*:\\s*(.+?)\\s*$`, "m"));
    return value ? value[1].replace(/^['"]|['"]$/g, "").trim() : undefined;
  };
  return { name: get("name"), description: get("description") };
}

async function countFiles(root) {
  let count = 0;
  for (const entry of await readdir(root, { withFileTypes: true })) {
    if (entry.name.startsWith(".")) continue;
    count += entry.isDirectory() ? await countFiles(join(root, entry.name)) : 1;
  }
  return count;
}

const REGISTRY_FILE = join("jw", "subagents", "skill_registry.json");
const SAFE_NAME = /^[a-z0-9][a-z0-9._-]{1,80}$/;

async function readRegistry(projectRoot) {
  try {
    const value = JSON.parse(
      await readFile(join(projectRoot, REGISTRY_FILE), "utf8")
    );
    if (!value || typeof value !== "object")
      throw new Error("invalid registry");
    return {
      version: Number(value.version) || 1,
      primaryAgents: Array.isArray(value.primary_agents)
        ? value.primary_agents
        : [],
      supportAgents: Array.isArray(value.support_agents)
        ? value.support_agents
        : [],
      main: Array.isArray(value.main)
        ? value.main.filter((x) => typeof x === "string")
        : [],
      agentProfiles:
        value.agent_profiles && typeof value.agent_profiles === "object"
          ? value.agent_profiles
          : {},
      shared: Array.isArray(value.shared)
        ? value.shared.filter((x) => typeof x === "string")
        : [],
      agents:
        value.agents && typeof value.agents === "object" ? value.agents : {},
      adaptations:
        value.adaptations && typeof value.adaptations === "object"
          ? value.adaptations
          : {},
    };
  } catch {
    return {
      version: 1,
      primaryAgents: [],
      supportAgents: [],
      main: [],
      agentProfiles: {},
      shared: [],
      agents: {},
      adaptations: {},
    };
  }
}

export async function readSkillTopology(projectRoot) {
  const registry = await readRegistry(projectRoot);
  const profile = (name) => {
    const value = registry.agentProfiles[name];
    return {
      name,
      title:
        value && typeof value.title === "string" && value.title.trim()
          ? value.title.trim()
          : name,
      description:
        value &&
        typeof value.description === "string" &&
        value.description.trim()
          ? value.description.trim()
          : "",
    };
  };
  return {
    mainAgent: profile("JW"),
    primaryAgents: registry.primaryAgents.map(profile),
    supportAgents: registry.supportAgents.map(profile),
  };
}

export function buildPrimaryAgentGroups(primaryAgents, skillsByAgent) {
  return primaryAgents.map(({ name, title, description }) => ({
    name,
    title,
    description,
    skills: skillsByAgent.get(name) || [],
  }));
}

function assignmentFor(registry, name) {
  const agents = Object.entries(registry.agents)
    .filter(([, names]) => Array.isArray(names) && names.includes(name))
    .map(([agent]) => agent);
  const shared = registry.shared.includes(name);
  const main = registry.main.includes(name);
  const scoped = main ? ["JW", ...agents] : agents;
  return { shared, agents: shared ? ["all", ...scoped] : scoped };
}

function localSkillRoots() {
  const configured = process.env.JW_SKILLS_IMPORT_DIRS;
  if (configured)
    return configured
      .split(/[;,]/)
      .map((x) => x.trim())
      .filter(Boolean);
  return [
    join(homedir(), ".agents", "skills"),
    join(homedir(), ".codex", "skills"),
  ];
}

function safeWithin(root, candidate) {
  const rootAbs = resolve(root);
  const candidateAbs = resolve(candidate);
  const rel = relative(rootAbs, candidateAbs);
  return (
    rel === "" ||
    (!rel.startsWith(".." + sep) && rel !== ".." && !isAbsolute(rel))
  );
}

function adaptedName(sourceName, registry) {
  const mapped = registry.adaptations[sourceName]?.name;
  if (typeof mapped === "string" && SAFE_NAME.test(mapped)) return mapped;
  return SAFE_NAME.test(`jw-${sourceName}`)
    ? `jw-${sourceName}`
    : `jw-imported-skill`;
}

function adaptMarkdown(sourceName, markdown, registry) {
  const name = adaptedName(sourceName, registry);
  const body = markdown.replace(/^---\s*\n[\s\S]*?\n---\s*\n?/, "").trim();
  const heading =
    name === "solar-evidence-figure-production"
      ? "# JW 太阳周期证据图生产\n\n"
      : "";
  return `---\nname: ${name}\ndescription: JW 项目适配技能（来源技能 ${sourceName}）；已补充数据回执、科学有效性和发布边界。\n---\n\n${heading}${body}\n\n## JW 项目适配边界\n\n本技能已适配 JW 太阳周期科研闭环。使用时必须绑定当前任务的输入回执，区分观测、代理量、预测和机制解释，并保留不确定性与负结果。不得把本地路径、旧运行产物或模型自述当作科学证据；若数据或语义不足，返回 revise 或 blocked。`;
}

/** Read the read-only Skills shipped inside the JW source distribution. */
export async function readBundledSkills(projectRoot) {
  const registry = await readRegistry(projectRoot);
  const subagents = join(projectRoot, "jw", "subagents");
  let bundles;
  try {
    bundles = await readdir(subagents, { withFileTypes: true });
  } catch {
    return [];
  }

  const skills = [];
  const seen = new Set();
  for (const bundle of bundles.sort((a, b) => a.name.localeCompare(b.name))) {
    if (!bundle.isDirectory() || bundle.name.startsWith(".")) continue;
    const skillsRoot = join(subagents, bundle.name, "skills");
    let entries;
    try {
      entries = await readdir(skillsRoot, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const entry of entries.sort((a, b) => a.name.localeCompare(b.name))) {
      if (!entry.isDirectory() || entry.name.startsWith(".")) continue;
      const skillDir = join(skillsRoot, entry.name);
      try {
        const md = await readFile(join(skillDir, "SKILL.md"), "utf8");
        const frontmatter = parseFrontmatter(md);
        // Deprecated source names are never presented as JW skills once an
        // explicit adaptation is registered; users see the project name.
        if (registry.adaptations[entry.name]?.name) continue;
        if (seen.has(entry.name)) continue;
        seen.add(entry.name);
        skills.push({
          name: entry.name,
          title: frontmatter.name || entry.name,
          description: frontmatter.description || "",
          dir: skillDir,
          source: "builtin",
          bundle: bundle.name,
          assignment: assignmentFor(registry, entry.name),
          adaptation: registry.adaptations[entry.name] || null,
          fileCount: await countFiles(skillDir),
        });
      } catch {
        // A bundle entry without a readable SKILL.md is not a Skill.
      }
    }
  }
  return skills.sort((a, b) => a.name.localeCompare(b.name));
}

/** Adapt bundled read-only Skills to the remote catalog response shape. */
export function bundledSkillsAsCatalog(skills) {
  return skills.map(
    ({
      name,
      title,
      description,
      fileCount = 1,
      assignment,
      bundle,
      adaptation,
    }) => {
      const entry = {
        name,
        title,
        description,
        fileCount,
        installed: true,
        updateAvailable: false,
      };
      if (assignment) entry.assignment = assignment;
      if (bundle) entry.bundle = bundle;
      if (adaptation) entry.adaptation = adaptation;
      return entry;
    }
  );
}

export async function readLocalSkillCandidates(projectRoot) {
  const registry = await readRegistry(projectRoot);
  const candidates = [];
  for (const root of localSkillRoots()) {
    let entries;
    let realRoot;
    try {
      realRoot = await realpath(root);
      entries = await readdir(realRoot, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const entry of entries) {
      if (!entry.isDirectory() || entry.name.startsWith(".")) continue;
      const dir = join(root, entry.name);
      let realDir;
      try {
        realDir = await realpath(dir);
      } catch {
        continue;
      }
      if (!safeWithin(realRoot, realDir) || !SAFE_NAME.test(entry.name))
        continue;
      try {
        const metadata = await stat(join(dir, "SKILL.md"));
        if (!metadata.isFile() || metadata.size > 512 * 1024) continue;
        const md = await readFile(join(dir, "SKILL.md"), "utf8");
        const frontmatter = parseFrontmatter(md);
        const adapted = adaptedName(entry.name, registry);
        const adaptation = registry.adaptations[entry.name];
        // A one-click import is allowed only for an explicitly reviewed
        // mapping. Codex skills remain reference-only and are never edited.
        if (
          !adaptation ||
          adaptation.reviewed !== true ||
          adaptation.adapted_copy_only !== true
        )
          continue;
        const targetAgents = adaptation.target_agents || [];
        const displayTitle =
          adapted === "solar-evidence-figure-production"
            ? "JW 太阳周期证据图生产"
            : adapted === "jw-release-export-qa"
            ? "JW 科研结果发布质检"
            : adapted;
        const targetBundle = targetAgents.some((x) => x.startsWith("solar-"))
          ? "solar"
          : "core";
        try {
          if (
            (
              await stat(
                join(
                  projectRoot,
                  "jw",
                  "subagents",
                  targetBundle,
                  "skills",
                  adapted,
                  "SKILL.md"
                )
              )
            ).isFile()
          )
            continue;
        } catch {
          /* not imported yet */
        }
        candidates.push({
          name: entry.name,
          title: displayTitle,
          description: frontmatter.description || "",
          source: "local",
          sourceRoot: root,
          adaptedName: adapted,
          targetAgents,
          imported: false,
        });
      } catch {
        /* no valid SKILL.md */
      }
    }
  }
  return candidates.sort((a, b) => a.name.localeCompare(b.name));
}

export async function importLocalSkills(projectRoot, names = []) {
  const registry = await readRegistry(projectRoot);
  const candidates = await readLocalSkillCandidates(projectRoot);
  const wanted = names.length
    ? new Set(names)
    : new Set(candidates.map((x) => x.name));
  const imported = [];
  for (const candidate of candidates) {
    if (!wanted.has(candidate.name)) continue;
    const sourceDir = join(candidate.sourceRoot, candidate.name);
    const targetName = candidate.adaptedName;
    const targetBundle = candidate.targetAgents.some((x) =>
      x.startsWith("solar-")
    )
      ? "solar"
      : "core";
    const targetDir = join(
      projectRoot,
      "jw",
      "subagents",
      targetBundle,
      "skills",
      targetName
    );
    if (!safeWithin(join(projectRoot, "jw", "subagents"), targetDir))
      throw new Error("Skill 目标路径无效");
    await mkdir(targetDir, { recursive: true });
    const sourceMarkdown = await readFile(join(sourceDir, "SKILL.md"), "utf8");
    let adaptedMarkdown = adaptMarkdown(
      candidate.name,
      sourceMarkdown,
      registry
    );
    if (targetName === "solar-evidence-figure-production") {
      try {
        adaptedMarkdown = await readFile(
          join(
            projectRoot,
            "jw",
            "subagents",
            "solar",
            "skills",
            targetName,
            "SKILL.md"
          ),
          "utf8"
        );
      } catch {
        /* a minimal test or external bundle may not ship the canonical file */
      }
    }
    await writeFile(join(targetDir, "SKILL.md"), adaptedMarkdown, "utf8");
    for (const [sourceName, adaptation] of Object.entries(
      registry.adaptations
    )) {
      if (adaptation?.name === targetName) {
        registry.agents = registry.agents || {};
        for (const agent of adaptation.target_agents || []) {
          const current = Array.isArray(registry.agents[agent])
            ? registry.agents[agent]
            : [];
          if (!current.includes(targetName)) current.push(targetName);
          registry.agents[agent] = current;
        }
      }
    }
    imported.push({
      sourceName: candidate.name,
      name: targetName,
      targetBundle,
      agents: candidate.targetAgents,
    });
  }
  if (imported.length)
    await writeFile(
      join(projectRoot, REGISTRY_FILE),
      JSON.stringify(registry, null, 2) + "\n",
      "utf8"
    );
  return imported;
}
