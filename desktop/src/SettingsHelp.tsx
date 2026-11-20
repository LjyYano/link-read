import { useEffect, useId, useRef, useState, type ReactNode } from "react"
import { CircleHelp } from "lucide-react"

export function SettingsHelp({ label, children }: { label: string; children: ReactNode }) {
  const [open, setOpen] = useState(false)
  const id = useId()
  const root = useRef<HTMLDivElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!open) return
    function outside(event: Event) {
      if (event.target instanceof Node && !root.current?.contains(event.target)) setOpen(false)
    }
    function escape(event: KeyboardEvent) {
      if (event.key !== "Escape" || event.defaultPrevented || event.isComposing || event.keyCode === 229 || event.repeat) return
      if (document.querySelector('dialog[open], [role="dialog"][aria-modal="true"]')) return
      event.preventDefault()
      event.stopPropagation()
      setOpen(false)
      trigger.current?.focus({ preventScroll: true })
    }
    document.addEventListener("pointerdown", outside)
    document.addEventListener("focusin", outside)
    document.addEventListener("keydown", escape, true)
    return () => {
      document.removeEventListener("pointerdown", outside)
      document.removeEventListener("focusin", outside)
      document.removeEventListener("keydown", escape, true)
    }
  }, [open])

  return <div className="settings-help" ref={root}>
    <button ref={trigger} type="button" className="icon-button settings-help-trigger"
      aria-label={label} title={label} aria-expanded={open} aria-controls={id}
      onClick={() => setOpen(value => !value)}>
      <CircleHelp size={17} aria-hidden="true" />
    </button>
    {open && <div className="settings-help-popover" id={id} role="note">{children}</div>}
  </div>
}
