import { useEffect, useState } from 'react'
import { api, type Rule } from '../api'
import { ErrorNote, Key, PageHead, SevTag, Spinner, useApp, useLoad } from '../ui'

type PolicyData = {
  versions: { number: number; note: string; published_by: string; published_at: string; is_current: boolean; rule_count: number; diff: { added: string[]; removed: string[]; changed: string[] } }[]
  rules: Rule[]; imports: { id: number; filename: string; status: string; proposed: number; dropped_uncited: number; duplicates: number; error: string; created_at: string }[]
  pending_review: boolean; unpublished_changes: string[]; can_edit: boolean
}
const TYPE_LABEL: Record<string, string> = { term: 'Terminology', voice: 'Voice', claim: 'Claims', a11y: 'Accessibility', format: 'Channel rules' }

export default function Policy() {
  const { tick, toast, meta } = useApp()
  const { data, error, reload } = useLoad<PolicyData>(() => api('/policy'), [tick])
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')
  const [importId, setImportId] = useState<number | null>(null)

  useEffect(() => {
    if (!importId) return
    const t = setInterval(async () => {
      const r = await api(`/policy/imports/${importId}`)
      if (r.status === 'done' || r.status === 'error') {
        clearInterval(t); setImportId(null); reload()
        if (r.status === 'done') toast(`${r.proposed} rule${r.proposed === 1 ? '' : 's'} proposed${r.dropped_uncited ? `, ${r.dropped_uncited} dropped for missing citation` : ''}${r.duplicates ? `, ${r.duplicates} duplicates skipped` : ''}`)
        else toast(r.error, 'err')
      }
    }, 900)
    return () => clearInterval(t)
  }, [importId, reload, toast])

  if (error) return <ErrorNote msg={error} />
  if (!data) return <p className="flex items-center gap-2"><Spinner /> Loading…</p>
  const current = data.versions.find(v => v.is_current)
  const proposed = data.rules.filter(r => r.status === 'proposed')
  const active = data.rules.filter(r => r.status === 'approved')

  async function act(fn: () => Promise<any>, ok?: string) {
    setBusy(true)
    try { await fn(); if (ok) toast(ok); reload() } catch (e: any) { toast(e.message, 'err') } finally { setBusy(false) }
  }

  return (
    <div className="space-y-8">
      <PageHead title="Brand policy" sub="Kora Naturals' guidelines, compiled into rules Plumb can check. Each rule quotes the sentence it comes from. Nothing is enforced until a brand manager approves it and publishes a new version." />

      <section className="grid gap-4 lg:grid-cols-[1fr_1.1fr]">
        <div className="sheet p-5">
          <h2 className="font-bold">Policy v{current?.number}, in force</h2>
          <p className="text-sm text-ink-2">{current?.note}</p>
          <p className="mt-1 text-sm text-mute">{current?.rule_count} rules. Published by {current?.published_by} on {current && new Date(current.published_at).toLocaleDateString()}.</p>
          <h3 className="mt-4 text-sm font-semibold">History</h3>
          <ul className="mt-1 space-y-1.5 text-sm">
            {data.versions.map(v => (
              <li key={v.number}><b>v{v.number}</b> <span className="text-ink-2">{v.note}</span>
                {v.number > 1 && <span className="block text-xs text-mute">{[v.diff.added.length && `added ${v.diff.added.join(', ')}`, v.diff.changed.length && `changed ${v.diff.changed.join(', ')}`, v.diff.removed.length && `removed ${v.diff.removed.join(', ')}`].filter(Boolean).join('; ') || 'no rule changes'}</span>}</li>
            ))}
          </ul>
          {data.can_edit && data.unpublished_changes.length > 0 && (
            <div className="mt-4 rounded-md bg-chalk-soft p-3">
              <p className="text-sm"><b>{data.unpublished_changes.length} change{data.unpublished_changes.length > 1 ? 's' : ''} ready to publish:</b> {data.unpublished_changes.join(', ')}</p>
              <div className="mt-2 flex gap-2"><label htmlFor="note" className="sr-only">Version note</label>
                <input id="note" className="field py-1.5 text-sm" placeholder={`What changed in v${(current?.number || 0) + 1}?`} value={note} onChange={e => setNote(e.target.value)} />
                <button className="btn btn-primary" disabled={busy} onClick={() => act(() => api('/policy/publish', { body: { note } }), `Published policy v${(current?.number || 0) + 1}. Open drafts will ask to re-check.`)}>Publish v{(current?.number || 0) + 1}</button></div>
            </div>
          )}
        </div>
        {data.can_edit ? <ImportBox demo={meta?.demo_files || []} onStarted={setImportId} running={!!importId} /> : (
          <div className="sheet p-5 text-ink-2"><h2 className="font-bold text-ink">Importing guidelines</h2><p className="mt-1 text-sm">Brand managers upload guideline documents here and Plumb proposes rules from them. Switch to Meera Iyer in the sidebar to try it.</p></div>
        )}
      </section>

      {proposed.length > 0 && (
        <section>
          <h2 className="mb-1 text-xl font-bold">Proposed rules ({proposed.length})</h2>
          <p className="mb-3 text-sm text-ink-2">Compiled from your document. Approve the ones that are right, then publish a new policy version.</p>
          <div className="space-y-2.5">{proposed.map(r => <RuleRow key={r.id} r={r} canEdit={data.can_edit} busy={busy}
            onDecide={(status, severity) => act(() => api(`/policy/rules/${r.id}`, { method: 'PATCH', body: { status, severity } }), status === 'approved' ? `Approved ${r.key}` : `Rejected ${r.key}`)} />)}</div>
        </section>
      )}

      <section>
        <h2 className="mb-3 text-xl font-bold">Active rules</h2>
        {Object.keys(TYPE_LABEL).map(t => {
          const rs = active.filter(r => r.type === t)
          if (!rs.length) return null
          return (
            <div key={t} className="mb-5">
              <h3 className="mb-2 font-semibold text-ink-2">{TYPE_LABEL[t]}</h3>
              <ul className="sheet divide-y divide-rule">{rs.map(r => (
                <li key={r.id} className="grid gap-x-4 gap-y-1 px-4 py-3 sm:grid-cols-[110px_1fr_auto]">
                  <span className="flex items-center gap-1.5"><Key>{r.key}</Key><span className="text-xs text-mute">{r.section}</span></span>
                  <span><span className="font-semibold">{r.title}</span><span className="block text-sm text-mute">“{r.source_quote}”</span></span>
                  <span><SevTag s={r.severity} small /></span>
                </li>))}</ul>
            </div>
          )
        })}
      </section>
    </div>
  )
}

function ImportBox({ demo, onStarted, running }: { demo: string[]; onStarted: (id: number) => void; running: boolean }) {
  const { toast } = useApp()
  const [file, setFile] = useState<File | null>(null)
  const [text, setText] = useState('')
  async function go() {
    const f = new FormData()
    if (file) f.append('file', file)
    else f.append('text', text)
    try { const r = await api('/policy/import', { form: f }); onStarted(r.id); toast('Compiling guidelines into rules') } catch (e: any) { toast(e.message, 'err') }
  }
  async function useDemo(name: string) {
    const blob = await (await fetch(`/api/demo/${name}`)).blob()
    setFile(new File([blob], name)); setText('')
  }
  return (
    <div className="sheet p-5">
      <h2 className="font-bold">Import a guideline document</h2>
      <p className="text-sm text-ink-2">PDF, Word, Markdown or pasted text. Plumb proposes typed rules. Any rule whose quote can't be found in your document is dropped: no citation, no rule.</p>
      <div className="mt-3 space-y-2">
        <input type="file" accept=".pdf,.docx,.md,.txt" aria-label="Guideline file" onChange={e => setFile(e.target.files?.[0] || null)} />
        {file && <p className="text-sm">Selected: <b>{file.name}</b></p>}
        {!file && <textarea className="field min-h-[90px] text-sm" placeholder="…or paste guideline text" value={text} onChange={e => setText(e.target.value)} aria-label="Guideline text" />}
        <div className="flex flex-wrap items-center gap-2">
          <button className="btn btn-primary" disabled={running || (!file && text.trim().length < 40)} onClick={go}>{running && <Spinner />}{running ? 'Compiling…' : 'Compile into rules'}</button>
          {demo.includes('kora-guidelines-2026-update.md') && <button className="btn btn-ghost btn-sm" onClick={() => useDemo('kora-guidelines-2026-update.md')}>Use the October 2026 update</button>}
          {demo.includes('kora-brand-guidelines.md') && <a className="text-sm text-chalk underline" href="/api/demo/kora-brand-guidelines.md" target="_blank">Read the full guideline</a>}
        </div>
      </div>
    </div>
  )
}

function RuleRow({ r, canEdit, busy, onDecide }: { r: Rule; canEdit: boolean; busy: boolean; onDecide: (status: string, severity?: string) => void }) {
  const [sev, setSev] = useState(r.severity)
  const p = r.params || {}
  const detail = r.type === 'term' ? `${p.mode === 'preferred' ? 'Replace' : 'Ban'} ${(p.patterns || []).map((x: string) => `“${x}”`).join(', ')}${p.replacement ? ` with “${p.replacement}”` : ''}`
    : r.type === 'voice' ? `Flags ${(p.flag_phrases || []).map((x: string) => `“${x}”`).join(', ')}`
    : r.type === 'format' ? `Requires one of ${(p.required_any || []).join(', ')}${p.channels?.length ? ` on ${p.channels.join(', ')}` : ''}`
    : r.type === 'claim' ? `Claim class: ${p.claim_class}` : `Metric: ${p.metric}`
  return (
    <div className="sheet grid gap-3 p-4 md:grid-cols-[1fr_auto]">
      <div>
        <p className="flex flex-wrap items-center gap-2"><Key>{r.key}</Key><span className="text-xs text-mute">{r.section} from import #{r.import_id ?? '—'}</span></p>
        <p className="mt-1 font-semibold">{r.title}</p>
        <p className="text-sm text-ink-2">{detail}</p>
        <p className="mt-1 text-sm text-mute">Source: “{r.source_quote}”</p>
      </div>
      {canEdit && (
        <div className="flex items-center gap-2 md:flex-col md:items-end">
          <label className="sr-only" htmlFor={`sev${r.id}`}>Severity</label>
          <select id={`sev${r.id}`} className="field w-auto py-1 text-sm" value={sev} onChange={e => setSev(e.target.value as any)}>
            {(r.type === 'voice' ? ['warn', 'suggest'] : ['block', 'warn', 'suggest']).map(s => <option key={s} value={s}>{s === 'block' ? 'Blocks' : s === 'warn' ? 'Warning' : 'Suggestion'}</option>)}
          </select>
          <div className="flex gap-2"><button className="btn btn-primary btn-sm" disabled={busy} onClick={() => onDecide('approved', sev)}>Approve</button>
            <button className="btn btn-danger btn-sm" disabled={busy} onClick={() => onDecide('rejected')}>Reject</button></div>
        </div>
      )}
    </div>
  )
}
