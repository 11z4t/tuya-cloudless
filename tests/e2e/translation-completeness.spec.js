// @ts-check
/**
 * Translation completeness tests.
 *
 * For every language file other than en.json, verify that:
 *   1. All dot-path keys present in en.json also exist in the language file.
 *   2. No translated value is an empty string.
 *
 * No browser is launched — tests read translation JSON files from disk only.
 *
 * Translations directory:
 *   custom_components/tuya_cloudless/translations/
 *
 * Supported languages (auto-discovered at test-collection time):
 *   cs, da, de, es, fi, fr, it, ja, ko, nl, no, pl, pt, ru, sv, tr,
 *   zh-Hans, zh-Hant  (plus any added later)
 */

const { test, expect } = require("@playwright/test");
const fs = require("fs");
const path = require("path");

const TRANSLATIONS_DIR = path.resolve(
  __dirname,
  "../../custom_components/tuya_cloudless/translations",
);

// ── Helpers ───────────────────────────────────────────────────────────────────

/**
 * Returns every dot-separated path that leads to a scalar leaf value inside
 * a JSON object.  Arrays are treated as scalars because HA translation files
 * never use arrays at the leaf level.
 *
 * @param {Record<string, unknown>} obj
 * @param {string} prefix
 * @returns {string[]}
 */
function leafPaths(obj, prefix = "") {
  /** @type {string[]} */
  const paths = [];
  for (const [key, val] of Object.entries(obj)) {
    const p = prefix ? `${prefix}.${key}` : key;
    if (val !== null && typeof val === "object" && !Array.isArray(val)) {
      paths.push(...leafPaths(/** @type {Record<string, unknown>} */ (val), p));
    } else {
      paths.push(p);
    }
  }
  return paths;
}

/**
 * Resolves a dot-separated key path against a JSON object.
 * Returns `undefined` if any segment of the path is absent.
 *
 * @param {Record<string, unknown>} obj
 * @param {string} dotPath
 * @returns {unknown}
 */
function valueAt(obj, dotPath) {
  return dotPath.split(".").reduce(
    /**
     * @param {unknown} cur
     * @param {string} k
     */
    (cur, k) =>
      cur !== null && typeof cur === "object" && !Array.isArray(cur)
        ? /** @type {Record<string, unknown>} */ (cur)[k]
        : undefined,
    /** @type {unknown} */ (obj),
  );
}

/**
 * @param {string} filePath
 * @returns {Record<string, unknown>}
 */
function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, "utf-8"));
}

// ── Load reference (en.json) at test-collection time ─────────────────────────

const EN = readJson(path.join(TRANSLATIONS_DIR, "en.json"));
const EN_PATHS = leafPaths(EN);

// Discover every non-English translation file in the directory.
const LANGUAGES = fs
  .readdirSync(TRANSLATIONS_DIR)
  .filter((f) => f.endsWith(".json") && f !== "en.json")
  .map((f) => path.basename(f, ".json"))
  .sort();

// ── Tests ─────────────────────────────────────────────────────────────────────

test.describe("Translation completeness", () => {
  // Sanity-check: the reference file itself is non-empty.
  test("en.json has translation keys", () => {
    expect(EN_PATHS.length).toBeGreaterThan(0);
  });

  // At least one non-English language must exist.
  test("at least one non-English language file exists", () => {
    expect(LANGUAGES.length).toBeGreaterThan(0);
  });

  for (const lang of LANGUAGES) {
    test.describe(lang, () => {
      test("all en.json keys are present and non-empty", () => {
        const langData = readJson(
          path.join(TRANSLATIONS_DIR, `${lang}.json`),
        );

        /** @type {string[]} */
        const missing = [];
        /** @type {string[]} */
        const empty = [];

        for (const p of EN_PATHS) {
          const val = valueAt(langData, p);
          if (val === undefined) {
            missing.push(p);
          } else if (val === "") {
            empty.push(p);
          }
        }

        expect(
          missing,
          `[${lang}] Keys present in en.json but missing from ${lang}.json:\n  ${missing.join("\n  ")}`,
        ).toHaveLength(0);

        expect(
          empty,
          `[${lang}] Keys with empty-string values in ${lang}.json:\n  ${empty.join("\n  ")}`,
        ).toHaveLength(0);
      });

      test("no stale keys absent from en.json", () => {
        // Stale keys are present in the language file but removed from en.json.
        const langData = readJson(
          path.join(TRANSLATIONS_DIR, `${lang}.json`),
        );
        const langPaths = leafPaths(langData);

        const stale = langPaths.filter((p) => valueAt(EN, p) === undefined);

        expect(
          stale,
          `[${lang}] Stale keys in ${lang}.json not present in en.json:\n  ${stale.join("\n  ")}`,
        ).toHaveLength(0);
      });
    });
  }
});
