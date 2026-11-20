import type { KeyboardEvent } from "react"

export function menuNavigation(event: KeyboardEvent<HTMLElement>) {
  if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return false
  const items = [...event.currentTarget.querySelectorAll<HTMLButtonElement>("[data-action-menu] button:not(:disabled)")]
  if (!items.length) return false
  const index = items.indexOf(document.activeElement as HTMLButtonElement)
  const next = event.key === "Home" ? 0 : event.key === "End" ? items.length - 1
    : event.key === "ArrowDown" ? (index + 1) % items.length
      : index <= 0 ? items.length - 1 : index - 1
  event.preventDefault()
  items[next].focus()
  return true
}
