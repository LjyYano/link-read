import { isTauri } from "@tauri-apps/api/core"
import { getCurrentWindow } from "@tauri-apps/api/window"
import { BookOpen, ListTodo, Monitor, Moon, Search, Settings as SettingsIcon, Sun } from "lucide-react"
import { tr } from "./language.ts"
import { nextThemeMode, type ResolvedTheme, type ThemeMode } from "./themeState"
import { titlebarMouseDown } from "./titlebarActions"

export type TitleBarScreen = "create" | "library" | "settings" | "reader"

export function TitleBar({
  screen,
  taskCount,
  unreadTasks,
  themeMode,
  resolvedTheme,
  onToggleTheme,
  onTasks,
  onSettings,
  onQuickOpen,
}: {
  screen: TitleBarScreen
  taskCount: number
  unreadTasks: number
  themeMode: ThemeMode
  resolvedTheme: ResolvedTheme
  onToggleTheme: (next: ThemeMode) => void
  onTasks: () => void
  onSettings: () => void
  onQuickOpen: () => void
}) {
  const themeLabel = themeMode === "system"
    ? tr("外观：跟随系统")
    : themeMode === "dark" ? tr("外观：深色") : tr("外观：浅色")

  return (
    <header className="titlebar">
      <div className="titlebar-drag" onMouseDown={event => {
        if (!isTauri()) return
        void titlebarMouseDown(event, getCurrentWindow()).catch(() => {
          console.warn(tr("窗口拖动或缩放操作未完成"))
        })
      }} />
      <div className="titlebar-brand" aria-label="LinkRead">
        <span className="titlebar-mark"><BookOpen size={13} strokeWidth={2.1} aria-hidden="true" /></span>
        <span>LinkRead</span>
      </div>
      <div className="titlebar-tools">
        <button
          className="icon-button titlebar-tasks"
          type="button"
          aria-label={tr(
            "任务中心{0}{1}",
            taskCount ? tr("，{0} 个活动任务", taskCount) : "",
            unreadTasks ? tr("，{0} 条未查看", unreadTasks) : "",
          )}
          title={tr("任务中心{0}", unreadTasks ? tr(" · {0} 条记录需要查看", unreadTasks) : "")}
          onClick={event => {
            // WebKit does not focus buttons on pointer clicks by default. Make
            // this trigger the dialog's explicit return point before mounting it.
            event.currentTarget.focus({ preventScroll: true })
            onTasks()
          }}
        >
          <ListTodo size={16} aria-hidden="true" />
          {taskCount > 0 && <span className="titlebar-task-count">{taskCount}</span>}
          {unreadTasks > 0 && <span className="titlebar-task-alert" aria-hidden="true">!</span>}
        </button>
        <button
          className="icon-button"
          type="button"
          aria-label={tr("快速打开")}
          title={tr("快速打开（⌘K / Ctrl+K）")}
          onClick={onQuickOpen}
        >
          <Search size={16} aria-hidden="true" />
        </button>
        {screen !== "reader" && (
          <button
            className="icon-button"
            type="button"
            aria-label={themeLabel}
            title={themeLabel}
            onClick={() => onToggleTheme(nextThemeMode(themeMode))}
          >
            {themeMode === "system"
              ? <Monitor size={16} aria-hidden="true" />
              : resolvedTheme === "dark"
              ? <Moon size={16} aria-hidden="true" />
              : <Sun size={16} aria-hidden="true" />}
          </button>
        )}
        {(screen === "create" || screen === "library") && (
          <button
            className="icon-button titlebar-settings"
            type="button"
            aria-label={tr("设置")}
            title={tr("设置")}
            onClick={onSettings}
          >
            <SettingsIcon size={17} aria-hidden="true" />
          </button>
        )}
      </div>
    </header>
  )
}
