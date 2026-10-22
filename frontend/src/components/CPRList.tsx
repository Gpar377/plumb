import { Link } from 'react-router-dom'
import { AUTHOR_LABEL, CHANNEL_LABEL, STATUS_LABEL, ago, type CPRBrief } from '../api'
import { StatusTag } from '../ui'

export default function CPRList({ items, empty }: { items: CPRBrief[]; empty: string }) {
  if (!items.length) return <p className="rounded-lg border border-dashed border-rule px-4 py-6 text-center text-ink-2">{empty}</p>
  return (
    <ul className="sheet divide-y divide-rule overflow-hidden">
      {items.map(c => (
        <li key={c.key}>
          <Link to={`/c/${c.key}`} className="grid grid-cols-[1fr_auto] items-center gap-x-4 gap-y-1 px-4 py-3 hover:bg-concrete/50 sm:grid-cols-[90px_1fr_auto_auto]">
            <span className="key text-mute">{c.key}</span>
            <span className="min-w-0">
              <span className="block truncate font-semibold">{c.title}</span>
              <span className="block truncate text-sm text-mute">{CHANNEL_LABEL[c.channel]} by {c.author} ({AUTHOR_LABEL[c.author_type]}), v{c.versions}, {ago(c.updated_at)}</span>
            </span>
            <Counts c={c} />
            <StatusTag status={c.status} label={STATUS_LABEL[c.status]} />
          </Link>
        </li>
      ))}
    </ul>
  )
}

function Counts({ c }: { c: CPRBrief }) {
  if (c.run_status !== 'done') return <span className="text-sm text-mute">Checking…</span>
  const b = c.counts.block || 0, w = c.counts.warn || 0
  if (!b && !w) return <span className="text-sm font-medium text-pass">All checks pass</span>
  return (
    <span className="flex gap-2 text-sm font-medium">
      {b > 0 && <span className="text-block">{b} blocking</span>}
      {w > 0 && <span className="text-warn">{w} warning{w > 1 ? 's' : ''}</span>}
    </span>
  )
}
