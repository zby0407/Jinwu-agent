import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { promises as fs } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import test from "node:test";
import ts from "typescript";
import { readRegisteredProjectInput } from "../src/lib/server/project-inputs.js";

const require = createRequire(import.meta.url);
const virtualPath = "/project/data/solar/cycles.txt";

async function fixture(t) {
  const baseWorkspace = await fs.mkdtemp(join(tmpdir(), "jw-project-preview-"));
  t.after(() => fs.rm(baseWorkspace, { recursive: true, force: true }));
  const projectRoot = join(baseWorkspace, "projects", "default");
  const workspace = join(projectRoot, "runs", "run-one");
  const shared = join(projectRoot, "shared");
  await fs.mkdir(workspace, { recursive: true });
  await fs.mkdir(join(shared, "data", "solar"), { recursive: true });
  const content = Buffer.from("registered solar data\n");
  const target = join(shared, "data", "solar", "cycles.txt");
  await fs.writeFile(target, content);
  const entry = {
    source: "registered_project_data",
    path: virtualPath,
    bytes: content.length,
    sha256: createHash("sha256").update(content).digest("hex"),
  };
  const manifest = {
    schema_version: 2,
    thread_id: "thread-one",
    project_inputs: [entry],
  };
  const writeManifest = () =>
    fs.writeFile(
      join(workspace, "input_manifest.json"),
      JSON.stringify(manifest)
    );
  await writeManifest();
  const options = {
    threadId: "thread-one",
    baseWorkspace,
    path: virtualPath,
    binding: {
      thread_id: "thread-one",
      base_workspace: baseWorkspace,
      project_root: projectRoot,
      project_shared: shared,
      workspace,
      legacy: false,
    },
  };
  return {
    options,
    manifest,
    entry,
    content,
    target,
    workspace,
    shared,
    writeManifest,
  };
}

test("reads the exact bytes of a current-thread registered project input", async (t) => {
  const f = await fixture(t);
  const result = await readRegisteredProjectInput(f.options);
  assert.deepEqual(result.content, f.content);
  assert.equal(result.bytes, f.entry.bytes);
  assert.equal(result.fileName, "cycles.txt");
});

test("also honors built-in project data recorded with the same manifest contract", async (t) => {
  const f = await fixture(t);
  f.entry.source = "project_shared_data";
  await f.writeManifest();
  assert.deepEqual((await readRegisteredProjectInput(f.options)).content, f.content);
});

test("never guesses shortened paths or accepts traversal, hidden entries, and separator aliases", async (t) => {
  const f = await fixture(t);
  for (const path of [
    "solar/cycles.txt",
    "project/data/solar/cycles.txt",
    "/project/../cycles.txt",
    "/project/data//solar/cycles.txt",
    "/project/data/./cycles.txt",
    "/project/data\\solar\\cycles.txt",
    "/project/.secret/cycles.txt",
    "/project/data/cycles.txt:stream",
    "/project/data/cycles\u0000.txt",
  ]) {
    await assert.rejects(readRegisteredProjectInput({ ...f.options, path }));
  }
});

test("requires the current thread and an isolated binding", async (t) => {
  const f = await fixture(t);
  await assert.rejects(readRegisteredProjectInput({ ...f.options, threadId: null }), /绑定/);
  await assert.rejects(
    readRegisteredProjectInput({ ...f.options, threadId: "thread-other" }),
    /绑定/
  );
  await assert.rejects(
    readRegisteredProjectInput({
      ...f.options,
      binding: { ...f.options.binding, legacy: true },
    }),
    /绑定/
  );
  f.manifest.thread_id = "thread-other";
  await f.writeManifest();
  await assert.rejects(
    readRegisteredProjectInput(f.options),
    /清单与当前会话不匹配/
  );
});

test("rejects a symlink or junction used as the task input manifest", async (t) => {
  const f = await fixture(t);
  const manifestPath = join(f.workspace, "input_manifest.json");
  await fs.unlink(manifestPath);
  try {
    await fs.symlink(f.shared, manifestPath, process.platform === "win32" ? "junction" : "dir");
  } catch (error) {
    if (error.code === "EPERM" || error.code === "EACCES") return t.skip("host cannot create symlinks");
    throw error;
  }
  await assert.rejects(readRegisteredProjectInput(f.options), /清单无效/);
});

test("does not use another thread's manifest or an unregistered same-name input", async (t) => {
  const f = await fixture(t);
  const otherWorkspace = join(
    f.options.baseWorkspace,
    "projects",
    "default",
    "runs",
    "run-two"
  );
  await fs.mkdir(otherWorkspace);
  await fs.copyFile(
    join(f.workspace, "input_manifest.json"),
    join(otherWorkspace, "input_manifest.json")
  );
  f.manifest.project_inputs = [];
  await f.writeManifest();
  await assert.rejects(readRegisteredProjectInput(f.options), /未登记/);
  f.manifest.project_inputs = [{ ...f.entry, source: "arbitrary_file" }];
  await f.writeManifest();
  await assert.rejects(readRegisteredProjectInput(f.options), /未登记/);
});

test("rejects changed hashes, sizes, missing digests, ambiguous entries, and excessive allocation", async (t) => {
  const f = await fixture(t);
  await fs.writeFile(f.target, Buffer.alloc(f.content.length, "x"));
  await assert.rejects(readRegisteredProjectInput(f.options), /SHA256/);
  await fs.writeFile(f.target, f.content);
  f.entry.bytes += 1;
  await f.writeManifest();
  await assert.rejects(readRegisteredProjectInput(f.options), /大小/);
  f.entry.bytes = f.content.length;
  const digest = f.entry.sha256;
  delete f.entry.sha256;
  await f.writeManifest();
  await assert.rejects(readRegisteredProjectInput(f.options), /未登记/);
  f.entry.sha256 = digest;
  f.manifest.project_inputs.push({ ...f.entry });
  await f.writeManifest();
  await assert.rejects(readRegisteredProjectInput(f.options), /未登记/);
  f.manifest.project_inputs.pop();
  f.entry.bytes = 64 * 1024 * 1024 + 1;
  await f.writeManifest();
  await assert.rejects(readRegisteredProjectInput(f.options), /64 MiB/);
});

test("rejects a project root or shared root borrowed from another project", async (t) => {
  const f = await fixture(t);
  const foreign = join(
    f.options.baseWorkspace,
    "projects",
    "foreign",
    "shared"
  );
  await fs.mkdir(foreign, { recursive: true });
  await assert.rejects(
    readRegisteredProjectInput({
      ...f.options,
      binding: { ...f.options.binding, project_shared: foreign },
    }),
    /绑定不属于/
  );
  await assert.rejects(
    readRegisteredProjectInput({
      ...f.options,
      binding: { ...f.options.binding, project_root: resolve(foreign, "..") },
    }),
    /绑定不属于/
  );
});

test("rejects a symlink or Windows junction in the registered input path", async (t) => {
  const f = await fixture(t);
  const linked = join(f.shared, "linked");
  try {
    await fs.symlink(
      join(f.shared, "data", "solar"),
      linked,
      process.platform === "win32" ? "junction" : "dir"
    );
  } catch (error) {
    if (error.code === "EPERM" || error.code === "EACCES")
      return t.skip("host cannot create symlinks");
    throw error;
  }
  f.entry.path = "/project/linked/cycles.txt";
  await f.writeManifest();
  await assert.rejects(
    readRegisteredProjectInput({ ...f.options, path: f.entry.path }),
    /符号链接/
  );
});

async function loadTsModule(relative, overrides = {}) {
  const source = await fs.readFile(new URL(relative, import.meta.url), "utf8");
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
    },
  });
  const module = { exports: {} };
  new Function("require", "module", "exports", outputText)(
    (id) => overrides[id] ?? require(id),
    module,
    module.exports
  );
  return module.exports;
}

test("file-link detection preserves project scope and ordinary task-relative paths", async () => {
  const { detectFileLink } = await loadTsModule("../src/lib/fileLink.ts");
  assert.deepEqual(detectFileLink(virtualPath), {
    kind: "workspace",
    display: virtualPath,
    path: virtualPath,
  });
  assert.equal(detectFileLink("/outputs/report.md").path, "outputs/report.md");
  assert.equal(detectFileLink("solar/cycles.txt").path, "solar/cycles.txt");
});

test("GET previews/downloads verified bytes and PUT/DELETE cannot reach a project input", async (t) => {
  const f = await fixture(t);
  let writes = 0;
  let projectReads = 0;
  const route = await loadTsModule("../src/app/api/workspace/file/route.ts", {
    "@/lib/server/workspace": {
      isCrossOrigin: () => false,
      MAX_WORKSPACE_WRITE_BYTES: 5 * 1024 * 1024,
      readProjectInput: async (threadId, path) => {
        projectReads++;
        return readRegisteredProjectInput({ ...f.options, threadId, path });
      },
      getWorkspaceDir: async () => f.workspace,
      safeResolve: async (root, path) => fs.realpath(resolve(root, path)),
      writeWorkspaceFile: async () => {
        writes++;
      },
      deleteWorkspaceFile: async () => {
        writes++;
      },
    },
  });
  const { NextRequest } = require("next/server");
  const url = new URL("http://localhost/api/workspace/file");
  url.search = new URLSearchParams({
    threadId: "thread-one",
    path: virtualPath,
  });
  for (const download of ["0", "1"]) {
    url.searchParams.set("download", download);
    const response = await route.GET(new NextRequest(url));
    assert.equal(response.status, 200);
    assert.deepEqual(Buffer.from(await response.arrayBuffer()), f.content);
    assert.equal(response.headers.get("content-security-policy"), "sandbox");
    assert.match(
      response.headers.get("content-disposition"),
      download === "1" ? /^attachment;/ : /^inline;/
    );
  }
  for (const method of ["PUT", "DELETE"]) {
    const response = await route[method](
      new NextRequest(url, {
        method,
        ...(method === "PUT"
          ? {
              headers: { "content-type": "application/json" },
              body: JSON.stringify({ content: "changed" }),
            }
          : {}),
      })
    );
    assert.equal(response.status, 403);
  }
  assert.equal(writes, 0);
  assert.equal(projectReads, 2);
  assert.deepEqual(await fs.readFile(f.target), f.content);
  url.searchParams.delete("threadId");
  assert.equal((await route.GET(new NextRequest(url))).status, 400);
  url.searchParams.set("threadId", "thread-one");
  url.searchParams.set("path", "solar/cycles.txt");
  assert.equal((await route.GET(new NextRequest(url))).status, 400);
  assert.equal(projectReads, 3, "short paths must not search the project");
});
