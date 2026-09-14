export interface User {
  id: string
  username: string
  createdAt: string
}

export interface Session {
  token: string
  user: User
  expiresAt: string
}

export type AuthErrorCode = 'USERNAME_TAKEN' | 'INVALID_CREDENTIALS' | 'UNKNOWN_USER' | 'VALIDATION'

export class AuthApiError extends Error {
  code: AuthErrorCode

  constructor(message: string, code: AuthErrorCode) {
    super(message)
    this.name = 'AuthApiError'
    this.code = code
  }
}

/**
 * Contract the real backend should implement. The mock implementation
 * (mockAuthApi.ts) fulfils the same shape against localStorage so swapping
 * it for real HTTP calls later is a one-file change.
 */
export interface AuthApi {
  /** Returns the per-user salt to hash a password against (registration: generate a fresh one). */
  getSalt(username: string): Promise<string>
  register(username: string, passwordHash: string, salt: string): Promise<Session>
  login(username: string, passwordHash: string): Promise<Session>
  logout(token: string): Promise<void>
  getSession(token: string): Promise<Session | null>
}
