import { createHash } from "node:crypto";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { relative, resolve } from "node:path";
import { gzipSync } from "node:zlib";

const BUDGETS = Object.freeze({
  totalJavaScriptRaw: 868_352,
  totalJavaScriptGzip: 245_760,
  largestJavaScriptRaw: 868_352,
  largestJavaScriptGzip: 245_760,
  totalCssRaw: 57_344,
  totalCssGzip: 12_288,
});

const webRoot = process.cwd();
const assetsRoot = resolve(webRoot, "dist/assets");

if (!existsSync(assetsRoot)) {
  console.error("Bundle budget check failed: dist/assets does not exist.");
  console.error("Run `npm run build` before checking bundle budgets.");
  process.exit(1);
}

function emittedFiles(directory) {
  return readdirSync(directory, { withFileTypes: true })
    .sort((left, right) => left.name.localeCompare(right.name))
    .flatMap((entry) => {
      const path = resolve(directory, entry.name);
      return entry.isDirectory() ? emittedFiles(path) : [path];
    });
}

function bytes(value) {
  return `${value.toLocaleString("en-US")} B`;
}

function percentage(value, budget) {
  return `${((value / budget) * 100).toFixed(2)}%`;
}

const assets = emittedFiles(assetsRoot)
  .filter((path) => path.endsWith(".js") || path.endsWith(".css"))
  .map((path) => {
    const content = readFileSync(path);
    return {
      path: relative(webRoot, path),
      kind: path.endsWith(".js") ? "JavaScript" : "CSS",
      raw: content.byteLength,
      gzip: gzipSync(content, { level: 9 }).byteLength,
      sha256: createHash("sha256").update(content).digest("hex"),
    };
  });

const javascript = assets.filter((asset) => asset.kind === "JavaScript");
const css = assets.filter((asset) => asset.kind === "CSS");

if (javascript.length === 0 || css.length === 0) {
  console.error(
    "Bundle budget check failed: expected at least one emitted JavaScript and CSS asset.",
  );
  process.exit(1);
}

console.log("Emitted bundle assets (gzip level 9)");
console.table(
  assets.map((asset) => ({
    asset: asset.path,
    type: asset.kind,
    raw: bytes(asset.raw),
    gzip: bytes(asset.gzip),
    sha256: asset.sha256,
  })),
);

const sum = (items, field) =>
  items.reduce((total, item) => total + item[field], 0);
const largest = (items, field) =>
  items.reduce((maximum, item) => Math.max(maximum, item[field]), 0);

const measurements = [
  ["Total JavaScript raw", sum(javascript, "raw"), BUDGETS.totalJavaScriptRaw],
  [
    "Total JavaScript gzip",
    sum(javascript, "gzip"),
    BUDGETS.totalJavaScriptGzip,
  ],
  [
    "Largest JavaScript raw",
    largest(javascript, "raw"),
    BUDGETS.largestJavaScriptRaw,
  ],
  [
    "Largest JavaScript gzip",
    largest(javascript, "gzip"),
    BUDGETS.largestJavaScriptGzip,
  ],
  ["Total CSS raw", sum(css, "raw"), BUDGETS.totalCssRaw],
  ["Total CSS gzip", sum(css, "gzip"), BUDGETS.totalCssGzip],
];

console.log("Bundle budget summary");
console.table(
  measurements.map(([metric, actual, budget]) => ({
    metric,
    actual: bytes(actual),
    budget: bytes(budget),
    remaining: bytes(budget - actual),
    used: percentage(actual, budget),
    status: actual <= budget ? "PASS" : "FAIL",
  })),
);

const failures = measurements.filter(([, actual, budget]) => actual > budget);
if (failures.length > 0) {
  for (const [metric, actual, budget] of failures) {
    console.error(
      `${metric} exceeded: actual=${actual} bytes, budget=${budget} bytes, ` +
        `overage=${actual - budget} bytes.`,
    );
  }
  process.exitCode = 1;
} else {
  console.log("Bundle budget check passed.");
}
