export type Role = 'creator' | 'brand_manager' | 'claims_reviewer' | 'admin'
export type User = { id: string; name: string; role: Role; role_label: string; title: string; author_type: string; team: string }
export type Severity = 'block' | 'warn' | 'suggest'
export type Finding = {
  id: number; check: string; severity: Severity; status: string; rule_key: string | null; fact_key: string | null
  policy_version: number | null; citation: string; title: string; message: string; start: number; end: number; excerpt: string
  fix: string | null; fix_verified: boolean; fix_note: string; confidence: number; source: string; verdict: string | null
  extra: Record<string, any>; carried_from: number | null
  decisions: { action: string; by: string; role: string; reason: string; at: string }[]
}
export type Claim = { start: number; end: number; text: string; claim_class: string; hypothesis: string; verdict: string; fact_key: string | null
  replacement_fact: string | null; method: string; score: number; source: string; rationale: string; nli: Record<string, number> }
export type Summary = { checks?: Record<string, { status: string; count: number }>; counts?: Record<Severity, number>; claims?: Record<string, number>
  dropped_uncited?: number; llm_used?: boolean; ml?: boolean; ms?: number; policy_version?: number }
export type VersionBrief = { id: number; number: number; source: string; filename: string; created_by: string; created_at: string; run_status: string
  summary: Summary; policy_version: number | null; sha256: string; chars: number }
export type VersionFull = VersionBrief & { text: string; progress: { step?: string; steps?: string[] }; error: string; claims: Claim[]
  ledger_hash: string; ran_at: string | null; run_ms: number; findings: Finding[] }
export type CPRBrief = { key: string; title: string; channel: string; author_type: string; status: string; author: string; author_id: string
  confidential: boolean; versions: number; run_status: string; counts: Partial<Record<Severity, number>>; requires_claims_review: boolean
  updated_at: string; created_at: string }
export type AICall = { id: number; at: string; cpr_id: number | null; cpr_key?: string | null; purpose: string; provider: string; model: string
  location: string; status: string; redactions: number; prompt_chars: number; prompt_sha256: string; prompt_preview: string; latency_ms: number; note: string }
export type CPR = CPRBrief & {
  intent: { goal?: string; audience?: string; cta?: string; key_message?: string; must_keep?: string[]; source?: string; confirmed?: boolean }
  latest: VersionFull; versions_list?: never; versions: number
  reviews: { by: string; role: string; action: string; note: string; version: number; at: string }[]
  timeline: { kind: string; by: string; role: string; body: string; finding_id: number | null; at: string }[]
  ai_calls: AICall[]; required_roles: Role[]; approved_roles: Role[]; receipt: any
  permissions: { edit: boolean; submit: boolean; review: boolean; resolve: boolean; dispute: boolean }
}
// the detail endpoint returns versions as an array; keep a separate type
export type CPRDetail = Omit<CPR, 'versions'> & { versions: VersionBrief[] }
export type Rule = { id: number; key: string; type: string; title: string; severity: Severity; params: any; section: string; source_quote: string
  status: string; origin: string; import_id: number | null; created_at: string; decided_by: string | null }
export type Fact = { id: number; key: string; statement: string; approved_phrasing: string; claim_class: string; subject: string; value: number | null
  unit: string; owner: string; evidence: string; valid_from: string | null; valid_until: string | null; superseded_by: string | null
  verified_at: string | null; status: string; retired: boolean }
export type Meta = { llm: { enabled: boolean; provider: string; model: string; reason: string }; ml: { loaded: boolean; error: string }
  today: string; policy_version: number; users: User[]; samples: Sample[]; demo_files: string[]; channels: string[]; author_types: string[] }
export type Sample = { id: string; label: string; hint: string; title: string; channel: string; author_type: string; text: string }

let currentUser = (() => { try { return localStorage.getItem('plumb.user') || 'bluefin' } catch { return 'bluefin' } })()
export const getUserId = () => currentUser
export const setUserId = (id: string) => { currentUser = id; try { localStorage.setItem('plumb.user', id) } catch { /* private mode */ } }

export class ApiError extends Error { status: number; constructor(status: number, msg: string) { super(msg); this.status = status } }

export async function api<T = any>(path: string, opts: { method?: string; body?: any; form?: FormData } = {}): Promise<T> {
  const headers: Record<string, string> = { 'X-Plumb-User': currentUser }
  let body: BodyInit | undefined
  if (opts.form) body = opts.form
  else if (opts.body !== undefined) { headers['Content-Type'] = 'application/json'; body = JSON.stringify(opts.body) }
  const r = await fetch(`/api${path}`, { method: opts.method || (body ? 'POST' : 'GET'), headers, body })
  if (!r.ok) {
    let msg = `Request failed (${r.status})`
    try { const j = await r.json(); if (j.detail) msg = typeof j.detail === 'string' ? j.detail : j.detail.map((d: any) => d.msg).join('; ') } catch { /* not json */ }
    throw new ApiError(r.status, msg)
  }
  return r.json()
}

export const ROLE_LABEL: Record<Role, string> = { creator: 'Creator', brand_manager: 'Brand manager', claims_reviewer: 'Claims reviewer', admin: 'Admin' }
export const CHECK_LABEL: Record<string, string> = { terms: 'Terminology', voice: 'Voice', claims: 'Claims & facts', a11y: 'Accessibility', format: 'Channel rules' }
export const STATUS_LABEL: Record<string, string> = { draft: 'Draft', in_review: 'In review', changes_requested: 'Changes requested', approved: 'Approved' }
export const CHANNEL_LABEL: Record<string, string> = { instagram: 'Instagram', linkedin: 'LinkedIn', x: 'X', email: 'Email', web: 'Web page', blog: 'Blog', sms: 'SMS', print: 'Print', other: 'Other' }
export const AUTHOR_LABEL: Record<string, string> = { human: 'In-house', agency: 'Agency', ai: 'AI-generated', influencer: 'Influencer' }
export const ago = (iso: string) => {
  const s = (Date.now() - new Date(iso).getTime()) / 1000
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)} min ago`
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`
  return new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
}
