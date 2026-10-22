import { useCallback, useEffect, useState } from 'react'
import { BrowserRouter, NavLink, Route, Routes, useNavigate } from 'react-router-dom'
import { api, getUserId, setUserId, type Meta, type User } from './api'
import { Ctx, Toasts } from './ui'
import Home from './pages/Home'
import NewDraft from './pages/NewDraft'
import CPRPage from './pages/CPRPage'
import Queue from './pages/Queue'
import Policy from './pages/Policy'
import Facts from './pages/Facts'
import AILog from './pages/AILog'
import About from './pages/About'

export default function App() {
  const [meta, setMeta] = useState<Meta | null>(null)
  const [me, setMe] = useState<User | null>(null)
  const [perms, setPerms] = useState<Record<string, boolean>>({})
  const [uid, setUid] = useState(getUserId())
  const [tick, setTick] = useState(0)
  const [toasts, setToasts] = useState<{ id: number; msg: string; kind: 'ok' | 'err' }[]>([])
  const [bootErr, setBootErr] = useState('')

  const toast = useCallback((msg: string, kind: 'ok' | 'err' = 'ok') => {
    const id = Date.now() + Math.random()
    setToasts(t => [...t, { id, msg, kind }])
    setTimeout(() => setToasts(t => t.filter(x => x.id !== id)), kind === 'err' ? 6000 : 3500)
  }, [])

  useEffect(() => { api<Meta>('/meta').then(setMeta).catch(e => setBootErr(e.message)) }, [])
  useEffect(() => {
    api('/me').then(r => { setMe(r); setPerms(r.permissions) }).catch(() => { setUserId('bluefin'); setUid('bluefin') })
  }, [uid])

  const switchUser = useCallback((id: string) => { setUserId(id); setUid(id); setTick(t => t + 1) }, [])
  const bump = useCallback(() => setTick(t => t + 1), [])

  if (bootErr) return <div className="p-8"><p className="font-semibold">Plumb is starting up.</p><p className="text-ink-2">{bootErr}. Refresh in a few seconds.</p></div>

  return (
    <Ctx.Provider value={{ meta, me, perms, switchUser, toast, tick, bump }}>
      <BrowserRouter>
        <div className="flex min-h-full flex-col md:flex-row">
          <Sidebar />
          <main className="min-w-0 flex-1 px-4 py-6 md:px-10 md:py-9">
            <div className="mx-auto max-w-[1240px]" key={uid}>
              <Routes>
                <Route path="/" element={<Home />} />
                <Route path="/new" element={<NewDraft />} />
                <Route path="/c/:key" element={<CPRPage />} />
                <Route path="/queue" element={<Queue />} />
                <Route path="/policy" element={<Policy />} />
                <Route path="/facts" element={<Facts />} />
                <Route path="/ai-log" element={<AILog />} />
                <Route path="/about" element={<About />} />
                <Route path="*" element={<p>Page not found.</p>} />
              </Routes>
            </div>
          </main>
        </div>
      </BrowserRouter>
      <Toasts items={toasts} />
    </Ctx.Provider>
  )
}

function Sidebar() {
  const { meta, me, perms, switchUser } = useAppSafe()
  const nav = useNavigate()
  const link = ({ isActive }: { isActive: boolean }) =>
    `block rounded-md px-3 py-1.5 text-[15px] font-medium ${isActive ? 'bg-ink text-white' : 'text-ink-2 hover:bg-concrete-2 hover:text-ink'}`
  return (
    <aside className="border-b border-rule bg-concrete md:sticky md:top-0 md:h-screen md:w-[248px] md:shrink-0 md:border-b-0 md:border-r">
      <div className="flex h-full flex-col gap-5 px-4 py-4 md:py-6">
        <button onClick={() => nav('/')} className="flex items-center gap-2.5 text-left" aria-label="Plumb home">
          <svg width="30" height="30" viewBox="0 0 32 32" aria-hidden><rect width="32" height="32" rx="7" fill="#1D2127" /><line x1="16" y1="4" x2="16" y2="16" stroke="#E9EAE6" strokeWidth="1.6" /><path d="M16 15 L22 21 L16 29 L10 21 Z" fill="#A87B2C" /></svg>
          <span><span className="block text-[19px] font-bold leading-none tracking-[-0.02em]">Plumb</span><span className="text-xs text-mute">Brand CI for Kora Naturals</span></span>
        </button>

        <nav className="flex flex-wrap gap-1 md:flex-col" aria-label="Main">
          <NavLink to="/" end className={link}>Home</NavLink>
          {perms['cpr.create'] && <NavLink to="/new" className={link}>New draft</NavLink>}
          {perms['review'] && <NavLink to="/queue" className={link}>Review queue</NavLink>}
          <NavLink to="/policy" className={link}>Brand policy</NavLink>
          <NavLink to="/facts" className={link}>Facts ledger</NavLink>
          <NavLink to="/ai-log" className={link}>AI disclosure</NavLink>
          <NavLink to="/about" className={link}>How Plumb works</NavLink>
        </nav>

        <div className="mt-auto space-y-3">
          <div className="rounded-lg border border-rule bg-sheet p-3">
            <label htmlFor="who" className="lbl">Viewing as</label>
            <select id="who" className="field py-1.5 text-sm" value={me?.id || ''} onChange={e => switchUser(e.target.value)}>
              {meta?.users.map(u => <option key={u.id} value={u.id}>{u.name} — {u.role_label}</option>)}
            </select>
            {me && <p className="mt-1.5 text-xs leading-snug text-mute">{me.title}. Demo workspace: switch people to see each role's view.</p>}
          </div>
          {meta && (
            <p className="flex items-center gap-2 px-1 text-xs text-mute">
              <span className={`inline-block h-2 w-2 rounded-full ${meta.llm.enabled ? 'bg-pass' : 'bg-warn'}`} />
              {meta.llm.enabled ? `AI checks on (${meta.llm.model})` : 'AI checks off: rules + local models only'}
            </p>
          )}
        </div>
      </div>
    </aside>
  )
}

import { useApp } from './ui'
function useAppSafe() { return useApp() }
