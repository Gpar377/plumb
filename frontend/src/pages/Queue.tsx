import { api, type CPRBrief } from '../api'
import CPRList from '../components/CPRList'
import { ErrorNote, PageHead, useApp, useLoad } from '../ui'

export default function Queue() {
  const { tick, me } = useApp()
  const home = useLoad<{ awaiting_me: CPRBrief[] }>(() => api('/home'), [tick])
  const all = useLoad<CPRBrief[]>(() => api('/cprs?status=in_review,changes_requested'), [tick])
  return (
    <div className="space-y-8">
      <PageHead title="Review queue" sub={me?.role === 'claims_reviewer' ? 'Drafts that make claims are routed to you. Check them against the Facts Ledger, decide disputes, then approve or request changes.' : 'Everything submitted needs a brand sign-off. Drafts with claims also need the claims reviewer.'} />
      <ErrorNote msg={home.error || all.error} />
      <section><h2 className="mb-3 text-xl font-bold">Needs your sign-off</h2>
        {home.data && <CPRList items={home.data.awaiting_me} empty="You're all caught up." />}</section>
      <section><h2 className="mb-3 text-xl font-bold">Everything in review</h2>
        {all.data && <CPRList items={all.data} empty="Nothing is in review right now." />}</section>
    </div>
  )
}
