import React, { useEffect, useState } from 'react'
import Chat from './Chat'
import Viewer from './Viewer'
import { getStats } from './api'

export default function App() {
  const [stats, setStats] = useState(null)
  const [openId, setOpenId] = useState(null)

  useEffect(() => {
    getStats().then(setStats).catch(() => {})
  }, [])

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">✦</span>
          <span className="brand-name">Extropians Archive</span>
          <span className="brand-sub">1992–2003</span>
        </div>
        {stats && (
          <div className="stats">
            <span>{stats.messages.toLocaleString()} messages</span>
            <span>{stats.threads.toLocaleString()} threads</span>
            <span>{stats.people.toLocaleString()} posters</span>
            {stats.semantic_status && stats.semantic_status !== 'ready' && (
              <span
                className="warn"
                title={
                  stats.semantic_status === 'stale'
                    ? 'The semantic index was built against an older database. Re-run rag/embed.py.'
                    : 'No semantic index yet. Run rag/embed.py.'
                }
              >
                semantic index {stats.semantic_status} — keyword only
              </span>
            )}
            <span className="backend" title="LLM backend">
              {stats.backend}
            </span>
          </div>
        )}
      </header>
      <main className="panes">
        <Chat onOpenMessage={setOpenId} />
        <Viewer openMessageId={openId} clearOpen={() => setOpenId(null)} />
      </main>
    </div>
  )
}
