import React, { useState, useEffect } from 'react'
import { search, getMessage, getThread } from './api'
import { EmailMessage, fmtDate } from './EmailView'

function ResultRow({ m, onClick, active }) {
  return (
    <button className={`result-row${active ? ' active' : ''}`} onClick={onClick}>
      <div className="rr-top">
        <span className="rr-subj">{m.subject}</span>
      </div>
      <div className="rr-meta">
        <span className="rr-from">{m.from_name}</span>
        <span className="rr-date">{fmtDate(m.date)}</span>
        {m.digest_label && <span className="rr-digest">{m.digest_label}</span>}
      </div>
      {m.snippet && <div className="rr-snippet">{m.snippet}</div>}
    </button>
  )
}

export default function Viewer({ openMessageId, clearOpen }) {
  const [tab, setTab] = useState('search') // search | message | thread
  const [q, setQ] = useState('')
  const [mode, setMode] = useState('hybrid')
  const [results, setResults] = useState([])
  const [searching, setSearching] = useState(false)
  const [msg, setMsg] = useState(null)
  const [thread, setThread] = useState(null)

  useEffect(() => {
    if (openMessageId != null) {
      openMsg(openMessageId)
      clearOpen()
    }
  }, [openMessageId])

  // deep link: #msg-1234 opens that message on load
  useEffect(() => {
    const m = window.location.hash.match(/^#msg-(\d+)$/)
    if (m) openMsg(Number(m[1]))
  }, [])

  async function openMsg(id) {
    try {
      const m = await getMessage(id)
      setMsg(m)
      setTab('message')
      window.history.replaceState(null, '', `#msg-${id}`)
    } catch {
      /* ignore */
    }
  }

  async function openThread(id) {
    const t = await getThread(id)
    setThread(t)
    setTab('thread')
  }

  async function doSearch(e) {
    e?.preventDefault()
    if (!q.trim()) return
    setSearching(true)
    setTab('search')
    try {
      const r = await search(q, mode)
      setResults(r.results || [])
    } finally {
      setSearching(false)
    }
  }

  return (
    <div className="viewer-pane">
      <div className="viewer-tabs">
        <button className={tab === 'search' ? 'on' : ''} onClick={() => setTab('search')}>
          Search
        </button>
        <button
          className={tab === 'message' ? 'on' : ''}
          onClick={() => setTab('message')}
          disabled={!msg}
        >
          Message
        </button>
        <button
          className={tab === 'thread' ? 'on' : ''}
          onClick={() => setTab('thread')}
          disabled={!thread}
        >
          Thread
        </button>
      </div>

      {tab === 'search' && (
        <div className="viewer-body">
          <form className="search-bar" onSubmit={doSearch}>
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search the archive directly…"
            />
            <select value={mode} onChange={(e) => setMode(e.target.value)}>
              <option value="hybrid">hybrid</option>
              <option value="keyword">keyword</option>
              <option value="semantic">semantic</option>
            </select>
            <button type="submit">{searching ? '…' : 'Go'}</button>
          </form>
          <div className="results">
            {results.map((m) => (
              <ResultRow key={m.id} m={m} active={msg?.id === m.id} onClick={() => openMsg(m.id)} />
            ))}
            {!results.length && !searching && (
              <div className="results-empty">
                Search results appear here. Clicking a citation in the chat also opens the
                original email here.
              </div>
            )}
          </div>
        </div>
      )}

      {tab === 'message' && msg && (
        <div className="viewer-body">
          <EmailMessage msg={msg} onOpenThread={openThread} />
        </div>
      )}

      {tab === 'thread' && thread && (
        <div className="viewer-body">
          <div className="thread-head">
            <span className="thread-subj">“{thread.subject}”</span>
            <span className="thread-count">{thread.messages.length} messages</span>
          </div>
          <div className="results">
            {thread.messages.map((m) => (
              <ResultRow key={m.id} m={m} active={msg?.id === m.id} onClick={() => openMsg(m.id)} />
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
