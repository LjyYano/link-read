import { tr } from "./language.ts"

export type CliProvider = "codex" | "claude"
export type CliReasoningEffort = "" | "minimal" | "low" | "medium" | "high" | "xhigh" | "max" | "ultra"
export type NonEmptyReasoningEffort = Exclude<CliReasoningEffort, "">

export type CliModelSettings = {
  model: string
  reasoningEffort: CliReasoningEffort
}

export type CliPreferences = {
  codex: CliModelSettings
  claude: CliModelSettings
}

export type CliModelOption = {
  id: string
  label: string
  efforts: NonEmptyReasoningEffort[]
  defaultEffort?: NonEmptyReasoningEffort
  description?: string
}

export type CliModelCatalog = {
  models: CliModelOption[]
  defaultModel?: string | null
  defaultEffort?: NonEmptyReasoningEffort | null
  supportedEfforts: NonEmptyReasoningEffort[]
  source: "cache" | "builtin"
}

export const allReasoningEfforts: readonly NonEmptyReasoningEffort[] = [
  "minimal", "low", "medium", "high", "xhigh", "max", "ultra",
]

export const providerReasoningEfforts: Readonly<Record<CliProvider, readonly NonEmptyReasoningEffort[]>> = {
  codex: ["minimal", "low", "medium", "high", "xhigh", "max", "ultra"],
  claude: ["low", "medium", "high", "xhigh", "max"],
}

export const defaultCliModelSettings: CliModelSettings = { model: "", reasoningEffort: "" }

export function defaultCliPreferences(): CliPreferences {
  return {
    codex: { ...defaultCliModelSettings },
    claude: { ...defaultCliModelSettings },
  }
}

const controlChars = /[\u0000-\u001f\u007f-\u009f]/
const utf8Bytes = (value: string) => new TextEncoder().encode(value).byteLength

export function isValidCliModel(value: string): boolean {
  return value.length > 0
    && utf8Bytes(value) <= 256
    && !value.startsWith("-")
    && !controlChars.test(value)
}

export function isValidReasoningEffort(provider: CliProvider, value: string): value is CliReasoningEffort {
  return value === "" || (providerReasoningEfforts[provider] as readonly string[]).includes(value)
}

export function isAnyReasoningEffort(value: string): value is CliReasoningEffort {
  return value === "" || (allReasoningEfforts as readonly string[]).includes(value)
}

export function loadCliModelSettings(provider: CliProvider, raw: unknown): CliModelSettings {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return { ...defaultCliModelSettings }
  const obj = raw as Record<string, unknown>
  const rawModel = typeof obj.model === "string" ? obj.model.trim() : ""
  const rawEffort = typeof obj.reasoningEffort === "string" ? obj.reasoningEffort.trim().toLowerCase() : ""
  return {
    model: isValidCliModel(rawModel) ? rawModel : "",
    reasoningEffort: isValidReasoningEffort(provider, rawEffort) ? rawEffort : "",
  }
}

export function loadCliPreferences(raw: unknown): CliPreferences {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return defaultCliPreferences()
  const obj = raw as Record<string, unknown>
  return {
    codex: loadCliModelSettings("codex", obj.codex),
    claude: loadCliModelSettings("claude", obj.claude),
  }
}

export function builtinCliCatalog(provider: CliProvider): CliModelCatalog {
  if (provider === "codex") {
    return {
      source: "builtin",
      defaultModel: null,
      defaultEffort: null,
      supportedEfforts: ["low", "medium", "high", "xhigh"],
      models: [
        { id: "gpt-5.4", label: "GPT-5.4", efforts: ["low", "medium", "high", "xhigh"], defaultEffort: "medium" },
        { id: "gpt-5.3-codex", label: "GPT-5.3-Codex", efforts: ["low", "medium", "high", "xhigh"], defaultEffort: "medium" },
        { id: "gpt-5.2-codex", label: "GPT-5.2-Codex", efforts: ["low", "medium", "high", "xhigh"], defaultEffort: "medium" },
        { id: "gpt-5.1-codex", label: "GPT-5.1-Codex", efforts: ["low", "medium", "high"], defaultEffort: "medium" },
        { id: "gpt-5.1-codex-mini", label: "GPT-5.1-Codex-Mini", efforts: ["low", "medium", "high"], defaultEffort: "medium" },
        { id: "o3", label: "o3", efforts: ["low", "medium", "high"], defaultEffort: "medium" },
        { id: "o4-mini", label: "o4-mini", efforts: ["low", "medium", "high"], defaultEffort: "medium" },
      ],
    }
  }
  return {
    source: "builtin",
    defaultModel: null,
    defaultEffort: null,
    supportedEfforts: ["low", "medium", "high", "xhigh", "max"],
    models: [
      { id: "sonnet", label: "Claude Sonnet", efforts: ["low", "medium", "high", "xhigh", "max"], defaultEffort: "medium" },
      { id: "opus", label: "Claude Opus", efforts: ["low", "medium", "high", "xhigh", "max"], defaultEffort: "high" },
      { id: "haiku", label: "Claude Haiku", efforts: ["low", "medium", "high"], defaultEffort: "medium" },
      { id: "claude-sonnet-4-6", label: "Claude Sonnet 4.6", efforts: ["low", "medium", "high", "xhigh", "max"], defaultEffort: "medium" },
      { id: "claude-opus-4-6", label: "Claude Opus 4.6", efforts: ["low", "medium", "high", "xhigh", "max"], defaultEffort: "high" },
      { id: "claude-haiku-4-5", label: "Claude Haiku 4.5", efforts: ["low", "medium", "high"], defaultEffort: "medium" },
    ],
  }
}

export function effortShortLabel(effort: CliReasoningEffort): string {
  switch (effort) {
    case "": return tr("默认")
    case "minimal": return "Min"
    case "low": return "Low"
    case "medium": return "Med"
    case "high": return "High"
    case "xhigh": return "XHigh"
    case "max": return "Max"
    case "ultra": return "Ultra"
  }
}

export function effortDescription(effort: CliReasoningEffort): string {
  switch (effort) {
    case "": return tr("跟随 CLI 默认思考深度")
    case "minimal": return tr("极简思考，速度最快")
    case "low": return tr("轻度思考，响应较快")
    case "medium": return tr("平衡响应速度与分析深度")
    case "high": return tr("更深入的推理与整理")
    case "xhigh": return tr("极高思考深度，适合复杂内容")
    case "max": return tr("最大思考深度")
    case "ultra": return tr("最高思考深度")
  }
}

export function effortsForSelection(
  provider: CliProvider,
  catalog: CliModelCatalog,
  selectedModelId: string,
): NonEmptyReasoningEffort[] {
  const allowed = providerReasoningEfforts[provider]
  const found = selectedModelId ? catalog.models.find(item => item.id === selectedModelId) : undefined
  const raw = found?.efforts?.length ? found.efforts : catalog.supportedEfforts
  const filtered = raw.filter((effort): effort is NonEmptyReasoningEffort =>
    (allowed as readonly string[]).includes(effort))
  if (filtered.length) return filtered
  return provider === "codex" ? ["low", "medium", "high", "xhigh"] : ["low", "medium", "high", "xhigh", "max"]
}

export function reconcileEffortForModel(
  provider: CliProvider,
  catalog: CliModelCatalog,
  nextModelId: string,
  currentEffort: CliReasoningEffort,
): CliReasoningEffort {
  if (!currentEffort) return ""
  const supported = effortsForSelection(provider, catalog, nextModelId)
  return (supported as readonly string[]).includes(currentEffort) ? currentEffort : ""
}

export function filterCliModels(
  models: readonly CliModelOption[],
  query: string,
  currentModelId: string,
): CliModelOption[] {
  const normalized = query.trim().toLowerCase()
  const base = currentModelId && !models.some(item => item.id === currentModelId)
    ? [{ id: currentModelId, label: currentModelId, efforts: [] }, ...models]
    : [...models]
  if (!normalized) return base
  return base.filter(item =>
    item.label.toLowerCase().includes(normalized) || item.id.toLowerCase().includes(normalized))
}
