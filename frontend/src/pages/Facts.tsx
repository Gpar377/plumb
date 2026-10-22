import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type CPRBrief, type Fact } from '../api'
import { Dialog, ErrorNote, Key, PageHead, Spinner, useApp, useLoad } from '../ui'

const STATUS_CLS: Record<string, string> = { active: 'bg-pass-soft text-pass', expired: 'bg-block-soft text-block', scheduled: 'bg-chalk-soft text-chalk', retired: 'bg-concrete-2 text-ink-2' }

export default function Facts() {
  const { tick, toast } = useApp()
  const { data, error, reload } = useLoad<{ today: string; can_edit: boolean; facts: Fact[] }>(() => api('/facts'), [tick])
  const [adding, setAdding] = useState(false)
  const [impact, setImpact] = useState<null | { fact: Fact; open: CPRBrief[]; approved: CPRBrief[] }>(null)
  const [busy, setBusy] = useState(false)

  if (error) return <ErrorNote msg={error} />
  if (!data) return <p className="flex items-center gap-2"><Spinner /> Loading…</p>

  async function act(fn: () => Promise<any>, ok?: string) {
    setBusy(true)
    try { const r = await fn(); if (ok) toast(ok); reload(); return r } catch (e: any) { toast(e.message, 'err') } finally { setBusy(false) }
  }
  async function showImpact(f: Fact) {
    const r = await api(`/facts/${f.key}/impact`)
    setImpact({ fact: f, ...r })
  }

  return (
    <div className="space-y-6">
      <PageHead title="Facts Ledger" sub={`Every claim in a draft is checked against these approved facts. Each one has an owner, evidence and a validity window, so outdated information is caught automatically. Today is ${new Date(data.today).toLocaleDateString(undefined, { day: 'numeric', month: 'long', year: 'numeric' })}.`}
        right={data.can_edit ? <button className="btn btn-primary" onClick={() => setAdding(true)}>Add a fact</button> : <span className="text-sm text-mute">Switch to Rohan (claims) to edit.</span>} />
      <div className="sheet overflow-x-auto">
        <table className="w-full min-w-[820px] text-sm">
          <thead><tr className="border-b border-ink text-left text-xs text-mute">
            <th className="px-4 py-2 font-semibold">Fact</th><th className="px-2 py-2 font-semibold">Statement</th><th className="px-2 py-2 font-semibold">Owner and evidence</th>
            <th className="px-2 py-2 font-semibold">Valid</th><th className="px-4 py-2 font-semibold"><span className="sr-only">Actions</span></th></tr></thead>
          <tbody className="divide-y divide-rule">
            {data.facts.map(f => (
              <tr key={f.key} className="align-top">
                <td className="px-4 py-3"><Key>{f.key}</Key><span className={`mt-1 block w-fit rounded px-1.5 text-xs font-semibold ${STATUS_CLS[f.status]}`}>{f.status}</span></td>
                <td className="max-w-[380px] px-2 py-3"><span className="font-serif text-[15px]">{f.statement}</span>
                  {f.approved_phrasing && <span className="block text-xs text-mute">Approved wording: “{f.approved_phrasing}”</span>}
                  {f.superseded_by && <span className="block text-xs text-mute">Superseded by <Key>{f.superseded_by}</Key></span>}</td>
                <td className="px-2 py-3 text-ink-2">{f.owner}<span className="block text-xs text-mute">{f.evidence}</span></td>
                <td className="whitespace-nowrap px-2 py-3 text-ink-2">{f.valid_from || '—'}<span className="block text-xs">{f.valid_until ? `until ${f.valid_until}` : 'no end date'}</span></td>
                <td className="px-4 py-3 text-right">
                  <div className="flex flex-col items-end gap-1.5">
                    <button className="text-sm font-semibold text-chalk hover:underline" onClick={() => showImpact(f)}>Where it's used</button>
                    {data.can_edit && f.status === 'active' && <button className="text-sm font-semibold text-block hover:underline" disabled={busy}
                      onClick={async () => { const y = new Date(data.today); y.setDate(y.getDate() - 1)
                        await act(() => api(`/facts/${f.key}`, { method: 'PATCH', body: { valid_until: y.toISOString().slice(0, 10) } }), `${f.key} expired. Drafts that use it will flag it on their next check.`); showImpact({ ...f, status: 'expired' }) }}>Expire now</button>}
                    {data.can_edit && f.status === 'expired' && !f.superseded_by && <button className="text-sm font-semibold text-ink-2 hover:underline" disabled={busy}
                      onClick={() => act(() => api(`/facts/${f.key}`, { method: 'PATCH', body: { clear_valid_until: true } }), `${f.key} is active again`)}>Reactivate</button>}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <Dialog open={!!impact} onClose={() => setImpact(null)} title={impact ? `Where ${impact.fact.key} is used` : ''}>
        {impact && (
          <div className="space-y-3 text-sm">
            <p className="font-serif text-[15px]">{impact.fact.statement}</p>
            <h3 className="font-semibold">Open drafts ({impact.open.length})</h3>
            <ul className="space-y-1">{impact.open.map(c => <li key={c.key}><Link className="text-chalk underline" to={`/c/${c.key}`}>{c.key}</Link> {c.title}</li>)}{!impact.open.length && <li className="text-mute">None</li>}</ul>
            <h3 className="font-semibold">Published content relying on it ({impact.approved.length})</h3>
            <ul className="space-y-1">{impact.approved.map(c => <li key={c.key}><Link className="text-chalk underline" to={`/c/${c.key}`}>{c.key}</Link> {c.title}</li>)}{!impact.approved.length && <li className="text-mute">None</li>}</ul>
            {data.can_edit && impact.open.length > 0 && <button className="btn btn-primary" disabled={busy} onClick={async () => { const r = await act(() => api(`/facts/${impact.fact.key}/recheck`, { method: 'POST' })); if (r) toast(`Re-checking ${r.rechecked} draft(s)`); setImpact(null) }}>Re-check open drafts now</button>}
          </div>
        )}
      </Dialog>

      <Dialog open={adding} onClose={() => setAdding(false)} title="Add an approved fact">
        <AddFact onDone={() => { setAdding(false); reload() }} />
      </Dialog>
    </div>
  )
}

function AddFact({ onDone }: { onDone: () => void }) {
  const { toast } = useApp()
  const [f, setF] = useState({ statement: '', approved_phrasing: '', subject: 'Aloe Calm Gel', claim_class: 'general', value: '', unit: '', owner: '', evidence: '', valid_until: '' })
  const [err, setErr] = useState('')
  const set = (k: keyof typeof f) => (e: any) => setF({ ...f, [k]: e.target.value })
  async function save() {
    setErr('')
    try {
      await api('/facts', { body: { ...f, value: f.value ? Number(f.value) : null, valid_until: f.valid_until || null, valid_from: null } })
      toast('Fact added to the ledger'); onDone()
    } catch (e: any) { setErr(e.message) }
  }
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <div className="sm:col-span-2"><label className="lbl" htmlFor="st">Fact, as a full sentence</label><input id="st" className="field" value={f.statement} onChange={set('statement')} placeholder="Aloe Calm Gel is suitable for sensitive skin." /></div>
      <div className="sm:col-span-2"><label className="lbl" htmlFor="ap">Approved wording (used in fixes)</label><input id="ap" className="field" value={f.approved_phrasing} onChange={set('approved_phrasing')} /></div>
      <div><label className="lbl" htmlFor="sb">Subject</label><input id="sb" className="field" value={f.subject} onChange={set('subject')} /></div>
      <div><label className="lbl" htmlFor="cc">Kind</label><select id="cc" className="field" value={f.claim_class} onChange={set('claim_class')}>
        {['general', 'environmental', 'health', 'superlative', 'price', 'statistic'].map(x => <option key={x}>{x}</option>)}</select></div>
      <div><label className="lbl" htmlFor="vv">Number (optional)</label><input id="vv" className="field" inputMode="decimal" value={f.value} onChange={set('value')} /></div>
      <div><label className="lbl" htmlFor="un">Unit (INR, %, hours…)</label><input id="un" className="field" value={f.unit} onChange={set('unit')} /></div>
      <div><label className="lbl" htmlFor="ow">Owner</label><input id="ow" className="field" value={f.owner} onChange={set('owner')} placeholder="R&D" /></div>
      <div><label className="lbl" htmlFor="vu">Valid until (optional)</label><input id="vu" type="date" className="field" value={f.valid_until} onChange={set('valid_until')} /></div>
      <div className="sm:col-span-2"><label className="lbl" htmlFor="ev">Evidence</label><input id="ev" className="field" value={f.evidence} onChange={set('evidence')} placeholder="Study report, certificate or link" /></div>
      <div className="sm:col-span-2"><ErrorNote msg={err} /></div>
      <div className="sm:col-span-2 flex justify-end"><button className="btn btn-primary" onClick={save}>Add to ledger</button></div>
    </div>
  )
}
