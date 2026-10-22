import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, AUTHOR_LABEL, CHANNEL_LABEL } from '../api'
import { ErrorNote, PageHead, Spinner, useApp } from '../ui'

export default function NewDraft() {
  const { meta, me, perms } = useApp()
  const nav = useNavigate()
  const [mode, setMode] = useState<'paste' | 'upload'>('paste')
  const [title, setTitle] = useState('')
  const [channel, setChannel] = useState('instagram')
  const [authorType, setAuthorType] = useState(me?.author_type || 'human')
  const [text, setText] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [confidential, setConfidential] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  if (!perms['cpr.create']) return <PageHead title="New draft" sub="Your role reviews content rather than writing it. Switch to Asha or Bluefin Studio in the sidebar to create a draft." />

  async function go(e: React.FormEvent) {
    e.preventDefault()
    setErr('')
    setBusy(true)
    try {
      let r: { key: string }
      if (mode === 'upload') {
        if (!file) throw new Error('Choose a file first.')
        const f = new FormData()
        f.append('file', file); f.append('title', title); f.append('channel', channel); f.append('author_type', authorType); f.append('confidential', String(confidential))
        r = await api('/cprs/upload', { form: f })
      } else {
        r = await api('/cprs', { body: { title, channel, author_type: authorType, text, confidential } })
      }
      nav(`/c/${r.key}`)
    } catch (e: any) { setErr(e.message) } finally { setBusy(false) }
  }

  return (
    <div className="max-w-[860px]">
      <PageHead title="New draft" sub={`Paste or upload content before it's published. Plumb checks it against policy v${meta?.policy_version ?? ''} and the Facts Ledger, then you fix and submit it.`} />
      <form onSubmit={go} className="sheet space-y-5 p-5 md:p-6">
        <div className="grid gap-4 sm:grid-cols-[1fr_180px_180px]">
          <div><label className="lbl" htmlFor="t">Title</label><input id="t" className="field" value={title} onChange={e => setTitle(e.target.value)} placeholder="e.g. Diwali email — Rose Glow Serum" /></div>
          <div><label className="lbl" htmlFor="ch">Channel</label>
            <select id="ch" className="field" value={channel} onChange={e => setChannel(e.target.value)}>{meta?.channels.map(c => <option key={c} value={c}>{CHANNEL_LABEL[c]}</option>)}</select></div>
          <div><label className="lbl" htmlFor="at">Written by</label>
            <select id="at" className="field" value={authorType} onChange={e => setAuthorType(e.target.value)}>{meta?.author_types.map(a => <option key={a} value={a}>{AUTHOR_LABEL[a]}</option>)}</select></div>
        </div>

        <div>
          <div role="tablist" className="mb-2 inline-flex rounded-lg bg-concrete p-1">
            {(['paste', 'upload'] as const).map(m => (
              <button key={m} type="button" role="tab" aria-selected={mode === m} onClick={() => setMode(m)}
                className={`rounded-md px-3 py-1 text-sm font-semibold ${mode === m ? 'bg-sheet shadow-sm' : 'text-ink-2'}`}>{m === 'paste' ? 'Paste text' : 'Upload a document'}</button>
            ))}
          </div>
          {mode === 'paste' ? (
            <>
              <label className="sr-only" htmlFor="txt">Draft text</label>
              <textarea id="txt" className="field min-h-[220px] font-serif text-[17px] leading-relaxed" value={text} onChange={e => setText(e.target.value)} placeholder="Paste the caption, email, page copy or ad text here." />
              {meta && (
                <div className="mt-2 flex flex-wrap items-center gap-2 text-sm">
                  <span className="text-mute">Or load a sample:</span>
                  {meta.samples.map(s => (
                    <button key={s.id} type="button" className="btn btn-ghost btn-sm"
                      onClick={() => { setText(s.text); setTitle(s.title); setChannel(s.channel); setAuthorType(s.author_type) }}>{s.label}</button>
                  ))}
                </div>
              )}
            </>
          ) : (
            <div className="rounded-lg border border-dashed border-rule bg-concrete/40 p-5">
              <input type="file" accept=".pdf,.docx,.md,.txt" onChange={e => setFile(e.target.files?.[0] || null)} aria-label="Document" />
              <p className="mt-2 text-sm text-mute">PDF, Word (.docx), Markdown or plain text, up to 8 MB. Text is extracted on the server. Image checks arrive in Round 3.
                {meta?.demo_files.includes('kora-onepager.docx') && <> Try <a className="text-chalk underline" href="/api/demo/kora-onepager.docx">the sample one-pager</a>.</>}</p>
            </div>
          )}
        </div>

        <label className="flex items-start gap-3 rounded-lg bg-concrete/50 p-3">
          <input type="checkbox" className="mt-1" checked={confidential} onChange={e => setConfidential(e.target.checked)} />
          <span><b>Confidential draft.</b> <span className="text-ink-2">Nothing is sent to an external AI model. Rules and Plumb's local models still run, and the skip is recorded in the AI disclosure log.</span></span>
        </label>

        <ErrorNote msg={err} />
        <div className="flex items-center gap-3">
          <button className="btn btn-primary" disabled={busy || (mode === 'paste' ? !text.trim() : !file)}>{busy && <Spinner />} Run checks</button>
          <span className="text-sm text-mute">Creates a Content PR and runs every check.</span>
        </div>
      </form>
    </div>
  )
}
