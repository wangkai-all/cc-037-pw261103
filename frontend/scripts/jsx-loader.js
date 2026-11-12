import { transform } from "esbuild";
import { existsSync } from "node:fs";
import { readFileSync } from "node:fs";

export async function resolve(specifier, context, nextResolve) {
  if (specifier.startsWith(".") && !/\.[a-zA-Z0-9]+$/.test(specifier)) {
    for (const ext of [".jsx", ".js"]) {
      const candidate = new URL(specifier + ext, context.parentURL);
      if (existsSync(candidate)) {
        return { url: candidate.href, shortCircuit: true };
      }
    }
  }
  return nextResolve(specifier, context);
}

export async function load(url, context, nextLoad) {
  if (url.endsWith(".jsx")) {
    const source = readFileSync(new URL(url), "utf8");
    const { code } = await transform(source, {
      loader: "jsx",
      jsx: "automatic",
      format: "esm",
      sourcefile: url,
    });
    return { format: "module", shortCircuit: true, source: code };
  }
  return nextLoad(url, context);
}
