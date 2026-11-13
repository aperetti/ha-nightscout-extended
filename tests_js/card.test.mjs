// Unit tests for the glucose card's pure logic. Run: node --test tests_js
import { test } from "node:test";
import assert from "node:assert/strict";

// Just enough DOM for the module to load outside a browser.
globalThis.HTMLElement = class {};
globalThis.customElements = { get: () => true, define() {} };
globalThis.window = globalThis;

const { logic } = await import("../custom_components/nightscout_extended/www/nightscout-glucose-card.js");
const T = { urgent_low: 55, low: 80, high: 180, urgent_high: 260 };

test("classify uses Nightscout thresholds with inclusive target range", () => {
  assert.equal(logic.classify(54, 1, 15, T), "urgent_low");
  assert.equal(logic.classify(55, 1, 15, T), "low");
  assert.equal(logic.classify(79, 1, 15, T), "low");
  assert.equal(logic.classify(80, 1, 15, T), "in_range");
  assert.equal(logic.classify(180, 1, 15, T), "in_range");
  assert.equal(logic.classify(181, 1, 15, T), "high");
  assert.equal(logic.classify(260, 1, 15, T), "high");
  assert.equal(logic.classify(261, 1, 15, T), "urgent_high");
});

test("old, missing or non-numeric readings are stale", () => {
  assert.equal(logic.classify(120, 16, 15, T), "stale");
  assert.equal(logic.classify(NaN, 1, 15, T), "stale");
  assert.equal(logic.classify(120, NaN, 15, T), "stale");
});

test("night window wraps midnight and can be turned off", () => {
  const cfg = { night_mode: true, night_start: 21, night_end: 7 };
  assert.equal(logic.isNight(21, cfg), true);
  assert.equal(logic.isNight(3, cfg), true);
  assert.equal(logic.isNight(7, cfg), false);
  assert.equal(logic.isNight(12, cfg), false);
  assert.equal(logic.isNight(13, { night_mode: true, night_start: 13, night_end: 15 }), true);
  assert.equal(logic.isNight(3, { ...cfg, night_mode: false }), false);
  assert.equal(logic.isNight(3, { night_mode: true, night_start: 5, night_end: 5 }), false);
});

test("delta formatting in both units", () => {
  assert.equal(logic.formatDelta(6, false), "+6");
  assert.equal(logic.formatDelta(-18, false), "−18");
  assert.equal(logic.formatDelta(0.4, false), "±0");
  assert.equal(logic.formatDelta(0.33, true), "+0.3");
  assert.equal(logic.formatDelta(-1.05, true), "−1.0");
  assert.equal(logic.formatDelta(NaN, false), "");
});

test("age text", () => {
  assert.equal(logic.formatAge(0.5), "now");
  assert.equal(logic.formatAge(4.9), "4 min ago");
  assert.equal(logic.formatAge(125), "2 h ago");
  assert.equal(logic.formatAge(NaN), "no data");
});

test("thresholds fall back to Nightscout defaults per key", () => {
  assert.deepEqual(logic.thresholds(undefined), T);
  assert.deepEqual(logic.thresholds({ low: 70, high: "200", urgent_high: -1 }),
    { urgent_low: 55, low: 70, high: 200, urgent_high: 260 });
});

test("night colours are coloured text on black", () => {
  assert.deepEqual(logic.colors("in_range", true), { bg: "#000000", fg: "#f3e7a6" });
  assert.equal(logic.colors("high", false).fg, "#1f1f1f");
});

test("card version matches manifest.json, so browsers reload it after an update", async () => {
  const { readFile } = await import("node:fs/promises");
  const { CARD_VERSION } = await import("../custom_components/nightscout_extended/www/nightscout-glucose-card.js");
  const manifest = JSON.parse(await readFile(new URL("../custom_components/nightscout_extended/manifest.json", import.meta.url)));
  assert.equal(CARD_VERSION, manifest.version);
});
