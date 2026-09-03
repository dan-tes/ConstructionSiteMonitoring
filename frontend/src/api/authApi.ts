import { mockAuthApi } from './mockAuthApi'
import type { AuthApi } from './types'

// Swap this for a real HTTP-backed implementation once the backend exists —
// everything else in the app talks to the AuthApi interface, not to this file.
export const authApi: AuthApi = mockAuthApi

export * from './types'
