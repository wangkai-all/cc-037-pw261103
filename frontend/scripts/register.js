// JSX loader for smoke tests (Node >= 20.6): register an esbuild-based
// transform hook so .jsx test/source files run without a bundler.
import { register } from "node:module";

register(new URL("./jsx-loader.js", import.meta.url).href);
