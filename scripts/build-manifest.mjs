// Genera public/app/files.json con la lista de archivos que stlite monta en el
// navegador. Vercel lo corre en cada deploy (buildCommand en vercel.json), así que
// agregar o quitar un módulo de Python no requiere tocar index.html.
import { readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { join, relative, sep } from "node:path";

const root = join(process.cwd(), "public", "app");
const SKIP_DIRS = new Set(["__pycache__", ".streamlit", ".pytest_cache"]);
const KEEP = /\.(py|json|csv|txt|toml)$/i;

function walk(dir, out) {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) {
      if (!SKIP_DIRS.has(name)) walk(full, out);
    } else if (KEEP.test(name) && name !== "files.json" && name !== "requirements.txt") {
      out.push(relative(root, full).split(sep).join("/"));
    }
  }
  return out;
}

const files = walk(root, []).sort();
// Hash del contenido: cambia en cada deploy con cambios y evita cachés viejas.
const hash = createHash("sha1");
for (const file of files) hash.update(file).update(readFileSync(join(root, file)));
const version = hash.digest("hex").slice(0, 12);
writeFileSync(join(root, "files.json"), JSON.stringify({ version, files }, null, 2) + "\n");
console.log(`files.json: ${files.length} archivos`);
