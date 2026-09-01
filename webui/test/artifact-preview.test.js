import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const dialogSource = await readFile(
  new URL("../src/app/components/WorkspaceFileDialog.tsx", import.meta.url),
  "utf8"
);
const markdownSource = await readFile(
  new URL("../src/app/components/MarkdownContent.tsx", import.meta.url),
  "utf8"
);
const scrollAreaSource = await readFile(
  new URL("../src/components/ui/scroll-area.tsx", import.meta.url),
  "utf8"
);

test("artifact preview exposes a wide horizontal text viewport", () => {
  assert.match(dialogSource, /w-\[calc\(100vw-1rem\)\] max-w-\[calc\(100vw-1rem\)\]/);
  assert.match(dialogSource, /sm:w-\[98vw\] sm:max-w-\[98vw\]/);
  assert.match(dialogSource, /<ScrollArea\s+horizontal/);
  assert.match(dialogSource, /allowHorizontalOverflow/);
  assert.match(dialogSource, /width: "max-content"/);
  assert.match(markdownSource, /allowHorizontalOverflow && "min-w-max"/);
  assert.match(scrollAreaSource, /<ScrollBar orientation="horizontal"/);
});

test("image preview offers button and click-to-toggle zoom", () => {
  assert.match(dialogSource, /aria-pressed=\{imageZoomed\}/);
  assert.match(dialogSource, /cursor-zoom-in/);
  assert.match(dialogSource, /cursor-zoom-out/);
  assert.match(dialogSource, /onClick=\{\(\) => setImageZoomed/);
});
