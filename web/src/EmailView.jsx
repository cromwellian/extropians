import React from 'react'

const URL_RE = /(https?:\/\/[^\s<>()"]+[^\s<>()".,;:!?'])/g

function linkify(text, keyBase) {
  const parts = text.split(URL_RE)
  return parts.map((p, i) =>
    URL_RE.test(p) && p.startsWith('http') ? (
      <a key={`${keyBase}-${i}`} href={p} target="_blank" rel="noreferrer" className="body-link">
        {p}
      </a>
    ) : (
      p
    ),
  )
}

function quoteDepth(line) {
  let d = 0
  let i = 0
  while (i < line.length) {
    const c = line[i]
    if (c === '>') {
      d += 1
      i += 1
    } else if (c === ' ' && d > 0) {
      i += 1
    } else break
  }
  return d
}

// Render a plain-text email body with quote-depth coloring, dimmed
// signature, and clickable URLs.
export function EmailBody({ body }) {
  const lines = (body || '').split('\n')
  // signature: last "-- " marker, or a trailing run of short decorated lines
  let sigStart = -1
  for (let i = lines.length - 1; i >= Math.max(0, lines.length - 25); i--) {
    if (/^--\s*$/.test(lines[i]) || /^-- $/.test(lines[i])) {
      sigStart = i
      break
    }
  }
  return (
    <pre className="email-body">
      {lines.map((line, i) => {
        const d = quoteDepth(line)
        const cls = [
          d > 0 ? `q q${Math.min(d, 4)}` : '',
          sigStart !== -1 && i >= sigStart ? 'sig' : '',
          /^[A-Za-z-]+:\s/.test(line) && d === 0 && i < 6 ? '' : '',
        ]
          .filter(Boolean)
          .join(' ')
        return (
          <span key={i} className={cls || undefined}>
            {linkify(line, i)}
            {'\n'}
          </span>
        )
      })}
    </pre>
  )
}

export function fmtDate(iso) {
  if (!iso) return 'unknown date'
  try {
    return new Date(iso).toLocaleString('en-US', {
      weekday: 'short',
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return iso
  }
}

export function EmailMessage({ msg, onOpenThread }) {
  const headerRows = []
  if (msg.headers) {
    for (const line of msg.headers.split('\n')) {
      const idx = line.indexOf(':')
      if (idx > 0) headerRows.push([line.slice(0, idx), line.slice(idx + 1).trim()])
    }
  }
  return (
    <div className="email-msg">
      <div className="email-headers">
        <div className="email-subject">{msg.subject}</div>
        {headerRows
          .filter(([k]) => !['Subject'].includes(k))
          .map(([k, v], i) => (
            <div className="hdr-row" key={i}>
              <span className="hdr-key">{k}</span>
              <span className="hdr-val">{k === 'Date' ? v : v}</span>
            </div>
          ))}
        <div className="hdr-row">
          <span className="hdr-key">Archive</span>
          <span className="hdr-val hdr-src">
            {msg.source_file}
            {msg.digest_label ? ` · digest ${msg.digest_label}` : ''}
          </span>
        </div>
        {msg.thread_size > 1 && (
          <button className="thread-btn" onClick={() => onOpenThread(msg.id)}>
            View thread ({msg.thread_size} messages)
          </button>
        )}
      </div>
      <EmailBody body={msg.body} />
    </div>
  )
}
