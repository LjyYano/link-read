import type { GenerationFailure } from "./api"
import { tr } from "./language.ts"
import { SettingsSaveError, type Preferences } from "./settingsState.ts"
import type { GenerationInput } from "./taskJournal"

type SettingsSnapshot = Pick<GenerationInput, "provider" | "baseUrl" | "model" | "asr">
type SettingsStorage = {
  readPreferences: () => Preferences
  hasPendingWrite: () => boolean
  setPendingWrite: (pending: boolean) => void
}

function matchesSavedSettings(input: SettingsSnapshot, saved: Preferences): boolean {
  return input.provider === saved.provider && input.baseUrl === saved.api.baseUrl && input.model === saved.api.model
    && input.asr.enabled === saved.asr.enabled && input.asr.baseUrl === saved.asr.baseUrl
    && input.asr.model === saved.asr.model && input.asr.reuseToken === saved.asr.reuseToken
}

const pendingMessage = "上次未能完成设置保存。请核对服务地址和密钥，然后重新保存设置。"

/** Serialize native credential reads with the entire settings write and rollback. */
export class GenerationSettingsGate {
  private available = Promise.resolve()
  private readonly storage: SettingsStorage

  constructor(storage: SettingsStorage) { this.storage = storage }

  private async acquire(): Promise<() => void> {
    const previous = this.available
    let release!: () => void
    this.available = new Promise<void>(resolve => { release = resolve })
    await previous
    return release
  }

  async admit(input: SettingsSnapshot): Promise<() => void> {
    const release = await this.acquire()
    try {
      if (this.storage.hasPendingWrite()) {
        throw { message: tr(pendingMessage), code: "settings_pending", stage: null,
          retryable: true, action: "settings" } satisfies GenerationFailure
      }
      if (!matchesSavedSettings(input, this.storage.readPreferences())) {
        throw { message: tr("设置已变更，请重试以使用当前设置。"), code: "settings_changed", stage: null,
          retryable: true, action: "retry" } satisfies GenerationFailure
      }
      return release
    } catch (error) {
      release()
      throw error
    }
  }

  async withLock<T>(operation: () => Promise<T>): Promise<T> {
    const release = await this.acquire()
    try { return await operation() } finally { release() }
  }

  private markWrite(pending: boolean) {
    try { this.storage.setPendingWrite(pending) } catch {
      throw new SettingsSaveError(tr("无法确认设置保存状态，请检查设置后重试。"))
    }
  }

  save<T>(operation: () => Promise<T>): Promise<T> {
    return this.withLock(async () => {
      const wasPending = this.storage.hasPendingWrite()
      // Persist only a flag. An interrupted write must be reviewed on the next launch.
      this.markWrite(true)
      let result: T
      try {
        result = await operation()
      } catch (error) {
        if (!wasPending && !(error instanceof SettingsSaveError && error.rollbackFailed)) this.markWrite(false)
        throw error
      }
      this.markWrite(false)
      return result
    })
  }
}
