import { diffWords } from 'diff'
import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, AUTHOR_LABEL, CHANNEL_LABEL, CHECK_LABEL, ROLE_LABEL, STATUS_LABEL, ago, type CPRDetail, type Finding, type VersionFull } from '../api'
import Draft, { isOpen } from '../components/Draft'
import FindingCard from '../components/FindingCard'
import { Bob, Dialog, ErrorNote, Key, Spinner, StatusTag, useApp, useLoad } from '../ui'

const STEP_LABEL: Record<string, string> = {
  queued: 'Queued', intent: 'Reading the intent', rules: 'Terminology, voice, accessibility and channel rules', claims: 'Extracting claims',
  voice: 'Judging tone', verify: 'Verifying claims against the Facts Ledger', fixes: 'Re-checking every suggested fix', save: 'Saving', done: 'Done',
}
const SEV_ORDER: Record<string, number> = { block: 0, warn: 1, suggest: 2 }

export default function CPRPage() {
  const { key = '' } = useParams()
  const { me, toast, tick } = useApp()
  const { data: c, error, reload } = useLoad<CPRDetail>(() => api(`/cprs/${key}`), [key, tick])
  const [active, setActive] = useState<number | null>(null)
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [busy, setBusy] = useState(false)
  const [editing, setEditing] = useState(false)
  const [editText, setEditText] = useState('')
  const [dlg, setDlg] = useState<null | { kind: 'dispute' | 'override' | 'uphold' | 'changes'; f?: Finding }>(null)
  const [reason, setReason] = useState('')
  const [viewVersion, setViewVersion] = useState<VersionFull | null>(null)
  const [showDiff, setShowDiff] = useState(false)

  const running = c && ['pending', 'running'].includes(c.latest.run_status)
  const [progress, setProgress] = useState<string>('queued')
  useEffect(() => {
    if (!running) return
    const t = setInterval(async () => {
      try {
        const s = await api(`/cprs/${key}/status`)
        setProgress(s.progress?.step || 'queued')
        if (!['pending', 'running'].includes(s.run_status)) { clearInterval(t); reload() }
      } catch { /* keep polling */ }
    }, 800)
    return () => clearInterval(t)
  }, [running, key, reload])

  useEffect(() => { setSelected(new Set()); setViewVersion(null); setShowDiff(false) }, [c?.latest.number])

  const findings = useMemo(() => [...(c?.latest.findings || [])].sort((a, b) => a.start - b.start || SEV_ORDER[a.severity] - SEV_ORDER[b.severity]), [c])
  const numbered = useMemo(() => new Map(findings.map((f, i) => [f.id, i + 1])), [findings])
  const grouped = useMemo(() => [...findings].sort((a, b) => (isOpen(a) ? 0 : 1) - (isOpen(b) ? 0 : 1) || SEV_ORDER[a.severity] - SEV_ORDER[b.severity] || a.start - b.start), [findings])

  if (error) return <ErrorNote msg={error} />
  if (!c) return <p className="flex items-center gap-2 text-ink-2"><Spinner /> Loading…</p>

  const v = c.latest
  const p = c.permissions
  const openBlocks = findings.filter(f => f.severity === 'block' && (f.status === 'open' || f.status === 'upheld'))
  const verifiedOpen = findings.filter(f => f.status === 'open' && f.fix !== null && f.fix_verified)

  async function act(fn: () => Promise<any>, ok?: string) {
    setBusy(true)
    try { await fn(); if (ok) toast(ok); reload() } catch (e: any) { toast(e.message, 'err') } finally { setBusy(false) }
  }
  const accept = (items: { finding_id: number; replacement?: string }[]) =>
    act(() => api(`/cprs/${key}/fixes`, { body: { items } }), `Applied ${items.length} fix${items.length > 1 ? 'es' : ''}, re-checking v${v.number + 1}`)
  const pick = (id: number) => { setActive(id); document.getElementById(`f-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }) }

  async function confirmDialog() {
    if (!dlg) return
    const d = dlg
    setDlg(null)
    if (d.kind === 'dispute') await act(() => api(`/cprs/${key}/findings/${d.f!.id}/dispute`, { body: { reason } }), 'Dispute recorded. A reviewer will decide.')
    if (d.kind === 'override' || d.kind === 'uphold') await act(() => api(`/cprs/${key}/findings/${d.f!.id}/resolve`, { body: { action: d.kind, reason } }), d.kind === 'override' ? 'Finding overridden' : 'Finding upheld')
    if (d.kind === 'changes') await act(() => api(`/cprs/${key}/review`, { body: { action: 'request_changes', note: reason } }), 'Changes requested')
    setReason('')
  }

  async function openVersion(n: number) {
    if (n === v.number) { setViewVersion(null); return }
    try { setViewVersion(await api(`/cprs/${key}/versions/${n}`)) } catch (e: any) { toast(e.message, 'err') }
  }

  return (
    <div className="space-y-6">
      {/* header */}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="flex flex-wrap items-center gap-2 text-sm text-mute"><Link to="/" className="hover:text-ink">Home</Link><span aria-hidden>/</span><span className="key">{c.key}</span>
            {c.confidential && <span className="rounded bg-ink px-1.5 text-xs font-semibold text-white">Confidential</span>}</p>
          <h1 className="mt-1 text-[28px] font-bold leading-tight tracking-[-0.02em]">{c.title}</h1>
          <p className="mt-1 text-ink-2">{CHANNEL_LABEL[c.channel]} by {c.author} ({AUTHOR_LABEL[c.author_type]}). Version {v.number}, checked against policy v{v.policy_version ?? '…'}.</p>
        </div>
        <StatusTag status={c.status} label={STATUS_LABEL[c.status]} />
      </div>

      <GateTrack c={c} running={!!running} openBlocks={openBlocks.length} />

      {v.run_status === 'error' && <div className="flex items-center gap-3"><ErrorNote msg={`The check run failed: ${v.error}`} />
        <button className="btn btn-ghost" onClick={() => act(() => api(`/cprs/${key}/run`, { method: 'POST' }), 'Re-running checks')}>Run again</button></div>}

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)]">
        {/* left: draft */}
        <div className="space-y-5">
          <Intent c={c} canEdit={p.edit} onSaved={reload} />

          <section className="sheet p-5 md:p-7" aria-label="Draft">
            <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <h2 className="font-bold">{viewVersion ? `Version ${viewVersion.number} (read only)` : 'Draft'}</h2>
                {c.versions.length > 1 && (
                  <select aria-label="Version" className="field w-auto py-1 text-sm" value={viewVersion?.number ?? v.number} onChange={e => openVersion(Number(e.target.value))}>
                    {c.versions.map(x => <option key={x.number} value={x.number}>v{x.number} · {x.source === 'fix' ? 'fixes' : x.source}{x.number === v.number ? ' (latest)' : ''}</option>)}
                  </select>
                )}
                {v.number > 1 && !viewVersion && <button className="text-sm font-semibold text-chalk hover:underline" onClick={() => setShowDiff(s => !s)}>{showDiff ? 'Hide changes' : `Changes from v${v.number - 1}`}</button>}
              </div>
              {p.edit && !viewVersion && !running && (editing
                ? <div className="flex gap-2"><button className="btn btn-primary btn-sm" disabled={busy} onClick={() => act(async () => { await api(`/cprs/${key}/versions`, { body: { text: editText } }); setEditing(false) }, `Saved v${v.number + 1}, re-checking`)}>Save as v{v.number + 1}</button>
                  <button className="btn btn-ghost btn-sm" onClick={() => setEditing(false)}>Cancel</button></div>
                : <button className="btn btn-ghost btn-sm" onClick={() => { setEditText(v.text); setEditing(true) }}>Edit text</button>)}
            </div>
            {editing ? <textarea aria-label="Edit draft" className="field min-h-[260px] font-serif text-[17px] leading-relaxed" value={editText} onChange={e => setEditText(e.target.value)} />
              : viewVersion ? <Draft text={viewVersion.text} findings={viewVersion.findings} active={null} onPick={() => {}} />
              : showDiff ? <DiffView key={v.number} keyCpr={key} n={v.number} text={v.text} />
              : <div className={running ? 'opacity-60' : ''}><Draft text={v.text} findings={running ? [] : findings} active={active} onPick={pick} /></div>}
            {v.source !== 'paste' && v.filename && <p className="mt-4 text-xs text-mute">Extracted from {v.filename}</p>}
          </section>

          <Claims v={v} />
        </div>

        {/* right: checks + findings */}
        <div className="space-y-4">
          {running ? (
            <section className="sheet p-5" aria-live="polite">
              <h2 className="flex items-center gap-2 font-bold"><Spinner /> Checking v{v.number}</h2>
              <ol className="mt-3 space-y-1.5 text-sm">
                {['rules', 'claims', 'voice', 'verify', 'fixes'].map(s => {
                  const order = ['queued', 'intent', 'rules', 'claims', 'voice', 'verify', 'fixes', 'save', 'done']
                  const done = order.indexOf(progress) > order.indexOf(s)
                  const now = progress === s
                  return <li key={s} className={done ? 'text-pass' : now ? 'font-semibold text-ink' : 'text-mute'}>{done ? '✓ ' : now ? '… ' : '  '}{STEP_LABEL[s]}</li>
                })}
              </ol>
              <p className="mt-3 text-xs text-mute">The first run after a restart loads the local NLI model, which takes a few seconds.</p>
            </section>
          ) : v.run_status === 'done' && (
            <>
              <ChecksBar v={v} />
              {p.edit && verifiedOpen.length > 0 && (
                <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-ink p-3 text-white">
                  <p className="text-sm">{selected.size ? `${selected.size} selected` : `${verifiedOpen.length} verified fix${verifiedOpen.length > 1 ? 'es' : ''} ready`}</p>
                  <div className="flex gap-2">
                    {selected.size > 0 && <button className="btn btn-sm bg-white/10 text-white" onClick={() => setSelected(new Set())}>Clear</button>}
                    <button className="btn btn-sm bg-white text-ink" disabled={busy}
                      onClick={() => accept((selected.size ? verifiedOpen.filter(f => selected.has(f.id)) : verifiedOpen).map(f => ({ finding_id: f.id })))}>
                      {busy && <Spinner />}{selected.size ? `Apply ${selected.size} selected` : 'Apply all verified fixes'}</button>
                  </div>
                </div>
              )}
              <div className="space-y-2.5">
                {grouped.length === 0 && <p className="sheet p-5 text-center text-pass">Every check passes on this version.</p>}
                {grouped.map(f => (
                  <FindingCard key={f.id} f={f} n={numbered.get(f.id)!} active={active === f.id} onFocus={() => setActive(f.id)} busy={busy}
                    canFix={p.edit} canDispute={p.dispute} canResolve={p.resolve && me?.role !== 'creator'}
                    selected={selected.has(f.id)} onSelect={on => setSelected(s => { const n = new Set(s); if (on) n.add(f.id); else n.delete(f.id); return n })}
                    onAccept={rep => accept([{ finding_id: f.id, replacement: rep }])}
                    onDispute={() => { setReason(''); setDlg({ kind: 'dispute', f }) }}
                    onResolve={a => { setReason(''); setDlg({ kind: a, f }) }} />
                ))}
              </div>
            </>
          )}
        </div>
      </div>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)]">
        <ReviewPanel c={c} busy={busy} openBlocks={openBlocks.length} running={!!running}
          onSubmit={() => act(() => api(`/cprs/${key}/submit`, { method: 'POST' }), 'Submitted for review')}
          onApprove={() => act(() => api(`/cprs/${key}/review`, { body: { action: 'approve', note: '' } }), 'Approved')}
          onChanges={() => { setReason(''); setDlg({ kind: 'changes' }) }} onRerun={() => act(() => api(`/cprs/${key}/run`, { method: 'POST' }), 'Re-checking against the current policy')} />
        <Timeline c={c} onComment={body => act(() => api(`/cprs/${key}/comments`, { body: { body } }))} />
      </div>

      {c.receipt && <Receipt c={c} />}
      <AICalls c={c} />

      <Dialog open={!!dlg} onClose={() => setDlg(null)} title={dlg?.kind === 'dispute' ? 'Dispute this finding' : dlg?.kind === 'override' ? 'Override this finding' : dlg?.kind === 'uphold' ? 'Uphold this finding' : 'Request changes'}>
        <div className="space-y-3">
          {dlg?.f && <p className="text-sm text-ink-2"><b className="text-ink">{dlg.f.title}</b>: “{dlg.f.excerpt}” <Key>{dlg.f.rule_key}</Key></p>}
          <label className="lbl" htmlFor="reason">{dlg?.kind === 'dispute' ? 'Why is this wrong or acceptable here?' : dlg?.kind === 'changes' ? 'What should the creator change?' : 'Reason (saved in the audit receipt)'}</label>
          <textarea id="reason" className="field min-h-[90px]" value={reason} onChange={e => setReason(e.target.value)} autoFocus />
          <p className="text-xs text-mute">{dlg?.kind === 'dispute' ? 'Disputes go to a reviewer. Repeated disputes on the same rule are how Plumb will spot unclear guidelines (Round 3).' : 'Every decision is attributed to you and recorded.'}</p>
          <div className="flex justify-end gap-2"><button className="btn btn-ghost" onClick={() => setDlg(null)}>Cancel</button>
            <button className="btn btn-primary" disabled={reason.trim().length < (dlg?.kind === 'uphold' ? 0 : 5)} onClick={confirmDialog}>
              {dlg?.kind === 'dispute' ? 'Send dispute' : dlg?.kind === 'override' ? 'Override' : dlg?.kind === 'uphold' ? 'Uphold' : 'Request changes'}</button></div>
        </div>
      </Dialog>
    </div>
  )
}

// ---------------------------------------------------------------------------

function GateTrack({ c, running, openBlocks }: { c: CPRDetail; running: boolean; openBlocks: number }) {
  const stages = [
    { id: 'draft', label: 'Draft', note: `v${c.latest.number}` },
    { id: 'checked', label: 'Checks', note: running ? 'running' : openBlocks ? `${openBlocks} blocking` : 'clear' },
    { id: 'review', label: 'Review', note: c.required_roles.map(r => (c.approved_roles.includes(r) ? '✓ ' : '') + ROLE_LABEL[r]).join(', ') },
    { id: 'approved', label: 'Approved', note: c.receipt ? 'receipt issued' : '' },
  ]
  const at = c.status === 'approved' ? 3 : c.status === 'in_review' ? 2 : running || openBlocks ? 1 : 1
  const blocked = !running && openBlocks > 0 && c.status !== 'approved'
  return (
    <div className="sheet px-5 py-4" aria-label="Publishing gate">
      <div className="relative grid grid-cols-4">
        <div className="absolute left-[12.5%] right-[12.5%] top-[9px] h-px bg-ink/25" aria-hidden />
        <div className="absolute left-[12.5%] top-[9px] h-[2px] bg-chalk transition-all" style={{ width: `${(at / 3) * 75}%` }} aria-hidden />
        {stages.map((s, i) => (
          <div key={s.id} className="relative flex flex-col items-center text-center">
            <div className="flex h-[19px] items-start justify-center">
              {i === at ? <Bob size={14} className="bob-drop relative -top-1" />
                : <span className={`mt-[5px] h-[9px] w-[9px] rounded-full border-2 ${i < at ? 'border-chalk bg-chalk' : 'border-ink/30 bg-sheet'}`} />}
            </div>
            <span className={`mt-1.5 text-sm font-semibold ${i === at ? 'text-ink' : i < at ? 'text-ink-2' : 'text-mute'}`}>{s.label}</span>
            <span className={`text-xs ${i === 1 && blocked ? 'font-semibold text-block' : 'text-mute'}`}>{s.note}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

function ChecksBar({ v }: { v: VersionFull }) {
  const s = v.summary
  const icon: Record<string, string> = { pass: '✓', block: '✕', warn: '!', suggest: '·' }
  const cls: Record<string, string> = { pass: 'text-pass', block: 'text-block', warn: 'text-warn', suggest: 'text-suggest' }
  return (
    <section className="sheet p-4" aria-label="Checks">
      <ul className="grid grid-cols-2 gap-x-4 gap-y-1.5 sm:grid-cols-3 lg:grid-cols-2 xl:grid-cols-3">
        {Object.entries(s.checks || {}).map(([k, x]) => (
          <li key={k} className="flex items-center gap-2 text-sm"><span className={`w-4 text-center font-bold ${cls[x.status]}`} aria-hidden>{icon[x.status]}</span>
            <span>{CHECK_LABEL[k]}</span>{x.count > 0 && <span className="text-mute">{x.count}</span>}</li>
        ))}
      </ul>
      <p className="mt-3 border-t border-rule pt-2.5 text-xs leading-relaxed text-mute">
        Ran in {(v.run_ms / 1000).toFixed(1)} s against policy v{v.policy_version}. {s.llm_used ? 'AI judgment used for tone and claim extraction. ' : 'AI judgment off for this run: rules and local models only. '}
        {s.ml ? 'Claims verified with the local DeBERTa NLI model. ' : ''}{(s.dropped_uncited || 0) > 0 ? `${s.dropped_uncited} uncited finding(s) were discarded.` : 'Every finding cites a rule or fact.'}
      </p>
    </section>
  )
}

function Intent({ c, canEdit, onSaved }: { c: CPRDetail; canEdit: boolean; onSaved: () => void }) {
  const { toast } = useApp()
  const [edit, setEdit] = useState(false)
  const [f, setF] = useState({ goal: '', audience: '', cta: '', must_keep: '' })
  const i = c.intent || {}
  if (!i.goal && !edit) return null
  async function save() {
    try {
      await api(`/cprs/${c.key}/intent`, { method: 'PUT', body: { goal: f.goal, audience: f.audience, cta: f.cta, key_message: i.key_message || '', must_keep: f.must_keep.split(',').map(s => s.trim()).filter(Boolean) } })
      setEdit(false); toast('Intent confirmed. Fixes are re-verified against it.'); onSaved()
    } catch (e: any) { toast(e.message, 'err') }
  }
  return (
    <section className="rounded-lg border border-rule bg-concrete/50 p-4" aria-label="Intent">
      <div className="flex items-center justify-between gap-2">
        <h2 className="font-bold">What this draft is for</h2>
        <span className="text-xs text-mute">{i.confirmed ? 'Confirmed by the creator' : i.source === 'llm' ? 'Read by AI, please confirm' : 'Detected, please confirm'}</span>
      </div>
      {edit ? (
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          {(['goal', 'audience', 'cta'] as const).map(k => (
            <div key={k} className={k === 'cta' ? 'sm:col-span-2' : ''}><label className="lbl" htmlFor={`in-${k}`}>{k === 'cta' ? 'Call to action' : k[0].toUpperCase() + k.slice(1)}</label>
              <input id={`in-${k}`} className="field" value={f[k]} onChange={e => setF({ ...f, [k]: e.target.value })} /></div>
          ))}
          <div className="sm:col-span-2"><label className="lbl" htmlFor="in-keep">Must keep (comma-separated phrases fixes may not remove)</label>
            <input id="in-keep" className="field" value={f.must_keep} onChange={e => setF({ ...f, must_keep: e.target.value })} /></div>
          <div className="flex gap-2"><button className="btn btn-primary btn-sm" onClick={save}>Confirm intent</button><button className="btn btn-ghost btn-sm" onClick={() => setEdit(false)}>Cancel</button></div>
        </div>
      ) : (
        <dl className="mt-2 grid gap-x-6 gap-y-1 text-sm sm:grid-cols-[auto_1fr]">
          <dt className="text-mute">Goal</dt><dd>{i.goal || '—'}</dd>
          <dt className="text-mute">Audience</dt><dd>{i.audience || <span className="text-mute">not set</span>}</dd>
          <dt className="text-mute">Call to action</dt><dd>{i.cta || <span className="text-mute">none found</span>}</dd>
          <dt className="text-mute">Fixes must keep</dt><dd>{(i.must_keep || []).length ? i.must_keep!.join(', ') : <span className="text-mute">nothing pinned</span>}</dd>
        </dl>
      )}
      {canEdit && !edit && <button className="mt-2 text-sm font-semibold text-chalk hover:underline"
        onClick={() => { setF({ goal: i.goal || '', audience: i.audience || '', cta: i.cta || '', must_keep: (i.must_keep || []).join(', ') }); setEdit(true) }}>{i.confirmed ? 'Edit intent' : 'Review and confirm'}</button>}
    </section>
  )
}

function Claims({ v }: { v: VersionFull }) {
  if (!v.claims?.length || v.run_status !== 'done') return null
  const tone: Record<string, string> = { supported: 'text-pass', contradicted: 'text-block', expired: 'text-block', unsupported: 'text-warn', banned: 'text-block' }
  return (
    <section className="sheet p-5" aria-label="Claims">
      <h2 className="font-bold">Claims found in this draft</h2>
      <p className="text-sm text-mute">Each claim is matched against the Facts Ledger: numbers exactly, everything else with a local NLI model.</p>
      <ul className="mt-3 divide-y divide-rule">
        {v.claims.map((cl, i) => (
          <li key={i} className="grid grid-cols-[1fr_auto] gap-2 py-2 text-sm">
            <span className="font-serif text-[15px]">“{cl.text}”<span className="block font-sans text-xs text-mute">{cl.rationale}</span></span>
            <span className="text-right"><span className={`font-semibold ${tone[cl.verdict]}`}>{cl.verdict}</span>{cl.fact_key && <span className="ml-1.5"><Key>{cl.fact_key}</Key></span>}
              <span className="block text-xs text-mute">{cl.method === 'numeric' ? 'exact number match' : cl.method === 'nli' ? 'NLI model' : cl.method === 'llm' ? 'AI judge' : cl.method === 'rule' ? 'banned wording' : 'retrieval'}</span></span>
          </li>
        ))}
      </ul>
    </section>
  )
}

function DiffView({ keyCpr, n, text }: { keyCpr: string; n: number; text: string }) {
  const [prev, setPrev] = useState<string | null>(null)
  useEffect(() => { api(`/cprs/${keyCpr}/versions/${n - 1}`).then(x => setPrev(x.text)).catch(() => setPrev('')) }, [keyCpr, n])
  if (prev === null) return <Spinner />
  return (
    <div className="whitespace-pre-wrap font-serif text-[18px] leading-[1.75]">
      {diffWords(prev, text).map((p, i) => p.added ? <ins key={i} className="rounded bg-pass-soft text-pass no-underline">{p.value}</ins>
        : p.removed ? <del key={i} className="text-block">{p.value}</del> : <span key={i}>{p.value}</span>)}
    </div>
  )
}

function ReviewPanel({ c, busy, openBlocks, running, onSubmit, onApprove, onChanges, onRerun }: {
  c: CPRDetail; busy: boolean; openBlocks: number; running: boolean; onSubmit: () => void; onApprove: () => void; onChanges: () => void; onRerun: () => void
}) {
  const { meta } = useApp()
  const p = c.permissions
  const stale = c.latest.policy_version !== null && meta && c.latest.policy_version !== meta.policy_version && c.status !== 'approved'
  const disputed = c.latest.findings.filter(f => f.status === 'disputed' && f.severity === 'block').length
  return (
    <section className="sheet p-5" aria-label="Review">
      <h2 className="font-bold">Review and sign-off</h2>
      <ul className="mt-3 space-y-1.5 text-sm">
        {c.required_roles.map(r => (
          <li key={r} className="flex items-center gap-2">
            <span className={`flex h-5 w-5 items-center justify-center rounded-full text-xs font-bold ${c.approved_roles.includes(r) ? 'bg-pass text-white' : 'border border-rule text-mute'}`}>{c.approved_roles.includes(r) ? '✓' : ''}</span>
            {ROLE_LABEL[r]} {r === 'claims_reviewer' ? <span className="text-mute">(this draft makes claims)</span> : <span className="text-mute">(always required)</span>}
          </li>
        ))}
      </ul>
      {stale && <div className="mt-3 flex flex-wrap items-center gap-2 rounded-md bg-warn-soft p-2.5 text-sm text-warn">The brand policy changed to v{meta!.policy_version} after this check. <button className="btn btn-ghost btn-sm" disabled={busy || running} onClick={onRerun}>Re-check now</button></div>}
      <div className="mt-4 flex flex-wrap items-center gap-2">
        {p.submit && <button className="btn btn-ink" disabled={busy || running || openBlocks > 0} onClick={onSubmit}>Submit for review</button>}
        {p.submit && openBlocks > 0 && <span className="text-sm text-block">{openBlocks} blocking finding{openBlocks > 1 ? 's' : ''} left. Fix, edit or dispute {openBlocks > 1 ? 'them' : 'it'} first.</span>}
        {p.review && <>
          <button className="btn btn-primary" disabled={busy || running} onClick={onApprove}>Approve v{c.latest.number}</button>
          <button className="btn btn-ghost" disabled={busy} onClick={onChanges}>Request changes</button>
          {disputed > 0 && <span className="text-sm text-warn">Decide the {disputed} disputed blocker{disputed > 1 ? 's' : ''} first (override or uphold).</span>}
        </>}
        {c.status === 'in_review' && !p.review && <p className="text-sm text-ink-2">Waiting for {c.required_roles.filter(r => !c.approved_roles.includes(r)).map(r => ROLE_LABEL[r].toLowerCase()).join(' and ')}. Switch to Meera or Rohan in the sidebar to review.</p>}
        {c.status === 'approved' && <p className="text-sm font-semibold text-pass">Approved and locked. The receipt below records why it was allowed to ship.</p>}
      </div>
      {c.reviews.length > 0 && (
        <ul className="mt-4 space-y-1 border-t border-rule pt-3 text-sm">
          {c.reviews.map((r, i) => <li key={i}><b>{r.by}</b> {r.action === 'approve' ? 'approved' : 'requested changes on'} v{r.version}{r.note ? `: “${r.note}”` : ''} <span className="text-mute">{ago(r.at)}</span></li>)}
        </ul>
      )}
    </section>
  )
}

function Timeline({ c, onComment }: { c: CPRDetail; onComment: (b: string) => Promise<void> }) {
  const [body, setBody] = useState('')
  return (
    <section className="sheet p-5" aria-label="Activity">
      <h2 className="font-bold">Activity</h2>
      <ol className="mt-3 max-h-[320px] space-y-2 overflow-auto pr-1 text-sm">
        {c.timeline.map((t, i) => (
          <li key={i} className={t.kind === 'comment' ? 'rounded-md bg-concrete/60 p-2' : ''}>
            <b>{t.by}</b> <span className={t.kind === 'comment' ? '' : 'text-ink-2'}>{t.kind === 'comment' ? <><span className="text-mute">commented: </span>{t.body}</> : t.body}</span> <span className="text-xs text-mute">{ago(t.at)}</span>
          </li>
        ))}
      </ol>
      <form className="mt-3 flex gap-2" onSubmit={async e => { e.preventDefault(); if (!body.trim()) return; await onComment(body); setBody('') }}>
        <label htmlFor="cmt" className="sr-only">Comment</label>
        <input id="cmt" className="field" placeholder="Leave a comment for the team" value={body} onChange={e => setBody(e.target.value)} />
        <button className="btn btn-ghost" disabled={!body.trim()}>Post</button>
      </form>
    </section>
  )
}

function Receipt({ c }: { c: CPRDetail }) {
  const { toast } = useApp()
  const r = c.receipt
  const [check, setCheck] = useState<null | boolean>(null)
  async function verify() {
    try { const x = await api('/receipts/verify', { body: r }); setCheck(x.valid); toast(x.message, x.valid ? 'ok' : 'err') } catch (e: any) { toast(e.message, 'err') }
  }
  return (
    <section className="sheet overflow-hidden" aria-label="Audit receipt">
      <div className="flex flex-wrap items-center justify-between gap-3 bg-ink px-5 py-3 text-white">
        <h2 className="font-bold">Audit receipt <span className="key ml-1 text-white/70">{r.receipt_id}</span></h2>
        <div className="flex gap-2">
          <button className="btn btn-sm bg-white/10 text-white" onClick={verify}>{check === null ? 'Verify signature' : check ? '✓ Signature valid' : '✕ Invalid'}</button>
          <a className="btn btn-sm bg-white text-ink" href={`data:application/json,${encodeURIComponent(JSON.stringify(r, null, 2))}`} download={`${r.receipt_id}.json`}>Download JSON</a>
        </div>
      </div>
      <dl className="grid gap-x-8 gap-y-2 p-5 text-sm sm:grid-cols-2 lg:grid-cols-4">
        <div><dt className="text-mute">Approved version</dt><dd>v{r.approved_version.number} <span className="key text-mute">{r.approved_version.sha256.slice(0, 12)}…</span></dd></div>
        <div><dt className="text-mute">Policy</dt><dd>v{r.policy_version}</dd></div>
        <div><dt className="text-mute">Facts Ledger snapshot</dt><dd className="key">{r.ledger_sha256.slice(0, 12)}…</dd></div>
        <div><dt className="text-mute">Approved by</dt><dd>{r.approvals.map((a: any) => a.by).join(', ')}</dd></div>
        <div><dt className="text-mute">Claims</dt><dd>{r.claims.length ? r.claims.map((x: any) => `${x.verdict}${x.fact ? ' ' + x.fact : ''}`).join(', ') : 'none'}</dd></div>
        <div><dt className="text-mute">Human decisions</dt><dd>{r.decisions.length}</dd></div>
        <div><dt className="text-mute">External AI calls</dt><dd>{r.ai_disclosure.external_calls} ({r.ai_disclosure.redactions} values redacted)</dd></div>
        <div><dt className="text-mute">Signature</dt><dd>{r.signature_alg}</dd></div>
      </dl>
    </section>
  )
}

function AICalls({ c }: { c: CPRDetail }) {
  const [open, setOpen] = useState(false)
  return (
    <section className="rounded-lg border border-rule p-4" aria-label="AI use on this draft">
      <button className="flex w-full items-center justify-between text-left" onClick={() => setOpen(o => !o)} aria-expanded={open}>
        <span className="font-bold">AI use on this draft</span>
        <span className="text-sm text-mute">{c.ai_calls.length} logged call{c.ai_calls.length === 1 ? '' : 's'}, {open ? 'hide' : 'show'}</span>
      </button>
      {open && (
        <ul className="mt-3 divide-y divide-rule text-sm">
          {c.ai_calls.map(a => (
            <li key={a.id} className="grid gap-1 py-2 sm:grid-cols-[150px_110px_1fr]">
              <span className="font-semibold">{a.purpose.replace('_', ' ')}</span>
              <span className={a.status === 'ok' || a.status === 'cached' ? 'text-pass' : a.status === 'skipped' ? 'text-mute' : 'text-block'}>{a.status}{a.location === 'external' ? ', external' : ''}</span>
              <span className="text-ink-2">{a.model || 'no model'}{a.redactions ? `, ${a.redactions} redacted` : ''}{a.note ? `. ${a.note}` : ''}</span>
            </li>
          ))}
          {c.ai_calls.length === 0 && <li className="py-2 text-mute">No calls yet.</li>}
        </ul>
      )}
    </section>
  )
}
