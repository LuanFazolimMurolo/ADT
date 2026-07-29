import { SystemStatus } from '../types/system'

const API_URL = import.meta.env.VITE_ADT_API_URL || 'http://localhost:8000'

class APIClient {
  private baseURL: string

  constructor(baseURL: string) {
    this.baseURL = baseURL
  }

  private async request<T>(
    path: string,
    options?: RequestInit,
  ): Promise<T> {
    const url = `${this.baseURL}${path}`

    const response = await fetch(url, {
      headers: {
        'Content-Type': 'application/json',
        ...options?.headers,
      },
      ...options,
    })

    if (!response.ok) {
      throw new Error(
        `API Error: ${response.status} ${response.statusText}`,
      )
    }

    return response.json()
  }

  async getSystemStatus(): Promise<SystemStatus> {
    return this.request('/api/v1/system/status')
  }

  async getHealth(): Promise<{ status: string }> {
    return this.request('/health')
  }
}

export const apiClient = new APIClient(API_URL)
