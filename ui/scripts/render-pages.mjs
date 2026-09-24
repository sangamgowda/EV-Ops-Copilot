// Render web pages in a headless browser and save their visible text.
//
//   node scripts/render-pages.mjs ../data/sources.yaml ../data/raw/pages
//
// For sites that build their pages with JavaScript: a plain HTTP fetch
// returns an empty shell, so the collector in scripts/collect_web_data.py
// sees nothing. This loads each page the way a visitor's browser does,
// one at a time with a pause between, and writes <slug>.txt containing
// the URL, the page title and the text. Nothing else is fetched, and no
// API behind the page is called directly.
//
// Reads only the `url:` lines of the sources file, so it needs no YAML
// parser. Output belongs under data/raw/, which is git-ignored: page
// text is someone else's content and is never committed.
import { chromium } from "@playwright/test";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const [sourcesFile, outDir] = process.argv.slice(2);
if (!sourcesFile || !outDir) {
  console.error("usage: node scripts/render-pages.mjs <sources.yaml> <out-dir>");
  process.exit(2);
}
const urls = [...readFileSync(sourcesFile, "utf8").matchAll(/url:\s*(\S+)/g)].map((m) => m[1]);
mkdirSync(outDir, { recursive: true });

const browser = await chromium.launch();
const page = await browser.newPage();
for (const url of urls) {
  const slug = new URL(url).pathname.replace(/^\/+|\/+$/g, "").replace(/[^\w-]+/g, "_") || "index";
  try {
    await page.goto(url, { waitUntil: "networkidle", timeout: 60000 });
    await page.waitForTimeout(1500);
    const title = await page.title();
    const text = await page.evaluate(() => (document.querySelector("article, main") || document.body).innerText);
    writeFileSync(join(outDir, `${slug}.txt`), `${url}\n${title}\n\n${text}`);
    console.log(`${String(text.length).padStart(6)} chars  ${slug}`);
  } catch (err) {
    console.log(`failed        ${slug}: ${err.message.split("\n")[0]}`);
  }
  await page.waitForTimeout(2000);
}
await browser.close();
