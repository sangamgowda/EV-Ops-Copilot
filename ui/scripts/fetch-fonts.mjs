// Fetch the UI's font (Satoshi, by Indian Type Foundry) from Fontshare
// into public/fonts, where Vite serves it and the build copies it.
//
// Why fetched rather than committed: Satoshi is free for commercial use
// and self-hosting under the ITF Free Font License, but the licence
// forbids redistributing the font files — explicitly including through
// a repository or other publicly accessible server. This repository is
// public, so the files are never committed (public/fonts is ignored);
// whoever builds the UI gets their own copy directly from Fontshare, as
// the licence asks. Licence: https://www.fontshare.com/licenses/itf-ffl
//
// Idempotent: files already present are kept. If the download fails
// (offline, service down) it warns and exits 0 — the UI falls back to
// the system font rather than refusing to build.

import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const OUT = join(dirname(fileURLToPath(import.meta.url)), "..", "public", "fonts");
const WEIGHTS = [400, 500, 700];
// All weights in one family parameter; repeating f[] per weight returns
// only one of them.
const CSS_URL = `https://api.fontshare.com/v2/css?f[]=satoshi@${WEIGHTS.join(",")}&display=swap`;
const file = (w) => join(OUT, `Satoshi-${w}.woff2`);

async function main() {
  if (WEIGHTS.every((w) => existsSync(file(w)))) {
    console.log("fonts: Satoshi already present");
    return;
  }
  mkdirSync(OUT, { recursive: true });
  const css = await (await fetch(CSS_URL)).text();
  // One @font-face block per weight; take its woff2 source.
  const faces = [...css.matchAll(/@font-face\s*{([^}]*)}/g)].map((m) => m[1]);
  for (const w of WEIGHTS) {
    const face = faces.find((f) => new RegExp(`font-weight:\\s*${w}\\b`).test(f));
    const url = face?.match(/url\('([^']+\.woff2)'\)/)?.[1];
    if (!url) throw new Error(`no woff2 source for weight ${w}`);
    const res = await fetch(url.startsWith("//") ? `https:${url}` : url);
    if (!res.ok) throw new Error(`${res.status} fetching weight ${w}`);
    writeFileSync(file(w), Buffer.from(await res.arrayBuffer()));
    console.log(`fonts: fetched Satoshi ${w}`);
  }
}

main().catch((err) => {
  console.warn(`fonts: could not fetch Satoshi (${err.message}); the UI will use the system font`);
});
