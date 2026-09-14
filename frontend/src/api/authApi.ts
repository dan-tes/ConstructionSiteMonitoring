import { httpAuthApi } from './httpAuthApi'
import type { AuthApi } from './types'

// Real HTTP implementation backed by the FastAPI service (see backend/).
// Swap for ./mockAuthApi to work offline against localStorage.
export const authApi: AuthApi = httpAuthApi

export * from './types'
