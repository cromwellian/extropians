export async function getStats() {
  const r = await fetch('/api/stats')
  return r.json()
}

export async function search(q, mode = 'hybrid') {
  const r = await fetch(`/api/search?q=${encodeURIComponent(q)}&mode=${mode}`)
  return r.json()
}

export async function getMessage(id) {
  const r = await fetch(`/api/message/${id}`)
  if (!r.ok) throw new Error('not found')
  return r.json()
}

export async function getThread(id) {
  const r = await fetch(`/api/thread/${id}`)
  return r.json()
}

// POST /api/chat with SSE response. Calls handlers as events arrive.
export async function chatStream(messages, { onSources, onDelta, onError, onDone }) {
  const resp = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ messages }),
  })
  if (!resp.ok || !resp.body) {
    onError?.(`server error (${resp.status})`)
    onDone?.()
    return
  }
  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    let idx
    while ((idx = buf.indexOf('\n\n')) !== -1) {
      const frame = buf.slice(0, idx)
      buf = buf.slice(idx + 2)
      let event = 'message'
      let data = ''
      for (const line of frame.split('\n')) {
        if (line.startsWith('event: ')) event = line.slice(7).trim()
        else if (line.startsWith('data: ')) data += line.slice(6)
      }
      if (!data) continue
      try {
        const parsed = JSON.parse(data)
        if (event === 'sources') onSources?.(parsed)
        else if (event === 'delta') onDelta?.(parsed)
        else if (event === 'error') onError?.(parsed)
      } catch { /* skip malformed frame */ }
    }
  }
  onDone?.()
}
