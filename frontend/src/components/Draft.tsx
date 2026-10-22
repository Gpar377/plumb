import { Fragment } from 'react'
import type { Finding } from '../api'

const RANK: Record<string, number> = { block: 3, warn: 2, suggest: 1 }
export const isOpen = (f: Finding) => ['open', 'disputed', 'upheld'].includes(f.status)

/** Render the draft with every finding anchored to its exact span. */
export default function Draft({ text, findings, active, onPick }: { text: string; findings: Finding[]; active: number | null; onPick: (id: number) => void }) {
  const num = new Map(findings.map((f, i) => [f.id, i + 1]))
  const ranged = findings.filter(f => f.end > f.start)
  const pins = findings.filter(f => f.end === f.start)
  const cuts = new Set<number>([0, text.length])
  ranged.forEach(f => { cuts.add(f.start); cuts.add(f.end) })
  pins.forEach(f => cuts.add(Math.min(f.start, text.length)))
  const pts = [...cuts].sort((a, b) => a - b)
  const out: React.ReactNode[] = []
  pts.forEach((a, i) => {
    for (const p of pins.filter(p => Math.min(p.start, text.length) === a)) {
      out.push(
        <button key={`pin${p.id}`} type="button" onClick={() => onPick(p.id)} className={`mark mark-${isOpen(p) ? p.severity : 'done'} ${active === p.id ? 'active' : ''}`}
          aria-label={`Finding ${num.get(p.id)}: ${p.title}`}><span className="insert-pin" /><sup>{num.get(p.id)}</sup></button>,
      )
    }
    const b = pts[i + 1]
    if (b === undefined || b <= a) return
    const seg = text.slice(a, b)
    const cover = ranged.filter(f => f.start <= a && f.end >= b)
    if (!cover.length) { out.push(<Fragment key={a}>{seg}</Fragment>); return }
    const open = cover.filter(isOpen)
    const top = [...(open.length ? open : cover)].sort((x, y) => RANK[y.severity] - RANK[x.severity])[0]
    const sel = cover.some(f => f.id === active)
    out.push(
      <span key={a} role="button" tabIndex={0} onClick={() => onPick(top.id)}
        onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onPick(top.id) } }}
        className={`mark ${open.length ? `mark-${top.severity}` : 'mark-done'} ${sel ? 'active' : ''}`} aria-label={`${seg}: ${top.title}`}>
        {seg}{cover.filter(f => f.end === b).map(f => <sup key={f.id}>{num.get(f.id)}</sup>)}
      </span>,
    )
  })
  return <div className="whitespace-pre-wrap font-serif text-[18px] leading-[1.75] text-ink">{out}</div>
}
