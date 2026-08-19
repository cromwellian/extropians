import React, { useRef, useState, useEffect } from 'react'
import ReactMarkdown from 'react-markdown'
import { chatStream } from './api'
import { fmtDate } from './EmailView'

// Turn bare [3] citations into markdown links so we can render chips.
function citeLinks(text) {
  let out = ''
  let inCode = false
  for (const seg of text.split(/(```)/)) {
    if (seg === '```') {
      inCode = !inCode
      out += seg
    } else {
      out += inCode ? seg : seg.replace(/\[(\d{1,2})\]/g, '[$1](#cite-$1)')
    }
  }
  return out
}

function Answer({ text, sources, onOpenMessage }) {
  const byN = {}
  for (const s of sources || []) byN[s.n] = s
  return (
    <ReactMarkdown
      components={{
        a({ href, children, ...props }) {
          if (href && href.startsWith('#cite-')) {
            const n = Number(href.slice(6))
            const src = byN[n]
            return (
              <button
                className="cite-chip"
                title={src ? `${src.subject} — ${src.from_name}` : `source ${n}`}
                onClick={() => src && onOpenMessage(src.id)}
              >
                {n}
              </button>
            )
          }
          return (
            <a href={href} target="_blank" rel="noreferrer" {...props}>
              {children}
            </a>
          )
        },
      }}
    >
      {citeLinks(text)}
    </ReactMarkdown>
  )
}

function SourceList({ sources, onOpenMessage }) {
  if (!sources?.length) return null
  return (
    <div className="source-list">
      <div className="source-list-title">Sources</div>
      {sources.map((s) => (
        <button key={s.n} className="source-item" onClick={() => onOpenMessage(s.id)}>
          <span className="source-n">[{s.n}]</span>
          <span className="source-subj">{s.subject}</span>
          <span className="source-meta">
            {s.from_name} · {fmtDate(s.date)}
          </span>
        </button>
      ))}
    </div>
  )
}

const SUGGESTIONS = [
  'What did people think about cryonics pricing in the 90s?',
  'Summarize the debates about nanotech "grey goo" risks',
  'What did Eliezer Yudkowsky post about in 1999?',
  'Find threads about privately produced law (PPL)',
]

export default function Chat({ onOpenMessage }) {
  const [turns, setTurns] = useState([]) // {role, content, sources?, streaming?}
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const scrollRef = useRef(null)

  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [turns])

  async function send(text) {
    const q = (text ?? input).trim()
    if (!q || busy) return
    setInput('')
    setBusy(true)
    const history = [...turns, { role: 'user', content: q }]
    setTurns([...history, { role: 'assistant', content: '', sources: [], streaming: true }])

    const apiMessages = history.map(({ role, content }) => ({ role, content }))
    let acc = ''
    let sources = []
    await chatStream(apiMessages, {
      onSources(s) {
        sources = s
        setTurns((t) => {
          const copy = [...t]
          copy[copy.length - 1] = { ...copy[copy.length - 1], sources: s }
          return copy
        })
      },
      onDelta(d) {
        acc += d
        setTurns((t) => {
          const copy = [...t]
          copy[copy.length - 1] = { ...copy[copy.length - 1], content: acc, sources }
          return copy
        })
      },
      onError(e) {
        acc += `\n\n**Error:** ${e}`
        setTurns((t) => {
          const copy = [...t]
          copy[copy.length - 1] = { ...copy[copy.length - 1], content: acc }
          return copy
        })
      },
      onDone() {
        setTurns((t) => {
          const copy = [...t]
          copy[copy.length - 1] = { ...copy[copy.length - 1], streaming: false }
          return copy
        })
        setBusy(false)
      },
    })
  }

  return (
    <div className="chat-pane">
      <div className="chat-scroll" ref={scrollRef}>
        {turns.length === 0 && (
          <div className="chat-empty">
            <div className="empty-title">Ask the archive</div>
            <p>
              ~148,000 messages from the Extropians mailing list, 1992–2003. Ask about topics,
            people, or old threads — answers cite the original emails.
            </p>
            <div className="suggestions">
              {SUGGESTIONS.map((s) => (
                <button key={s} onClick={() => send(s)}>
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}
        {turns.map((t, i) =>
          t.role === 'user' ? (
            <div className="turn user" key={i}>
              {t.content}
            </div>
          ) : (
            <div className="turn assistant" key={i}>
              {t.content ? (
                <Answer text={t.content} sources={t.sources} onOpenMessage={onOpenMessage} />
              ) : (
                <span className="thinking">searching archive{'…'}</span>
              )}
              {t.streaming && t.content && <span className="cursor">▌</span>}
              {!t.streaming && <SourceList sources={t.sources} onOpenMessage={onOpenMessage} />}
            </div>
          ),
        )}
      </div>
      <form
        className="chat-input"
        onSubmit={(e) => {
          e.preventDefault()
          send()
        }}
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask about the Extropians archive…"
          disabled={busy}
        />
        <button type="submit" disabled={busy || !input.trim()}>
          {busy ? '…' : 'Ask'}
        </button>
      </form>
    </div>
  )
}
