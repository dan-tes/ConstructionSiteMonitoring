const API_URL = (import.meta.env.VITE_API_URL ?? 'http://localhost:8000').replace(/\/$/, '')

export const TOKEN_KEY = 'csm.session.token'

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export interface ApiErrorBody {
  code?: string
  message?: string
}

export class ApiError extends Error {
  status: number
  code?: string

  constructor(status: number, message: string, code?: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

interface RequestOptions {
  method?: string
  body?: unknown
  /** Send the stored bearer token (default: true). */
  auth?: boolean
  /** Override the token instead of reading it from storage. */
  token?: string | null
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, auth = true, token } = options

  const isForm = body instanceof FormData
  const headers: Record<string, string> = {}
  // Let the browser set multipart's Content-Type (with boundary) itself.
  if (body !== undefined && !isForm) headers['Content-Type'] = 'application/json'

  const bearer = token ?? (auth ? getToken() : null)
  if (bearer) headers.Authorization = `Bearer ${bearer}`

  let response: Response
  try {
    response = await fetch(`${API_URL}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : isForm ? body : JSON.stringify(body),
    })
  } catch {
    throw new ApiError(0, 'Не удалось связаться с сервером. Проверьте подключение.')
  }

  if (response.status === 204) return undefined as T

  let payload: unknown = null
  const text = await response.text()
  if (text) {
    try {
      payload = JSON.parse(text)
    } catch {
      payload = text
    }
  }

  if (!response.ok) {
    const detail = (payload as { detail?: ApiErrorBody | string } | null)?.detail
    if (detail && typeof detail === 'object') {
      throw new ApiError(response.status, detail.message ?? 'Ошибка запроса', detail.code)
    }
    throw new ApiError(
      response.status,
      typeof detail === 'string' ? detail : `Ошибка запроса (${response.status})`,
    )
  }

  return payload as T
}

/** Like `request`, but for a binary response (e.g. a generated .xlsx) that
 * isn't JSON/text — callers turn the Blob into an object URL to trigger a
 * download. */
export async function requestBlob(path: string): Promise<Blob> {
  const headers: Record<string, string> = {}
  const bearer = getToken()
  if (bearer) headers.Authorization = `Bearer ${bearer}`

  let response: Response
  try {
    response = await fetch(`${API_URL}${path}`, { headers })
  } catch {
    throw new ApiError(0, 'Не удалось связаться с сервером. Проверьте подключение.')
  }
  if (!response.ok) {
    throw new ApiError(response.status, `Ошибка запроса (${response.status})`)
  }
  return response.blob()
}
