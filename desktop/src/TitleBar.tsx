import type { Ref } from "react"
import { isTauri } from "@tauri-apps/api/core"
import { getCurrentWindow } from "@tauri-apps/api/window"
import { ChevronLeft, ListTodo, Monitor, Moon, Search, Settings as SettingsIcon, Sun } from "lucide-react"
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
  onNavigate,
  onBackFromSettings,
  onTasks,
  onSettings,
  onQuickOpen,
  readerLeadingRef,
  readerCenterRef,
  readerActionsRef,
}: {
  screen: TitleBarScreen
  taskCount: number
  unreadTasks: number
  themeMode: ThemeMode
  resolvedTheme: ResolvedTheme
  onToggleTheme: (next: ThemeMode) => void
  onNavigate: (next: "create" | "library") => void
  onBackFromSettings: () => void
  onTasks: () => void
  onSettings: () => void
  onQuickOpen: () => void
  readerLeadingRef?: Ref<HTMLDivElement>
  readerCenterRef?: Ref<HTMLDivElement>
  readerActionsRef?: Ref<HTMLDivElement>
}) {
  const themeLabel = themeMode === "system"
    ? tr("外观：跟随系统")
    : themeMode === "dark" ? tr("外观：深色") : tr("外观：浅色")
  const tasksTooltip = tr("任务中心{0}", unreadTasks ? tr(" · {0} 条记录需要查看", unreadTasks) : "")

  return (
    <header className={`titlebar titlebar-${screen}`}>
      <div className="titlebar-drag" onMouseDown={event => {
        if (!isTauri()) return
        void titlebarMouseDown(event, getCurrentWindow()).catch(() => {
          console.warn(tr("窗口拖动或缩放操作未完成"))
        })
      }} />
      <div className="titlebar-leading">
        {screen === "settings" && (
          <button
            className="titlebar-back"
            type="button"
            aria-label={tr("返回")}
            data-tooltip={tr("返回")}
            onClick={onBackFromSettings}
          >
            <ChevronLeft size={17} strokeWidth={2.1} aria-hidden="true" />
            <span>{tr("返回")}</span>
          </button>
        )}
        {screen === "reader" && <div className="titlebar-slot" ref={readerLeadingRef} />}
      </div>
      <div className="titlebar-center">
        {(screen === "create" || screen === "library") && (
          <nav className="titlebar-segmented" aria-label={tr("主视图")}>
            <button
              type="button"
              className={screen === "create" ? "is-active" : undefined}
              aria-current={screen === "create" ? "page" : undefined}
              onClick={() => onNavigate("create")}
            >
              {tr("首页")}
            </button>
            <button
              type="button"
              className={screen === "library" ? "is-active" : undefined}
              aria-current={screen === "library" ? "page" : undefined}
              onClick={() => onNavigate("library")}
            >
              {tr("书架")}
            </button>
          </nav>
        )}
        {screen === "settings" && (
          <span className="titlebar-page-title">{tr("设置")}</span>
        )}
        {screen === "reader" && <div className="titlebar-slot titlebar-slot-center" ref={readerCenterRef} />}
      </div>
      <div className="titlebar-tools">
        {screen === "reader" && (
          <>
            <div className="titlebar-slot titlebar-slot-actions" ref={readerActionsRef} />
            <span className="titlebar-divider" aria-hidden="true" />
          </>
        )}
        <button
          className="icon-button titlebar-tasks"
          type="button"
          aria-label={tr(
            "任务中心{0}{1}",
            taskCount ? tr("，{0} 个活动任务", taskCount) : "",
            unreadTasks ? tr("，{0} 条未查看", unreadTasks) : "",
          )}
          data-tooltip={tasksTooltip}
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
          data-tooltip={tr("快速打开（⌘K / Ctrl+K）")}
          onClick={onQuickOpen}
        >
          <Search size={16} aria-hidden="true" />
        </button>
        {(screen === "create" || screen === "library") && (
          <>
            <button
              className="icon-button"
              type="button"
              aria-label={themeLabel}
              data-tooltip={themeLabel}
              onClick={() => onToggleTheme(nextThemeMode(themeMode))}
            >
              {themeMode === "system"
                ? <Monitor size={16} aria-hidden="true" />
                : resolvedTheme === "dark"
                ? <Moon size={16} aria-hidden="true" />
                : <Sun size={16} aria-hidden="true" />}
            </button>
            <button
              className="icon-button titlebar-settings"
              type="button"
              aria-label={tr("设置")}
              data-tooltip={tr("设置")}
              onClick={onSettings}
            >
              <SettingsIcon size={16} aria-hidden="true" />
            </button>
          </>
        )}
      </div>
    </header>
  )
}
