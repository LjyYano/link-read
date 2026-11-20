import { useEffect, useRef, useState } from "react"
import { Eye, EyeOff, MoreHorizontal } from "lucide-react"

export function SettingsSecretField({ id, label, value, savedValue, onChange, onClear, disabled, clearDisabled = false }: {
  id: string
  label: string
  value: string
  savedValue: string
  onChange: (value: string) => void
  onClear?: () => void
  disabled: boolean
  clearDisabled?: boolean
}) {
  const [editing, setEditing] = useState(false)
  const [visible, setVisible] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)
  const input = useRef<HTMLInputElement>(null)
  const menu = useRef<HTMLDivElement>(null)
  const menuTrigger = useRef<HTMLButtonElement>(null)
  const changeTrigger = useRef<HTMLButtonElement>(null)
  const focusInput = useRef(false)
  const saved = Boolean(savedValue) && value === savedValue

  useEffect(() => {
    setEditing(false)
    setVisible(false)
    setMenuOpen(false)
  }, [savedValue])

  useEffect(() => {
    if (focusInput.current && input.current) {
      input.current.focus()
      input.current.select()
      focusInput.current = false
    }
  }, [editing, visible])

  useEffect(() => {
    if (!menuOpen) return
    function outside(event: Event) {
      if (event.target instanceof Node && !menu.current?.contains(event.target)) setMenuOpen(false)
    }
    function escape(event: KeyboardEvent) {
      if (event.key !== "Escape" || event.defaultPrevented || event.isComposing || event.keyCode === 229 || event.repeat) return
      if (document.querySelector('dialog[open], [role="dialog"][aria-modal="true"]')) return
      event.preventDefault()
      event.stopPropagation()
      setMenuOpen(false)
      menuTrigger.current?.focus({ preventScroll: true })
    }
    document.addEventListener("pointerdown", outside)
    document.addEventListener("focusin", outside)
    document.addEventListener("keydown", escape, true)
    return () => {
      document.removeEventListener("pointerdown", outside)
      document.removeEventListener("focusin", outside)
      document.removeEventListener("keydown", escape, true)
    }
  }, [menuOpen])

  function edit(reveal = false) {
    focusInput.current = true
    setVisible(reveal)
    setEditing(true)
    setMenuOpen(false)
  }

  return <div className="settings-field">
    <label className="settings-label" htmlFor={saved && !editing ? `${id}-change` : id}>{label}</label>
    <div className="settings-field-control">
      {saved && !editing ? <div className="settings-secret settings-secret-saved" aria-label={`${label}已保存`}>
        <span className="settings-secret-mask" aria-hidden="true">••••••••</span>
        <span className="settings-secret-state">已保存</span>
        <div className="settings-secret-actions">
          <button ref={changeTrigger} id={`${id}-change`} type="button" className="text-button" disabled={disabled}
            aria-label={`更换${label}`} onClick={() => edit()}>更换</button>
          <div ref={menu} className="settings-secret-menu">
            <button ref={menuTrigger} className="icon-button" type="button" disabled={disabled}
              aria-label={`${label}更多操作`} title="更多操作" aria-expanded={menuOpen} aria-controls={`${id}-menu`}
              onClick={() => setMenuOpen(value => !value)}><MoreHorizontal size={18} aria-hidden="true" /></button>
            {menuOpen && <div id={`${id}-menu`} className="settings-secret-popover" role="group" aria-label={`${label}操作`}>
              <button type="button" className="plain" disabled={disabled} onClick={() => edit(true)}>显示密钥</button>
              {onClear && <button type="button" className="plain settings-secret-delete" disabled={disabled || clearDisabled}
                onClick={() => { setMenuOpen(false); onClear() }}>删除已保存密钥…</button>}
            </div>}
          </div>
        </div>
      </div> : <>
        <div className="settings-secret">
          <input ref={input} id={id} aria-label={label} type={visible ? "text" : "password"}
            value={value} disabled={disabled} placeholder={`输入${label}`} autoComplete="new-password" spellCheck={false}
            onChange={event => onChange(event.target.value)} />
          <button className="icon-button settings-secret-toggle" type="button" disabled={disabled}
            aria-label={`${visible ? "隐藏" : "显示"}${label}`} title={`${visible ? "隐藏" : "显示"}密钥`}
            onClick={() => setVisible(value => !value)}>
            {visible ? <EyeOff size={17} aria-hidden="true" /> : <Eye size={17} aria-hidden="true" />}
          </button>
        </div>
        {savedValue && <button type="button" className="text-button settings-secret-undo" disabled={disabled}
          onClick={() => {
            onChange(savedValue); setEditing(false); setVisible(false)
            requestAnimationFrame(() => changeTrigger.current?.focus({ preventScroll: true }))
          }}>{saved ? "收起" : "撤销密钥修改"}</button>}
      </>}
    </div>
  </div>
}
