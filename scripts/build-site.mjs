import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const root = new URL("../", import.meta.url);
function read(relative) {
  return readFileSync(new URL(relative, root), "utf8");
}
const assets = {
  "/": ["text/html; charset=utf-8", read("dashboard/index.html").replace("<body>", '<body data-hosted="true">')],
  "/style.css": ["text/css; charset=utf-8", read("dashboard/style.css")],
  "/app.js": ["text/javascript; charset=utf-8", read("dashboard/app.js")],
};
for (const directory of ["dist/server", "dist/.openai"]) {
  mkdirSync(fileURLToPath(new URL(directory, root)), { recursive: true });
}
writeFileSync(new URL("dist/server/index.js", root), `const ASSETS = ${JSON.stringify(assets)};\n${read("site/worker.js")}`);
writeFileSync(new URL("dist/.openai/hosting.json", root), read(".openai/hosting.json"));
