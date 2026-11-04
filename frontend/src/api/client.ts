import { reactive } from 'vue'

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

interface SessionUser { username: string; name: string; temporary?: boolean; expires_at?: string | null }

export const session = reactive<{ checked: boolean; authenticated: boolean; user: SessionUser | null }>({
  checked: false,
  authenticated: false,
  user: null,
})

function csrfToken(): string {
  const m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/)
  return m ? decodeURIComponent(m[1]) : ''
}

let onUnauthorized: (() => void) | null = null
export function setUnauthorizedHandler(fn: () => void) { onUnauthorized = fn }

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json' }
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (method !== 'GET') headers['X-CSRFToken'] = csrfToken()
  let res: Response
  try {
    res = await fetch(`/api/${path}`, {
      method, headers, credentials: 'same-origin',
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch {
    throw new ApiError(0, 'Unable to reach the dashboard server. Check that the backend is running.')
  }
  const data = await res.json().catch(() => null)
  if (!res.ok) {
    if ((res.status === 401 || res.status === 403) && !path.startsWith('auth/')) {
      // Only sign out if the server confirms the session is really gone.
      if (!(await confirmSession())) {
        session.authenticated = false
        session.user = null
        onUnauthorized?.()
      }
    }
    throw new ApiError(res.status, data?.error?.message ?? `Request failed (${res.status})`)
  }
  return data as T
}

let pendingCheck: Promise<boolean> | null = null
/** One shared session re-check for any burst of 401/403 responses. */
function confirmSession(): Promise<boolean> {
  pendingCheck ??= fetch('/api/auth/session', { credentials: 'same-origin', headers: { Accept: 'application/json' } })
    .then((r) => (r.ok ? r.json() : null))
    .then((s) => Boolean(s?.authenticated))
    .catch(() => false)
    .finally(() => { setTimeout(() => { pendingCheck = null }, 0) })
  return pendingCheck
}

export function qs(params: Record<string, string | number | null | undefined>): string {
  const p = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) if (v !== null && v !== undefined && v !== '') p.set(k, String(v))
  const s = p.toString()
  return s ? `?${s}` : ''
}

export const api = {
  get: <T>(path: string) => request<T>('GET', path),
  post: <T>(path: string, body?: unknown) => request<T>('POST', path, body ?? {}),
}

export async function loadSession() {
  const s = await api.get<{ authenticated: boolean; user: SessionUser | null }>('auth/session')
  session.authenticated = s.authenticated
  session.user = s.user
  session.checked = true
}

export async function login(username: string, password: string) {
  await api.get('auth/session') // ensures the CSRF cookie is set
  const s = await api.post<{ authenticated: boolean; user: SessionUser }>('auth/login', { username, password })
  session.authenticated = s.authenticated
  session.user = s.user
}

export async function logout() {
  try { await api.post('auth/logout') } finally {
    session.authenticated = false
    session.user = null
  }
}
