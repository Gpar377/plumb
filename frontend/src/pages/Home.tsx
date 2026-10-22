import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, setUserId, type CPRBrief, type Sample } from '../api'
import CPRList from '../components/CPRList'
import { ErrorNote, Spinner, useApp, useLoad } from '../ui'

type HomeData = { counts: Record<string, number>; awaiting_me: CPRBrief[]; recent: CPRBrief[] }

export default function Home() {
  const { me, meta, tick } = useApp()
  const { data, error } = useLoad<HomeData>(() => api('/home'), [tick, me?.id])
  const first = me?.name.split(' ')[0]
  return (
    <div className="space-y-10">
      <section className="grid gap-8 lg:grid-cols-[1.15fr_1fr]">
        <div>
          <h1 className="max-w-[18ch] text-[40px] font-bold leading-[1.05] tracking-[-0.03em] md:text-[52px]">
            Nothing ships until it's plumb.
          </h1>
          <p className="mt-4 max-w-[58ch] text-[17px] text-ink-2">
            Every draft for Kora Naturals is checked like code: against the brand's compiled rules and its Facts Ledger,
            with fixes that keep the writer's message, and a human sign-off before anything goes out.
          </p>
          {me && <p className="mt-5 text-ink-2">You're viewing as <b className="text-ink">{me.name}</b>, {me.role_label.toLowerCase()}. {roleHint(me.role)}</p>}
        </div>
        <TryIt samples={meta?.samples || []} />
      </section>

      <ErrorNote msg={error} />
      {!data && !error && <p className="flex items-center gap-2 text-ink-2"><Spinner /> Loading…</p>}
      {data && (
        <>
          {(me?.role === 'brand_manager' || me?.role === 'claims_reviewer') && (
            <section>
              <h2 className="mb-3 text-xl font-bold">Waiting for your review{data.awaiting_me.length ? ` (${data.awaiting_me.length})` : ''}</h2>
              <CPRList items={data.awaiting_me} empty="Nothing is waiting for you. Submitted drafts that need your sign-off will appear here." />
            </section>
          )}
          <section>
            <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
              <h2 className="text-xl font-bold">{me?.role === 'creator' ? `Your drafts, ${first}` : 'Recent content'}</h2>
              <p className="text-sm text-mute">{data.counts.drafts} in progress, {data.counts.in_review} in review, {data.counts.approved} approved</p>
            </div>
            <CPRList items={data.recent} empty="No drafts yet. Start with a sample on the right, or write a new draft." />
          </section>
        </>
      )}
    </div>
  )
}

function roleHint(role: string) {
  return {
    creator: 'Open a draft, fix what Plumb flags, then submit it for review.',
    brand_manager: 'You approve content, decide disputes on brand rules, and own the policy.',
    claims_reviewer: 'Drafts with claims come to you. You own the Facts Ledger.',
    admin: 'You can see every AI call in the disclosure log and manage confidential terms.',
  }[role]
}

function TryIt({ samples }: { samples: Sample[] }) {
  const { switchUser, toast } = useApp()
  const nav = useNavigate()
  const [busy, setBusy] = useState('')
  async function start(s: Sample) {
    setBusy(s.id)
    const author = s.author_type === 'human' ? 'asha' : 'bluefin'
    setUserId(author)
    try {
      const r = await api<{ key: string }>('/cprs', { body: { title: s.title, channel: s.channel, author_type: s.author_type, text: s.text } })
      switchUser(author)
      nav(`/c/${r.key}`)
    } catch (e: any) { toast(e.message, 'err') } finally { setBusy('') }
  }
  return (
    <div className="sheet p-5">
      <h2 className="text-lg font-bold">Try it in two minutes</h2>
      <ol className="mt-3 space-y-2.5 text-[15px] text-ink-2">
        <li className="flex gap-3"><Step n={1} /> Open a sample draft. Plumb checks it in a few seconds.</li>
        <li className="flex gap-3"><Step n={2} /> Click a highlighted phrase to see the rule or fact behind it. Accept the fixes you agree with, then submit.</li>
        <li className="flex gap-3"><Step n={3} /> Switch to Meera (brand) and Rohan (claims) in the sidebar to approve. You get a signed receipt.</li>
      </ol>
      <div className="mt-4 grid gap-2 sm:grid-cols-2">
        {samples.map(s => (
          <button key={s.id} onClick={() => start(s)} disabled={!!busy}
            className="flex flex-col items-start rounded-lg border border-rule bg-concrete/40 px-3 py-2.5 text-left hover:border-chalk hover:bg-chalk-soft/50">
            <span className="flex items-center gap-2 font-semibold">{busy === s.id && <Spinner />}{s.label}</span>
            <span className="text-xs text-mute">{s.hint}</span>
          </button>
        ))}
      </div>
    </div>
  )
}

function Step({ n }: { n: number }) {
  return <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-ink text-[11px] font-bold text-white">{n}</span>
}
