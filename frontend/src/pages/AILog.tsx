import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api, ago, type AICall } from '../api'
import { ErrorNote, PageHead, Spinner, useApp, useLoad } from '../ui'

export default function AILog() {
  const { tick, perms, toast } = useApp()
  const { data, error } = useLoad<{ llm: any; ml: any; calls: AICall[]; totals: Record<string, number> }>(() => perms['ailog.view_all'] ? api('/ailog') : Promise.resolve(null as any), [tick, perms['ailog.view_all']])
  const [open, setOpen] = useState<number | null>(null)
  const [rec, setRec] = useState('')
  const [verdict, setVerdict] = useState('')

  async function verify() {
    try { const r = await api('/receipts/verify', { body: JSON.parse(rec) }); setVerdict(r.message) } catch (e: any) { setVerdict(e instanceof SyntaxError ? 'That is not valid JSON.' : e.message); toast('Could not verify', 'err') }
  }

  return (
    <div className="space-y-8">
      <PageHead title="AI disclosure" sub="Every model call Plumb makes is logged here: what it was for, which provider, how many values were redacted first, and whether it was skipped. Confidential drafts never reach an external model." />
      <section className="grid gap-4 md:grid-cols-3">
        <Card title="External model">{data?.llm ? (data.llm.enabled ? `Google Gemini (${data.llm.model}), through Plumb's gateway with redaction` : `Off: ${data.llm.reason}`) : '—'}</Card>
        <Card title="Local models">MiniLM sentence embeddings for retrieval and DeBERTa-v3 NLI for claim entailment. Both run on Plumb's server; drafts never leave it for these.</Card>
        <Card title="What AI never does">Approve content, change the policy, or apply a fix without a person choosing to. Tone findings can warn but never block.</Card>
      </section>

      {!perms['ailog.view_all'] ? <p className="sheet p-5 text-ink-2">The workspace-wide log is visible to admins and brand managers. Each draft shows its own AI calls at the bottom of its page.</p> : (
        <section>
          <h2 className="mb-3 text-xl font-bold">Call log</h2>
          <ErrorNote msg={error} />
          {!data ? <Spinner /> : (
            <>
              <p className="mb-2 text-sm text-mute">{Object.entries(data.totals).filter(([, n]) => n).map(([k, n]) => `${n} ${k}`).join(', ') || 'No calls yet'}</p>
              <ul className="sheet divide-y divide-rule text-sm">
                {data.calls.map(c => (
                  <li key={c.id}>
                    <button className="grid w-full gap-1 px-4 py-2.5 text-left hover:bg-concrete/50 sm:grid-cols-[120px_130px_110px_1fr]" onClick={() => setOpen(open === c.id ? null : c.id)} aria-expanded={open === c.id}>
                      <span className="text-mute">{ago(c.at)}</span>
                      <span className="font-semibold">{c.purpose.replace('_', ' ')}</span>
                      <span className={c.status === 'ok' || c.status === 'cached' ? 'text-pass' : c.status === 'skipped' ? 'text-mute' : 'text-block'}>{c.status}</span>
                      <span className="truncate text-ink-2">{c.cpr_key ? c.cpr_key + ' · ' : ''}{c.model || 'no model'}{c.redactions ? `, ${c.redactions} redacted` : ''}</span>
                    </button>
                    {open === c.id && (
                      <div className="space-y-1 bg-concrete/40 px-4 py-3 text-xs">
                        {c.cpr_key && <p><Link className="text-chalk underline" to={`/c/${c.cpr_key}`}>Open {c.cpr_key}</Link></p>}
                        <p><b>Location:</b> {c.location}. <b>Latency:</b> {c.latency_ms} ms. <b>Prompt:</b> {c.prompt_chars} characters, sha256 {c.prompt_sha256.slice(0, 16)}…</p>
                        {c.note && <p>{c.note}</p>}
                        {c.prompt_preview && <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded border border-rule bg-sheet p-2">{c.prompt_preview}</pre>}
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            </>
          )}
        </section>
      )}

      <section className="sheet p-5">
        <h2 className="font-bold">Verify an audit receipt</h2>
        <p className="text-sm text-ink-2">Paste a receipt JSON downloaded from an approved draft. Plumb checks its HMAC signature, so any edit to the receipt shows up as invalid.</p>
        <label htmlFor="rcpt" className="sr-only">Receipt JSON</label>
        <textarea id="rcpt" className="field mt-2 min-h-[100px] font-mono text-xs" value={rec} onChange={e => setRec(e.target.value)} placeholder='{"receipt_id": "RCPT-…", …}' />
        <div className="mt-2 flex items-center gap-3"><button className="btn btn-ghost" disabled={!rec.trim()} onClick={verify}>Verify</button><span className="text-sm">{verdict}</span></div>
      </section>
    </div>
  )
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return <div className="sheet p-4"><h3 className="font-semibold">{title}</h3><p className="mt-1 text-sm text-ink-2">{children}</p></div>
}
