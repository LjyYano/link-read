import { isTauri } from "@tauri-apps/api/core"
import { useCallback, useEffect, useRef, useState } from "react"
import { checkAppUpdate, installAppUpdate, type AppUpdateCheck } from "./api"
import { tr } from "./language"

type Available = Extract<AppUpdateCheck, { status: "available" }>
export type UpdatePrompt = Exclude<AppUpdateCheck, Available>
  | { status: "checking" | "checkFailed" }
  | { status: "installing"; version: string }
  | (Available & { error?: string })

export function useAppUpdates(canRestart: () => boolean, saveBeforeRestart: () => Promise<void>) {
  const [prompt, setPrompt] = useState<UpdatePrompt>()
  const request = useRef<{ manual: boolean; dismissed: boolean } | undefined>(undefined)
  const checked = useRef(false)
  const installing = useRef(false)
  const check = useCallback((manual = true) => {
    if (installing.current) return
    if (manual) setPrompt({ status: "checking" })
    if (request.current) {
      if (manual) { request.current.manual = true; request.current.dismissed = false }
      return
    }
    const current = { manual, dismissed: false }
    checked.current = true
    request.current = current
    void checkAppUpdate().then(result => {
      if (request.current === current && !current.dismissed && (current.manual || result.status === "available")) {
        setPrompt(result)
      }
    }, () => {
      if (request.current === current && current.manual && !current.dismissed) setPrompt({ status: "checkFailed" })
    }).finally(() => { if (request.current === current) request.current = undefined })
  }, [])

  useEffect(() => {
    if (!isTauri()) return
    const timer = window.setTimeout(() => { if (!checked.current) check(false) }, 1200)
    return () => { window.clearTimeout(timer); request.current = undefined }
  }, [check])

  function dismiss() {
    if (installing.current) return
    if (request.current) request.current.dismissed = true
    setPrompt(undefined)
  }

  async function install() {
    if (prompt?.status !== "available" || installing.current || !canRestart()) return
    const update = prompt
    installing.current = true
    setPrompt({ status: "installing", version: update.version })
    try {
      await saveBeforeRestart()
    } catch {
      installing.current = false
      setPrompt({ ...update, error: tr("无法保存任务记录。请处理后再安装更新。") })
      return
    }
    try {
      await installAppUpdate(update.version)
    } catch (error) {
      installing.current = false
      const message = error === "update_busy" ? tr("请等当前任务结束后再安装更新。")
        : error === "update_install_failed" ? tr("无法安装更新。请确认 LinkRead 已放在「应用程序」文件夹中。")
        : tr("无法下载或验证更新，请重试。")
      setPrompt({ ...update, error: message })
    }
  }
  return { prompt, check, dismiss, install }
}
