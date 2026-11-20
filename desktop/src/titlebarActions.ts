type MousePress = { button: number; buttons: number; detail: number }
type WindowActions = { startDragging(): Promise<void>; toggleMaximize(): Promise<void> }

/** Single left press drags; each double left press toggles maximize/restore once. */
export async function titlebarMouseDown(event: MousePress, window: WindowActions): Promise<void> {
  if (event.button !== 0 || event.buttons !== 1) return
  if (event.detail === 2) await window.toggleMaximize()
  else if (event.detail === 1) await window.startDragging()
}
