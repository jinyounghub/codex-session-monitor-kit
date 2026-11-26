import { copyFileSync, mkdirSync, realpathSync, writeFileSync } from "node:fs";
import { basename, dirname, isAbsolute, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

if (process.argv.length !== 3) {
  process.stderr.write("Usage: node scripts/prepare-site.mjs <new-directory-outside-this-kit>\n");
  process.exit(2);
}

const kit = realpathSync(fileURLToPath(new URL("../", import.meta.url)));
const requested = resolve(process.argv[2]);
const target = join(realpathSync(dirname(requested)), basename(requested));
const fromKit = relative(kit, target);
if (!isAbsolute(fromKit) && fromKit !== ".." && !fromKit.startsWith(`..${sep}`)) {
  process.stderr.write("Choose a new directory outside the public kit checkout.\n");
  process.exit(2);
}

const files = [
  "dashboard/app.js",
  "dashboard/index.html",
  "dashboard/style.css",
  "drizzle/0000_device_snapshots.sql",
  "drizzle/meta/_journal.json",
  "scripts/build-site.mjs",
  "site/worker.js",
];
mkdirSync(target, { mode: 0o700 });
for (const file of files) {
  const destination = join(target, file);
  mkdirSync(dirname(destination), { recursive: true });
  copyFileSync(join(kit, file), destination);
}

mkdirSync(join(target, ".openai"));
writeFileSync(join(target, ".openai", "hosting.json"), JSON.stringify({ d1: "DB", r2: null }, null, 2) + "\n");
writeFileSync(join(target, ".gitignore"), "dist/\nnode_modules/\n.env*\n*.key\n*.sqlite*\n*.log\n");
writeFileSync(join(target, "package.json"), JSON.stringify({
  name: "codex-session-monitor-site",
  private: true,
  type: "module",
  scripts: { build: "node scripts/build-site.mjs" },
}, null, 2) + "\n");
process.stdout.write(target + "\n");
