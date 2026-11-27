import { readFile } from "node:fs/promises";
import { basename, dirname, join } from "node:path";
import { NextResponse } from "next/server";
import { productImageKeys, type CycleArchive, type ProductImage, type ProductImageKey, type SolarFrame, type SolarProducts, type TodaySunSnapshot } from "@/lib/todaySun";

export const dynamic = "force-dynamic";

type StoredProducts = Omit<SolarProducts, ProductImageKey> & Partial<Record<ProductImageKey, ProductImage & { jpeg: string }>>;
type StoredSnapshot = Omit<TodaySunSnapshot, "images" | "products"> & {
  images: (SolarFrame & { jpeg: string })[];
  products?: StoredProducts;
};

const headers = { "Cache-Control": "no-store" };

function cachePath(file: "snapshot.json" | "cycle.json") {
  const cwd = process.cwd();
  const webui = basename(cwd) === "dist" ? dirname(cwd) : cwd;
  const checkout = basename(webui) === "webui" ? dirname(webui) : cwd;
  const workspace = process.env.JW_WORKSPACE_DIR || join(checkout, "workspace");
  return join(workspace, "today-sun", file);
}

export async function GET(request: Request) {
  const query = new URL(request.url).searchParams;
  if (query.get("archive") === "cycle") {
    try {
      const archive: CycleArchive = JSON.parse(await readFile(cachePath("cycle.json"), "utf8"));
      if (archive.version !== 1 || !Array.isArray(archive.cycles)) throw new Error("Invalid cycle archive");
      return NextResponse.json(archive, { headers });
    } catch (error) {
      return (error as NodeJS.ErrnoException).code === "ENOENT"
        ? NextResponse.json(null, { headers })
        : NextResponse.json({ error: "Cycle archive unavailable" }, { status: 503, headers });
    }
  }
  const channel = query.get("image");
  let snapshot: StoredSnapshot;
  try {
    snapshot = JSON.parse(await readFile(cachePath("snapshot.json"), "utf8"));
    if (![1, 2].includes(snapshot.version) || !Array.isArray(snapshot.images)) {
      throw new Error("Invalid Today Sun snapshot");
    }
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") {
      return NextResponse.json({ error: "Snapshot unavailable" }, { status: 503, headers });
    }
    return channel === null
      ? NextResponse.json(null, { headers })
      : new Response(null, { status: 404, headers });
  }

  if (channel !== null) {
    const frame = productImageKeys.includes(channel as ProductImageKey)
      ? snapshot.products?.[channel as ProductImageKey]
      : snapshot.images.find((image) => image.channel === channel);
    const at = query.get("at");
    const imageTime = frame?.observedAt ?? (frame && "cachedAt" in frame ? frame.cachedAt : null);
    if (!frame || (at !== null && at !== imageTime)) {
      return new Response(null, { status: 404, headers });
    }
    return new Response(new Uint8Array(Buffer.from(frame.jpeg, "base64")), {
      headers: { ...headers, "Content-Type": "image/jpeg", "X-Content-Type-Options": "nosniff" },
    });
  }

  const products: SolarProducts = { ...snapshot.products };
  for (const key of productImageKeys) {
    const image = snapshot.products?.[key];
    if (image) {
      const { url, observedAt, modifiedAt, cachedAt } = image;
      products[key] = { url, observedAt, modifiedAt, cachedAt };
    }
  }
  return NextResponse.json({
    ...snapshot,
    products,
    images: snapshot.images.map(({ channel, observedAt, url }) => ({ channel, observedAt, url })),
  }, { headers });
}
