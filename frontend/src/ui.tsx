import { useState, type ReactNode } from 'react'

export function Badge({ children, tone = 'neutral' }: { children: ReactNode; tone?: 'neutral' | 'green' | 'amber' | 'red' | 'blue' }) {
  return <span className={`badge ${tone}`}>{children}</span>
}

export function Empty({ title, children }: { title: string; children: ReactNode }) {
  return <div className="empty"><span className="empty-icon" aria-hidden="true">◇</span><h3>{title}</h3><p>{children}</p></div>
}

export function JsonDetails({ data, label = 'View structured details' }: { data: unknown; label?: string }) {
  return <details className="json-details"><summary>{label}</summary><pre>{JSON.stringify(data, null, 2) ?? 'No data'}</pre></details>
}

export function dateTime(value?: string | null, timezone?: string) {
  if (!value) return 'Not scheduled'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  try {
    return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short', ...(timezone ? { timeZone: timezone } : {}) }).format(date)
  } catch { return date.toLocaleString() }
}

export function pretty(value: string) {
  return value.replaceAll('_', ' ')
}

export function statusTone(status: string): 'green' | 'red' | 'blue' | 'amber' | 'neutral' {
  if (['completed', 'succeeded', 'success'].includes(status)) return 'green'
  if (['failed', 'error', 'denied'].includes(status)) return 'red'
  if (['running', 'pending', 'queued'].includes(status)) return 'blue'
  if (['cancelled', 'interrupted', 'blocked', 'unknown'].includes(status)) return 'amber'
  return 'neutral'
}

export function SectionHeading({ eyebrow, title, children, action }: { eyebrow?: string; title: string; children?: ReactNode; action?: ReactNode }) {
  return <div className="section-heading"><div>{eyebrow && <p className="eyebrow">{eyebrow}</p>}<h2>{title}</h2>{children && <p className="muted">{children}</p>}</div>{action}</div>
}

export function Notice({ message, tone = 'error' }: { message: string; tone?: 'error' | 'success' | 'info' }) {
  return <div role={tone === 'error' ? 'alert' : 'status'} className={`notice ${tone}`}>{message}</div>
}

export function useOperation() {
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')
  async function perform(operation: () => Promise<void>, message = '') {
    if (pending) return
    setPending(true)
    setError('')
    setSuccess('')
    try { await operation(); setSuccess(message) }
    catch (err) { setError(err instanceof Error ? err.message : 'Something went wrong. Please try again.') }
    finally { setPending(false) }
  }
  return { pending, error, success, perform, setError }
}

export function OperationStatus({ error, success }: { error: string; success: string }) {
  return <>{error && <Notice message={error} />}{success && <Notice message={success} tone="success" />}</>
}
