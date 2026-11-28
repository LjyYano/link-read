import assert from "node:assert/strict"
import test from "node:test"
import {
  builtinCliCatalog, effortShortLabel, effortsForSelection, filterCliModels,
  isValidCliModel, loadCliPreferences, reconcileEffortForModel,
} from "../src/cliModels.ts"
import { setLocale } from "../src/language.ts"

test("CLI model filtering matches by label and ID and keeps custom saved models visible", () => {
  const catalog = builtinCliCatalog("codex")
  const byId = filterCliModels(catalog.models, "o4-mini", "")
  assert.deepEqual(byId.map(m => m.id), ["o4-mini"])

  const withCustom = filterCliModels(catalog.models, "", "gpt-custom-preview")
  assert.equal(withCustom[0].id, "gpt-custom-preview")
  assert.equal(withCustom.length, catalog.models.length + 1)

  const filteredCustom = filterCliModels(catalog.models, "custom", "gpt-custom-preview")
  assert.deepEqual(filteredCustom.map(m => m.id), ["gpt-custom-preview"])
})

test("reconcileEffortForModel preserves supported efforts and clears unsupported ones", () => {
  const codexCatalog = {
    source: "cache",
    supportedEfforts: ["low", "medium", "high", "xhigh", "max", "ultra"],
    models: [
      { id: "gpt-6.2-sol", label: "GPT-6.2-Sol", efforts: ["low", "medium", "high", "xhigh", "max", "ultra"] },
      { id: "gpt-6-luna", label: "GPT-6-Luna", efforts: ["low", "medium", "high"] },
    ],
  }
  assert.equal(reconcileEffortForModel("codex", codexCatalog, "gpt-6.2-sol", "ultra"), "ultra")
  assert.equal(reconcileEffortForModel("codex", codexCatalog, "gpt-6-luna", "high"), "high")
  assert.equal(reconcileEffortForModel("codex", codexCatalog, "gpt-6-luna", "ultra"), "")
  assert.equal(reconcileEffortForModel("codex", codexCatalog, "gpt-6-luna", ""), "")

  const claudeCatalog = builtinCliCatalog("claude")
  assert.deepEqual(effortsForSelection("claude", claudeCatalog, "haiku"), ["low", "medium", "high"])
  assert.equal(reconcileEffortForModel("claude", claudeCatalog, "haiku", "max"), "")
  assert.equal(reconcileEffortForModel("claude", claudeCatalog, "opus", "max"), "max")
})

test("loadCliPreferences sanitizes invalid models and efforts while preserving valid ones", () => {
  const loaded = loadCliPreferences({
    codex: { model: " gpt-6.2-sol ", reasoningEffort: "XHigh" },
    claude: { model: "--bad", reasoningEffort: "ultra" },
  })
  assert.deepEqual(loaded, {
    codex: { model: "gpt-6.2-sol", reasoningEffort: "xhigh" },
    claude: { model: "", reasoningEffort: "" },
  })
  assert.equal(isValidCliModel("claude-fable-5-2[1m]"), true)
  assert.equal(isValidCliModel("-flag"), false)
  assert.equal(isValidCliModel("bad\nmodel"), false)
})

test("effortShortLabel localizes default cleanly in Chinese and English", () => {
  setLocale("zh-CN")
  assert.equal(effortShortLabel(""), "默认")
  assert.equal(effortShortLabel("medium"), "Med")
  assert.equal(effortShortLabel("xhigh"), "XHigh")
  setLocale("en")
  try {
    assert.equal(effortShortLabel(""), "Auto")
    assert.equal(effortShortLabel("high"), "High")
  } finally {
    setLocale("zh-CN")
  }
})
