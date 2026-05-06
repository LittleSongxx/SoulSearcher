import { getApiBaseUrl } from '@/lib/api'
import { ContinueResearchResponse, EvidenceResponse } from '@/types/evidence'

export interface ResearchSessionSummary {
  thread_id: string
  status: string
  topic: string
  created_at?: string
  updated_at?: string
  route?: string
  has_report?: boolean
  revision_count?: number
  message_count?: number
}

export interface ResearchSessionsResponse {
  count: number
  sessions: ResearchSessionSummary[]
}

export async function fetchResearchSessions(): Promise<ResearchSessionsResponse> {
  const response = await fetch(`${getApiBaseUrl()}/api/sessions`)
  if (!response.ok) throw new Error(`Research sessions request failed: ${response.status}`)
  return response.json()
}

export async function fetchResearchEvidence(threadId: string): Promise<EvidenceResponse | null> {
  const response = await fetch(`${getApiBaseUrl()}/api/sessions/${threadId}/evidence`)
  if (response.status === 404) return null
  if (!response.ok) throw new Error(`Evidence request failed: ${response.status}`)
  return response.json()
}

export async function continueResearch(threadId: string, payload: Record<string, any>): Promise<ContinueResearchResponse> {
  const response = await fetch(`${getApiBaseUrl()}/api/sessions/${threadId}/continue-research`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!response.ok) throw new Error(`Continue research request failed: ${response.status}`)
  return response.json()
}
