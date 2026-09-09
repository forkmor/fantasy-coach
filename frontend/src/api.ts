export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message)
    this.name = 'ApiError'
  }
}

let csrfToken: string | null = null
let unauthorized: (() => void) | undefined

export function onUnauthorized(handler?: () => void) {
  unauthorized = handler
}

export function resetSession() {
  csrfToken = null
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'An unexpected error occurred. Please try again.'
}

export async function request<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const mutation = method !== 'GET' && method !== 'HEAD'
  if (mutation && path !== '/login' && !csrfToken) {
    throw new ApiError(401, 'Your local session has expired. Sign in again.')
  }
  const headers: Record<string, string> = { Accept: 'application/json' }
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (mutation && path !== '/login' && csrfToken) headers['X-CSRF-Token'] = csrfToken
  const response = await fetch(`/api${path}`, {
    method,
    credentials: 'same-origin',
    headers,
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  })
  const text = await response.text()
  let data: unknown = null
  if (text) {
    try { data = JSON.parse(text) } catch {
      if (response.ok) throw new Error('The local server returned an unexpected response.')
    }
  }
  if (!response.ok) {
    if (response.status === 401 && path !== '/login') {
      resetSession()
      unauthorized?.()
    }
    const detail = data && typeof data === 'object' && 'detail' in data ? data.detail : null
    throw new ApiError(response.status, typeof detail === 'string'
      ? detail
      : detail ? JSON.stringify(detail) : `Request failed (${response.status}). Check the local server.`)
  }
  return data as T
}

export async function establishSession(token?: string) {
  if (token) await request('/login', 'POST', { token })
  const session = await request<{ csrf_token: string }>('/session')
  if (!session?.csrf_token) throw new Error('The local server did not provide a session token.')
  csrfToken = session.csrf_token
}

export function consumeLaunchToken(): string | undefined {
  const fragment = new URLSearchParams(window.location.hash.slice(1))
  const token = fragment.get('token') ?? undefined
  if (fragment.has('token')) {
    fragment.delete('token')
    const remaining = fragment.toString()
    window.history.replaceState(null, '', `${window.location.pathname}${window.location.search}${remaining ? `#${remaining}` : ''}`)
  }
  return token
}
