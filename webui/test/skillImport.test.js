// Tests for user-supplied skill imports (folder upload / .zip / server path).
//
// Like the other server-lib tests this loads `.ts` sources, so it registers a
// resolver hook that adds `.ts` candidates when plain resolution fails, and it
// points JW_DATA_DIR at a throwaway dir BEFORE importing anything (SKILLS_INSTALL_DIR
// is computed at module load).

import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import {
  mkdir,
  mkdtemp,
  readFile,
  readdir,
  rm,
  symlink,
  writeFile,
} from "node:fs/promises";
import { registerHooks } from "node:module";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";
import { deflateRawSync } from "node:zlib";

registerHooks({
  resolve(specifier, context, nextResolve) {
    try {
      return nextResolve(specifier, context);
    } catch (error) {
      if (!specifier.startsWith(".") || !context.parentURL) throw error;
      const url = new URL(specifier, context.parentURL);
      const candidates = url.pathname.endsWith(".js")
        ? [`${url.href.slice(0, -3)}.ts`]
        : [`${url.href}.ts`, `${url.href}/index.ts`];
      for (const candidate of candidates) {
        if (existsSync(new URL(candidate)))
          return nextResolve(candidate, context);
      }
      throw error;
    }
  },
});

const dataDir = await mkdtemp(join(tmpdir(), "jw-skill-import-"));
process.env.JW_DATA_DIR = dataDir;
delete process.env.agent_DATA_DIR;

const {
  importSkillFromDirectory,
  importSkillFromUploads,
  importSkillFromZip,
  readZipEntries,
  SkillImportError,
} = await import(
  new URL("../src/lib/server/skillImport.ts", import.meta.url).href
);
const {
  isValidSkillName,
  MAX_SKILL_BYTES,
  MAX_SKILL_FILES,
  SKILLS_INSTALL_DIR,
} = await import(new URL("../src/lib/server/skills.ts", import.meta.url).href);

const installRoot = join(dataDir, "skills");

/** Fresh, empty install dir for one test. */
async function freshInstallRoot(t) {
  await rm(installRoot, { recursive: true, force: true });
  await mkdir(installRoot, { recursive: true });
  t.after(() => rm(installRoot, { recursive: true, force: true }));
  return installRoot;
}

/** Build a throwaway source directory from `{ "rel/path": content }`. */
async function makeTree(t, files, name) {
  const root = await mkdtemp(join(tmpdir(), "jw-skill-src-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const base = name ? join(root, name) : root;
  for (const [rel, content] of Object.entries(files)) {
    const target = join(base, rel);
    await mkdir(dirname(target), { recursive: true });
    await writeFile(target, content);
  }
  return base;
}

/** `{ "rel/path": content }` for a directory tree (empty when it's missing). */
async function snapshot(root, prefix = "") {
  const out = {};
  let entries;
  try {
    entries = await readdir(prefix ? join(root, prefix) : root, {
      withFileTypes: true,
    });
  } catch {
    return out;
  }
  for (const entry of entries) {
    const rel = prefix ? `${prefix}/${entry.name}` : entry.name;
    if (entry.isDirectory()) Object.assign(out, await snapshot(root, rel));
    else out[rel] = await readFile(join(root, rel), "utf-8");
  }
  return out;
}

async function listInstalled() {
  try {
    return (await readdir(SKILLS_INSTALL_DIR)).sort();
  } catch {
    return [];
  }
}

// ---------------------------------------------------------------------------
// Minimal ZIP writer for fixtures (stored + deflate + unix mode/flag knobs)
// ---------------------------------------------------------------------------

const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let i = 0; i < 256; i += 1) {
    let c = i;
    for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    table[i] = c >>> 0;
  }
  return table;
})();

function crc32(buffer) {
  let crc = 0xffffffff;
  for (const byte of buffer) {
    crc = CRC_TABLE[(crc ^ byte) & 0xff] ^ (crc >>> 8);
  }
  return (crc ^ 0xffffffff) >>> 0;
}

/**
 * entries: [{ path, data?, method?: "store"|"deflate", mode?, encrypted? }]
 * `mode` sets the unix mode in the central directory (0o120777 == symlink).
 */
function buildZip(entries) {
  const localParts = [];
  const centralParts = [];
  let offset = 0;
  for (const entry of entries) {
    const name = Buffer.from(entry.path, "utf8");
    const content = Buffer.isBuffer(entry.data)
      ? entry.data
      : Buffer.from(entry.data ?? "", "utf8");
    const deflate = entry.method === "deflate";
    const body = deflate ? deflateRawSync(content) : content;
    const method = deflate ? 8 : 0;
    const crc = crc32(content);
    const flags = entry.encrypted ? 0x0801 : 0x0800;
    const externalAttrs =
      entry.mode === undefined ? 0 : (entry.mode << 16) >>> 0;

    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt16LE(20, 4);
    local.writeUInt16LE(flags, 6);
    local.writeUInt16LE(method, 8);
    local.writeUInt32LE(crc, 14);
    local.writeUInt32LE(body.length, 18);
    local.writeUInt32LE(content.length, 22);
    local.writeUInt16LE(name.length, 26);
    localParts.push(local, name, body);

    const central = Buffer.alloc(46);
    central.writeUInt32LE(0x02014b50, 0);
    central.writeUInt16LE(0x031e, 4); // made by unix, spec 3.0
    central.writeUInt16LE(20, 6);
    central.writeUInt16LE(flags, 8);
    central.writeUInt16LE(method, 10);
    central.writeUInt32LE(crc, 16);
    central.writeUInt32LE(body.length, 20);
    central.writeUInt32LE(content.length, 24);
    central.writeUInt16LE(name.length, 28);
    central.writeUInt32LE(externalAttrs, 38);
    central.writeUInt32LE(offset, 42);
    centralParts.push(central, name);

    offset += local.length + name.length + body.length;
  }
  const centralDirectory = Buffer.concat(centralParts);
  const eocd = Buffer.alloc(22);
  eocd.writeUInt32LE(0x06054b50, 0);
  eocd.writeUInt16LE(entries.length, 8);
  eocd.writeUInt16LE(entries.length, 10);
  eocd.writeUInt32LE(centralDirectory.length, 12);
  eocd.writeUInt32LE(offset, 16);
  return Buffer.concat([...localParts, centralDirectory, eocd]);
}

function skillZip(method = "store") {
  return buildZip([
    { path: "my-skill/SKILL.md", data: "# My Skill\n", method },
    { path: "my-skill/scripts/run.py", data: "print('hi')\n", method },
    { path: "my-skill/references/notes.md", data: "notes\n", method },
  ]);
}

async function rejectsWith(promise, code) {
  await assert.rejects(promise, (error) => {
    assert.ok(error instanceof SkillImportError, `unexpected error: ${error}`);
    assert.equal(error.code, code);
    return true;
  });
}

// ---------------------------------------------------------------------------
// Install root
// ---------------------------------------------------------------------------

test("installs into <DATA_DIR>/skills", () => {
  assert.equal(SKILLS_INSTALL_DIR, installRoot);
});

// ---------------------------------------------------------------------------
// Layout detection
// ---------------------------------------------------------------------------

test("directory with SKILL.md at the root installs under the directory name", async (t) => {
  await freshInstallRoot(t);
  const source = await makeTree(
    t,
    {
      "SKILL.md": "# Src\n",
      "scripts/run.py": "print('hi')\n",
    },
    "my-skill"
  );

  const result = await importSkillFromDirectory(source);

  assert.equal(result.name, "my-skill");
  assert.equal(result.files, 2);
  assert.equal(result.bytes, 6 + 12); // "# Src\n" + "print('hi')\n"
  assert.equal(result.overwritten, false);
  assert.deepEqual(await snapshot(join(installRoot, "my-skill")), {
    "SKILL.md": "# Src\n",
    "scripts/run.py": "print('hi')\n",
  });
});

test("a single wrapping directory is stripped and becomes the name", async (t) => {
  await freshInstallRoot(t);
  const source = await makeTree(t, {
    "my-skill/SKILL.md": "# Src\n",
    "my-skill/scripts/run.py": "print('hi')\n",
    "unrelated/other.txt": "ignored\n",
  });

  const result = await importSkillFromDirectory(source);

  assert.equal(result.name, "my-skill");
  assert.equal(result.files, 2);
  assert.deepEqual(await snapshot(installRoot), {
    "my-skill/SKILL.md": "# Src\n",
    "my-skill/scripts/run.py": "print('hi')\n",
  });
});

test("a directory without SKILL.md is rejected", async (t) => {
  await freshInstallRoot(t);
  const source = await makeTree(t, { "readme.md": "nope\n" }, "my-skill");
  await rejectsWith(importSkillFromDirectory(source), "invalid");
  assert.deepEqual(await listInstalled(), []);
});

test("two SKILL.md files are ambiguous and rejected", async (t) => {
  await freshInstallRoot(t);
  const source = await makeTree(t, {
    "SKILL.md": "# One\n",
    "other/SKILL.md": "# Two\n",
  });
  await rejectsWith(
    importSkillFromDirectory(source, { name: "two" }),
    "invalid"
  );
});

test("a nested SKILL.md (deeper than one directory) is rejected", async (t) => {
  await freshInstallRoot(t);
  const source = await makeTree(t, {
    "outer/inner/SKILL.md": "# Deep\n",
  });
  await rejectsWith(
    importSkillFromDirectory(source, { name: "deep" }),
    "invalid"
  );
});

test("a missing source directory is rejected", async (t) => {
  await freshInstallRoot(t);
  await rejectsWith(
    importSkillFromDirectory(join(dataDir, "does-not-exist")),
    "invalid"
  );
});

test("a source file (not a directory) is rejected", async (t) => {
  await freshInstallRoot(t);
  const source = await makeTree(t, { "SKILL.md": "# Src\n" }, "my-skill");
  const file = join(source, "SKILL.md");
  await rejectsWith(importSkillFromDirectory(file, { name: "x" }), "invalid");
});

// ---------------------------------------------------------------------------
// Naming
// ---------------------------------------------------------------------------

test("an explicit name wins over the directory name", async (t) => {
  await freshInstallRoot(t);
  const source = await makeTree(t, { "SKILL.md": "# Src\n" }, "upstream-dir");

  const result = await importSkillFromDirectory(source, { name: "renamed" });

  assert.equal(result.name, "renamed");
  assert.deepEqual(await listInstalled(), ["renamed"]);
});

test("an explicit name must pass isValidSkillName", async (t) => {
  await freshInstallRoot(t);
  const source = await makeTree(t, { "SKILL.md": "# Src\n" }, "my-skill");
  // A blank name means "not supplied", so it falls back to the directory name.
  for (const bad of [
    "../evil",
    "a/b",
    ".hidden",
    "-dash",
    "has space",
    "x".repeat(129),
  ]) {
    await rejectsWith(
      importSkillFromDirectory(source, { name: bad }),
      "invalid"
    );
  }
  assert.deepEqual(await listInstalled(), []);
});

test("uploads: an invalid derived name is rejected", async (t) => {
  await freshInstallRoot(t);
  await rejectsWith(
    importSkillFromUploads(
      [{ path: "my-skill/SKILL.md", data: Buffer.from("# Src\n") }],
      { name: ".hidden" }
    ),
    "invalid"
  );
  assert.equal(isValidSkillName(".hidden"), false);
});

test("uploads: a root-level SKILL.md needs an explicit name", async (t) => {
  await freshInstallRoot(t);
  await rejectsWith(
    importSkillFromUploads([
      { path: "SKILL.md", data: Buffer.from("# Src\n") },
    ]),
    "invalid"
  );

  const result = await importSkillFromUploads(
    [{ path: "SKILL.md", data: Buffer.from("# Src\n") }],
    { name: "flat-skill" }
  );
  assert.equal(result.name, "flat-skill");
  assert.deepEqual(await snapshot(join(installRoot, "flat-skill")), {
    "SKILL.md": "# Src\n",
  });
});

test("uploads: a wrapping folder becomes the name and its prefix is stripped", async (t) => {
  await freshInstallRoot(t);
  const result = await importSkillFromUploads([
    { path: "my-skill/SKILL.md", data: Buffer.from("# Src\n") },
    { path: "my-skill/scripts/run.py", data: Buffer.from("print('hi')\n") },
  ]);

  assert.equal(result.name, "my-skill");
  assert.equal(result.files, 2);
  assert.deepEqual(await snapshot(installRoot), {
    "my-skill/SKILL.md": "# Src\n",
    "my-skill/scripts/run.py": "print('hi')\n",
  });
});

// ---------------------------------------------------------------------------
// Path safety
// ---------------------------------------------------------------------------

test("uploads: unsafe entry paths are rejected", async (t) => {
  await freshInstallRoot(t);
  const skill = { path: "SKILL.md", data: Buffer.from("# Src\n") };
  for (const unsafePath of [
    "../evil.md",
    "nested/../../evil.md",
    "/etc/passwd",
    "C:/windows/evil.md",
    "scripts\\run.py",
    "..\\..\\evil.md",
  ]) {
    await rejectsWith(
      importSkillFromUploads(
        [skill, { path: unsafePath, data: Buffer.from("x") }],
        {
          name: "safe-skill",
        }
      ),
      "unsafe"
    );
  }
  assert.deepEqual(await listInstalled(), []);
});

test("uploads: duplicate paths are rejected", async (t) => {
  await freshInstallRoot(t);
  await rejectsWith(
    importSkillFromUploads(
      [
        { path: "SKILL.md", data: Buffer.from("# Src\n") },
        { path: "./SKILL.md", data: Buffer.from("# Other\n") },
      ],
      { name: "dupe" }
    ),
    "invalid"
  );
});

test("directory: symlinks are rejected", async (t) => {
  await freshInstallRoot(t);
  const source = await makeTree(t, { "SKILL.md": "# Src\n" }, "linked-skill");
  await symlink("/etc/passwd", join(source, "leak.txt"));
  await rejectsWith(importSkillFromDirectory(source), "unsafe");
  assert.deepEqual(await listInstalled(), []);
});

test("zip: a symlink entry is rejected", async (t) => {
  await freshInstallRoot(t);
  const zip = buildZip([
    { path: "my-skill/SKILL.md", data: "# Src\n" },
    { path: "my-skill/leak", data: "/etc/passwd", mode: 0o120777 },
  ]);
  await rejectsWith(importSkillFromZip(zip), "unsafe");
  assert.deepEqual(await listInstalled(), []);
});

test("zip: a traversal entry is rejected", async (t) => {
  await freshInstallRoot(t);
  const zip = buildZip([
    { path: "my-skill/SKILL.md", data: "# Src\n" },
    { path: "my-skill/../../evil.md", data: "x" },
  ]);
  await rejectsWith(importSkillFromZip(zip), "unsafe");
});

// ---------------------------------------------------------------------------
// Limits
// ---------------------------------------------------------------------------

test(`uploads: more than ${MAX_SKILL_FILES} files is too large`, async (t) => {
  await freshInstallRoot(t);
  const files = [{ path: "SKILL.md", data: Buffer.from("# Src\n") }];
  for (let index = 0; index <= MAX_SKILL_FILES; index += 1) {
    files.push({ path: `assets/f${index}.txt`, data: Buffer.from("x") });
  }
  await rejectsWith(
    importSkillFromUploads(files, { name: "huge-count" }),
    "too-large"
  );
  assert.deepEqual(await listInstalled(), []);
});

test("uploads: more than 25 MB is too large", async (t) => {
  await freshInstallRoot(t);
  await rejectsWith(
    importSkillFromUploads(
      [
        { path: "SKILL.md", data: Buffer.from("# Src\n") },
        {
          path: "assets/blob.bin",
          data: new Uint8Array(MAX_SKILL_BYTES + 1),
        },
      ],
      { name: "huge-bytes" }
    ),
    "too-large"
  );
  assert.deepEqual(await listInstalled(), []);
});

test("zip: a file-count blow-up is too large", async (t) => {
  await freshInstallRoot(t);
  const entries = [{ path: "my-skill/SKILL.md", data: "# Src\n" }];
  for (let index = 0; index <= MAX_SKILL_FILES; index += 1) {
    entries.push({ path: `my-skill/assets/f${index}.txt`, data: "x" });
  }
  await rejectsWith(importSkillFromZip(buildZip(entries)), "too-large");
});

// ---------------------------------------------------------------------------
// On-disk behaviour
// ---------------------------------------------------------------------------

test("an existing skill is not clobbered without overwrite", async (t) => {
  await freshInstallRoot(t);
  const first = await makeTree(t, { "SKILL.md": "# First\n" }, "my-skill");
  await importSkillFromDirectory(first);

  const second = await makeTree(t, { "SKILL.md": "# Second\n" }, "my-skill");
  await assert.rejects(importSkillFromDirectory(second), (error) => {
    assert.ok(error instanceof SkillImportError);
    assert.equal(error.code, "exists");
    assert.match(error.message, /技能 my-skill 已存在/);
    return true;
  });

  assert.deepEqual(await snapshot(join(installRoot, "my-skill")), {
    "SKILL.md": "# First\n",
  });
  assert.deepEqual(await listInstalled(), ["my-skill"]);
});

test("overwrite replaces the previous contents", async (t) => {
  await freshInstallRoot(t);
  const first = await makeTree(
    t,
    {
      "SKILL.md": "# First\n",
      "old/leftover.py": "old\n",
    },
    "my-skill"
  );
  await importSkillFromDirectory(first);

  const second = await makeTree(t, { "SKILL.md": "# Second\n" }, "my-skill");
  const result = await importSkillFromDirectory(second, { overwrite: true });

  assert.equal(result.overwritten, true);
  assert.deepEqual(await snapshot(join(installRoot, "my-skill")), {
    "SKILL.md": "# Second\n",
  });
  assert.deepEqual(await listInstalled(), ["my-skill"]);
});

test("a failed import leaves no .importing-* temp directory", async (t) => {
  await freshInstallRoot(t);
  // "a" as a file and "a/b.md" needing a directory can never both be written,
  // so the write fails after the tree is partly on disk.
  await assert.rejects(
    importSkillFromUploads(
      [
        { path: "SKILL.md", data: Buffer.from("# Src\n") },
        { path: "a", data: Buffer.from("file\n") },
        { path: "a/b.md", data: Buffer.from("nested\n") },
      ],
      { name: "residue" }
    )
  );
  assert.deepEqual(await listInstalled(), []);
});

// ---------------------------------------------------------------------------
// ZIP reading
// ---------------------------------------------------------------------------

test("readZipEntries restores stored and deflated content", () => {
  for (const method of ["store", "deflate"]) {
    const entries = readZipEntries(skillZip(method));
    assert.deepEqual(
      entries.map((entry) => entry.path),
      [
        "my-skill/SKILL.md",
        "my-skill/scripts/run.py",
        "my-skill/references/notes.md",
      ]
    );
    const decoded = Object.fromEntries(
      entries.map((entry) => [
        entry.path,
        Buffer.from(entry.data).toString("utf-8"),
      ])
    );
    assert.deepEqual(decoded, {
      "my-skill/SKILL.md": "# My Skill\n",
      "my-skill/scripts/run.py": "print('hi')\n",
      "my-skill/references/notes.md": "notes\n",
    });
  }
});

test("readZipEntries skips directory entries", () => {
  const zip = buildZip([
    { path: "my-skill/" },
    { path: "my-skill/SKILL.md", data: "# Src\n" },
  ]);
  assert.deepEqual(
    readZipEntries(zip).map((entry) => entry.path),
    ["my-skill/SKILL.md"]
  );
});

test("readZipEntries rejects garbage and truncated archives", () => {
  const garbage = Buffer.from("this is definitely not a zip archive");
  assert.throws(
    () => readZipEntries(garbage),
    (error) => error instanceof SkillImportError && error.code === "invalid"
  );

  const good = skillZip();
  assert.throws(
    () => readZipEntries(good.subarray(0, Math.floor(good.length / 2))),
    (error) => error instanceof SkillImportError && error.code === "invalid"
  );
  assert.throws(
    () => readZipEntries(new Uint8Array(0)),
    (error) => error instanceof SkillImportError && error.code === "invalid"
  );
});

test("readZipEntries rejects encrypted archives", () => {
  const zip = buildZip([
    { path: "my-skill/SKILL.md", data: "# Src\n", encrypted: true },
  ]);
  assert.throws(
    () => readZipEntries(zip),
    (error) => error instanceof SkillImportError && error.code === "invalid"
  );
});

test("zip imports write the tree for both compression methods", async (t) => {
  for (const method of ["store", "deflate"]) {
    await freshInstallRoot(t);
    const result = await importSkillFromZip(skillZip(method));
    assert.equal(result.name, "my-skill");
    assert.equal(result.files, 3);
    assert.deepEqual(await snapshot(join(installRoot, "my-skill")), {
      "SKILL.md": "# My Skill\n",
      "references/notes.md": "notes\n",
      "scripts/run.py": "print('hi')\n",
    });
  }
});

test("zip with a flat SKILL.md needs an explicit name", async (t) => {
  await freshInstallRoot(t);
  const zip = buildZip([{ path: "SKILL.md", data: "# Flat\n" }]);
  await rejectsWith(importSkillFromZip(zip), "invalid");

  const result = await importSkillFromZip(zip, { name: "flat-zip" });
  assert.equal(result.name, "flat-zip");
  assert.deepEqual(await snapshot(join(installRoot, "flat-zip")), {
    "SKILL.md": "# Flat\n",
  });
});

test("zip: __MACOSX and .DS_Store entries are ignored", async (t) => {
  await freshInstallRoot(t);
  const zip = buildZip([
    { path: "my-skill/SKILL.md", data: "# Src\n" },
    { path: "my-skill/.DS_Store", data: "junk" },
    { path: "__MACOSX/my-skill/._SKILL.md", data: "junk" },
  ]);
  const result = await importSkillFromZip(zip);
  assert.equal(result.files, 1);
  assert.deepEqual(await snapshot(join(installRoot, "my-skill")), {
    "SKILL.md": "# Src\n",
  });
});
