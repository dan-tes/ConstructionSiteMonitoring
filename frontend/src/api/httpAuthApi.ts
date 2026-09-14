import { ApiError, request } from './client'
import { type AuthApi, type AuthErrorCode, AuthApiError, type Session } from './types'

const AUTH_CODES: AuthErrorCode[] = [
  'USERNAME_TAKEN',
  'INVALID_CREDENTIALS',
  'UNKNOWN_USER',
  'VALIDATION',
]

function toAuthError(err: unknown): AuthApiError {
  if (err instanceof AuthApiError) return err
  if (err instanceof ApiError) {
    const code = AUTH_CODES.includes(err.code as AuthErrorCode)
      ? (err.code as AuthErrorCode)
      : 'VALIDATION'
    return new AuthApiError(err.message, code)
  }
  return new AuthApiError('Не удалось выполнить запрос', 'VALIDATION')
}

export const httpAuthApi: AuthApi = {
  async getSalt(username) {
    const { salt } = await request<{ salt: string }>('/auth/salt', {
      method: 'POST',
      auth: false,
      body: { username },
    })
    return salt
  },

  async register(username, passwordHash, salt) {
    try {
      return await request<Session>('/auth/register', {
        method: 'POST',
        auth: false,
        body: { username, passwordHash, salt },
      })
    } catch (err) {
      throw toAuthError(err)
    }
  },

  async login(username, passwordHash) {
    try {
      return await request<Session>('/auth/login', {
        method: 'POST',
        auth: false,
        body: { username, passwordHash },
      })
    } catch (err) {
      throw toAuthError(err)
    }
  },

  async logout(token) {
    try {
      await request<void>('/auth/logout', { method: 'POST', token })
    } catch (err) {
      // Logging out is best-effort: an already-invalid token is fine.
      if (!(err instanceof ApiError) || err.status !== 401) throw err
    }
  },

  async getSession(token) {
    try {
      return await request<Session>('/auth/session', { token })
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) return null
      throw err
    }
  },
}
