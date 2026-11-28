import { useEffect, useId, useRef, useState } from "react"
import { LoaderCircle, X } from "lucide-react"
import { tr } from "./language.ts"
import "./article-dialog.css"

export type ConfirmDialogProps = {
  title: string
  message: string
  confirmLabel?: string
  cancelLabel?: string
  danger?: boolean
  onConfirm: () => void | Promise<void>
  onCancel: () => void
}

export function ConfirmDialog({
  title,
  message,
  confirmLabel,
  cancelLabel,
  danger = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const id = useId()
  const dialog = useRef<HTMLDialogElement>(null)
  const cancelButton = useRef<HTMLButtonElement>(null)
  const confirmButton = useRef<HTMLButtonElement>(null)
  const mounted = useRef(true)
  const running = useRef(false)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState("")

  useEffect(() => {
    mounted.current = true
    const previous = document.activeElement
    const element = dialog.current
    if (element && !element.open) element.showModal()
    ;(danger ? cancelButton : confirmButton).current?.focus({ preventScroll: true })
    return () => {
      mounted.current = false
      if (element?.open) element.close()
      if (previous instanceof HTMLElement && previous.isConnected) previous.focus({ preventScroll: true })
    }
  }, [danger])

  async function handleConfirm() {
    if (running.current) return
    running.current = true
    setPending(true)
    setError("")
    try {
      await onConfirm()
    } catch (failure) {
      if (mounted.current) setError(failure instanceof Error ? failure.message : String(failure))
    } finally {
      running.current = false
      if (mounted.current) setPending(false)
    }
  }

  const paragraphs = message.split("\n").filter(Boolean)

  return (
    <dialog
      ref={dialog}
      className="article-dialog confirm-dialog"
      aria-labelledby={`${id}-title`}
      aria-describedby={`${id}-message`}
      onCancel={event => {
        event.preventDefault()
        event.stopPropagation()
        if (!pending) onCancel()
      }}
      onKeyDown={event => {
        if (event.key === "Escape") {
          event.stopPropagation()
          if (event.repeat || event.nativeEvent.isComposing || pending) event.preventDefault()
          else onCancel()
        }
      }}
      onClick={event => {
        if (pending || event.target !== event.currentTarget) return
        const bounds = event.currentTarget.getBoundingClientRect()
        if (event.clientX < bounds.left || event.clientX > bounds.right
          || event.clientY < bounds.top || event.clientY > bounds.bottom) {
          onCancel()
        }
      }}
    >
      <header>
        <h2 id={`${id}-title`}>{title}</h2>
        <button
          className="icon-button"
          type="button"
          aria-label={tr("关闭")}
          disabled={pending}
          onClick={onCancel}
        >
          <X size={18} aria-hidden="true" />
        </button>
      </header>
      <div id={`${id}-message`} className="article-dialog-body confirm-dialog-body">
        {paragraphs.map((line, index) => (
          <p key={index} className={index === 0 ? "confirm-dialog-lead" : "article-dialog-subtitle"}>{line}</p>
        ))}
        {error && <p className="error-line" role="alert">{error}</p>}
      </div>
      <footer>
        <button
          ref={cancelButton}
          type="button"
          className="secondary"
          disabled={pending}
          onClick={onCancel}
        >
          {cancelLabel ?? tr("取消")}
        </button>
        <button
          ref={confirmButton}
          type="button"
          className={danger ? "danger-button" : "primary"}
          disabled={pending}
          onClick={() => void handleConfirm()}
        >
          {pending && <LoaderCircle size={14} className="spin" aria-hidden="true" />}
          {confirmLabel ?? tr("确认")}
        </button>
      </footer>
    </dialog>
  )
}
