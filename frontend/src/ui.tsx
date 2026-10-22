import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import type { Meta, Severity, User } from './api'

// ---------------------------------------------------------------- app context
export type AppCtx = { meta: Meta | null; me: User | null; perms: Record<string, boolean>; switchUser: (id: string) => void; toast: (msg: string, kind?: 'ok' | 'err') => void; tick: number; bump: () => void }
export const Ctx = createContext<AppCtx>(null as any)
export const useApp = () => useContext(Ctx)

// ---------------------------------------------------------------- data hook
export function useLoad<T>(fn: () => Promise<T>, deps: any[]): { data: T | null; error: string; loading: boolean; reload: () => void; setData: (d: T) => void } {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [n, setN] = useState(0)
  useEffect(() => {
    let live = true
    setLoading(true)
    fn().then(d => { if (live) { setData(d); setError('') } }).catch(e => { if (live) setError(e.message) }).finally(() => live && setLoading(false))
    return () => { live = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, n])
  return { data, error, loading, reload: useCallback(() => setN(x => x + 1), []), setData }
}

// ---------------------------------------------------------------- bits
const SEV: Record<Severity, { label: string; cls: string }> = {
  block: { label: 'Blocks', cls: 'bg-block-soft text-block' },
  warn: { label: 'Warning', cls: 'bg-warn-soft text-warn' },
  suggest: { label: 'Suggestion', cls: 'bg-suggest-soft text-suggest' },
}
export function SevTag({ s, small }: { s: Severity; small?: boolean }) {
  return <span className={`inline-flex items-center rounded px-1.5 ${small ? 'text-[11px]' : 'text-xs'} font-semibold ${SEV[s].cls}`}>{SEV[s].label}</span>
}

const STATUS: Record<string, string> = {
  draft: 'bg-concrete-2 text-ink-2', in_review: 'bg-chalk-soft text-chalk', changes_requested: 'bg-warn-soft text-warn', approved: 'bg-pass-soft text-pass',
}
export function StatusTag({ status, label }: { status: string; label: string }) {
  return <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-semibold ${STATUS[status] || 'bg-concrete-2'}`}>{label}</span>
}

export function Key({ children, title }: { children: ReactNode; title?: string }) {
  return <span title={title} className="key rounded bg-concrete px-1 py-px text-ink-2">{children}</span>
}

export function Spinner({ size = 14 }: { size?: number }) {
  return <svg className="spin" width={size} height={size} viewBox="0 0 24 24" aria-hidden><circle cx="12" cy="12" r="9" stroke="currentColor" strokeOpacity=".25" strokeWidth="3" fill="none" /><path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="3" fill="none" strokeLinecap="round" /></svg>
}

export function Bob({ size = 16, className = '' }: { size?: number; className?: string }) {
  return <svg width={size} height={size * 1.35} viewBox="0 0 20 27" className={className} aria-hidden><path d="M10 0 L18 10 L10 27 L2 10 Z" fill="var(--color-brass)" /></svg>
}

export function ErrorNote({ msg }: { msg: string }) {
  if (!msg) return null
  return <div role="alert" className="rounded-md border border-[#e9b5ae] bg-block-soft px-3 py-2 text-sm text-block">{msg}</div>
}

export function PageHead({ title, sub, right }: { title: string; sub?: ReactNode; right?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div><h1 className="text-[28px] font-bold leading-tight tracking-[-0.02em]">{title}</h1>{sub && <p className="mt-1 max-w-[70ch] text-ink-2">{sub}</p>}</div>
      {right}
    </div>
  )
}

// ---------------------------------------------------------------- dialog
export function Dialog({ open, onClose, title, children }: { open: boolean; onClose: () => void; title: string; children: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const d = ref.current
    if (!d) return
    if (open && !d.open) d.showModal()
    if (!open && d.open) d.close()
  }, [open])
  return (
    <dialog ref={ref} onClose={onClose} className="m-auto w-[min(560px,calc(100vw-32px))] rounded-xl border border-rule bg-sheet p-0 text-ink shadow-2xl backdrop:bg-ink/40">
      <div className="flex items-center justify-between border-b border-rule px-5 py-3"><h2 className="text-lg font-bold">{title}</h2>
        <button className="btn btn-ghost btn-sm" onClick={onClose} aria-label="Close">Close</button></div>
      <div className="px-5 py-4">{open && children}</div>
    </dialog>
  )
}

// ---------------------------------------------------------------- toasts
export function Toasts({ items }: { items: { id: number; msg: string; kind: 'ok' | 'err' }[] }) {
  return (
    <div className="pointer-events-none fixed bottom-4 right-4 z-50 flex max-w-sm flex-col gap-2" aria-live="polite">
      {items.map(t => <div key={t.id} className={`pointer-events-auto rounded-lg px-4 py-2.5 text-sm font-medium shadow-lg ${t.kind === 'err' ? 'bg-block text-white' : 'bg-ink text-white'}`}>{t.msg}</div>)}
    </div>
  )
}
