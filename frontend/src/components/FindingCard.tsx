import { useState } from 'react'
import { CHECK_LABEL, type Finding } from '../api'
import { Key, SevTag } from '../ui'
import { isOpen } from './Draft'

const SOURCE: Record<string, string> = { rule: 'Rule check', nli: 'Local NLI model', llm: 'AI judgment', heuristic: 'Rule check' }
const STATUS: Record<string, string> = { accepted: 'Fix accepted', disputed: 'Disputed', upheld: 'Upheld by reviewer', overridden: 'Overridden by reviewer', resolved: 'Resolved' }

export default function FindingCard({ f, n, active, onFocus, canFix, canDispute, canResolve, selected, onSelect, onAccept, onDispute, onResolve, busy }: {
  f: Finding; n: number; active: boolean; onFocus: () => void; canFix: boolean; canDispute: boolean; canResolve: boolean
  selected: boolean; onSelect: (v: boolean) => void; onAccept: (replacement?: string) => void; onDispute: () => void; onResolve: (a: 'override' | 'uphold') => void; busy: boolean
}) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(f.fix ?? '')
  const [why, setWhy] = useState(false)
  const open = isOpen(f)
  const hasFix = f.fix !== null && f.status === 'open'
  return (
    <article id={`f-${f.id}`} onClick={onFocus}
      className={`rounded-lg border bg-sheet p-3.5 transition-colors ${active ? 'border-ink ring-1 ring-ink' : 'border-rule'} ${open ? '' : 'opacity-70'}`}>
      <header className="flex items-start gap-2.5">
        {canFix && hasFix && f.fix_verified && (
          <input type="checkbox" className="mt-1" checked={selected} onChange={e => onSelect(e.target.checked)} onClick={e => e.stopPropagation()} aria-label={`Select fix ${n}`} />
        )}
        <span className="mt-0.5 flex h-5 min-w-5 items-center justify-center rounded bg-ink px-1 text-[11px] font-bold text-white">{n}</span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <SevTag s={f.severity} small />
            <span className="text-xs text-mute">{CHECK_LABEL[f.check]}</span>
            {f.verdict && <span className="text-xs font-semibold text-ink-2">{f.verdict}</span>}
            {!open && <span className="text-xs font-semibold text-pass">{STATUS[f.status]}</span>}
            {f.status === 'disputed' && <span className="text-xs font-semibold text-warn">Disputed, waiting for a reviewer</span>}
          </div>
          <h3 className="mt-1 font-semibold leading-snug">{f.title}</h3>
          <p className="mt-0.5 text-sm text-ink-2">{f.message}</p>
        </div>
      </header>

      {hasFix && !editing && (
        <div className="mt-2.5 rounded-md bg-concrete/60 px-2.5 py-2 font-serif text-[15px]">
          {f.end > f.start && <del className="text-block decoration-1">{f.excerpt}</del>}
          {f.end > f.start && f.fix !== '' && <span className="mx-1.5 font-sans text-mute" aria-hidden>to</span>}
          {f.fix !== '' ? <ins className="rounded bg-pass-soft px-1 text-pass no-underline">{f.fix}</ins> : <span className="ml-1.5 font-sans text-sm text-mute">(remove)</span>}
          <p className={`mt-1 font-sans text-xs ${f.fix_verified ? 'text-pass' : 'text-warn'}`}>{f.fix_verified ? '✓ ' : ''}{f.fix_note}</p>
        </div>
      )}
      {!hasFix && open && f.fix_note && <p className="mt-2 text-xs text-mute">{f.fix_note}</p>}
      {editing && (
        <div className="mt-2.5 space-y-2" onClick={e => e.stopPropagation()}>
          <label className="lbl" htmlFor={`e${f.id}`}>Your wording for “{f.excerpt}”</label>
          <input id={`e${f.id}`} className="field font-serif" value={draft} onChange={e => setDraft(e.target.value)} />
          <div className="flex gap-2"><button className="btn btn-primary btn-sm" disabled={busy} onClick={() => onAccept(draft)}>Apply my wording</button>
            <button className="btn btn-ghost btn-sm" onClick={() => setEditing(false)}>Cancel</button></div>
          <p className="text-xs text-mute">Plumb re-checks the new version, so an edit that still breaks the rule will be flagged again.</p>
        </div>
      )}

      <div className="mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-xs">
        {f.rule_key && <Key title="Policy rule">{f.rule_key}</Key>}
        {f.fact_key && <Key title="Facts Ledger entry">{f.fact_key}</Key>}
        {f.extra?.replacement_fact && <span className="text-mute">current: <Key>{f.extra.replacement_fact}</Key></span>}
        <span className="text-mute">{SOURCE[f.source] || f.source}{f.source !== 'rule' ? `, confidence ${Math.round(f.confidence * 100)}%` : ''}</span>
        <button type="button" className="font-semibold text-chalk hover:underline" onClick={e => { e.stopPropagation(); setWhy(w => !w) }} aria-expanded={why}>{why ? 'Hide source' : 'Show source'}</button>
      </div>
      {why && <pre className="mt-2 whitespace-pre-wrap rounded-md border border-rule bg-concrete/40 p-2.5 font-sans text-[13px] leading-snug text-ink-2">{f.citation}</pre>}
      {f.decisions.length > 0 && (
        <ul className="mt-2 space-y-1 border-l-2 border-rule pl-2.5 text-xs text-ink-2">
          {f.decisions.map((d, i) => <li key={i}><b>{d.by}</b> {d.action === 'accept' ? 'accepted the fix' : d.action === 'edit' ? 'edited the fix' : `${d.action === 'dispute' ? 'disputed' : d.action === 'override' ? 'overrode' : 'upheld'}`}{d.reason ? `: “${d.reason}”` : ''}</li>)}
        </ul>
      )}

      {(canFix || canDispute || canResolve) && (
        <div className="mt-3 flex flex-wrap gap-2" onClick={e => e.stopPropagation()}>
          {canFix && hasFix && !editing && <button className="btn btn-primary btn-sm" disabled={busy} onClick={() => onAccept()}>{f.fix === '' ? 'Remove phrase' : 'Accept fix'}</button>}
          {canFix && f.status === 'open' && f.end > f.start && !editing && <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => { setDraft(f.fix ?? f.excerpt); setEditing(true) }}>Edit wording</button>}
          {canDispute && (f.status === 'open' || f.status === 'upheld') && <button className="btn btn-ghost btn-sm" disabled={busy} onClick={onDispute}>Dispute</button>}
          {canResolve && (f.status === 'open' || f.status === 'disputed') && <>
            <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => onResolve('override')}>Override</button>
            {f.status === 'disputed' && <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => onResolve('uphold')}>Uphold</button>}
          </>}
        </div>
      )}
    </article>
  )
}
