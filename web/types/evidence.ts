export interface EvidenceSource {
  title?: string
  url?: string
  summary?: string
  snippet?: string
  source_type?: string
  provider?: string
}

export interface EvidenceClaim {
  claim?: string
  status?: string
  score?: number
  notes?: string
  evidence_urls?: string[]
  evidence_passages?: EvidencePassage[]
}

export interface EvidencePassage {
  url?: string
  text?: string
  quote?: string
  heading?: string
  heading_path?: string[]
  page_title?: string
  snippet_hash?: string
  method?: string
  retrieved_at?: string
}

export interface QualityGateRecord {
  epoch?: number
  stage?: string
  gates?: Array<{
    name?: string
    status?: string
    reason?: string
    details?: Record<string, any>
  }>
}

export interface TimelineEvent {
  id?: string
  order?: number
  event_type?: string
  title?: string
  description?: string
  url?: string
  timestamp?: string
}

export interface ContinueResearchRequestItem {
  request_id?: string
  target_type?: string
  target_text?: string
  instruction?: string
  created_at?: string
}

export interface EvidenceResponse {
  sources?: EvidenceSource[]
  claims?: EvidenceClaim[]
  quality_summary?: Record<string, any>
  research_brief?: Record<string, any>
  quality_gates?: QualityGateRecord[]
  evidence_items?: EvidenceSource[]
  citation_annotations?: any[]
  timeline?: TimelineEvent[]
  supervisor_decisions?: any[]
  worker_runs?: any[]
  intermediate_steps?: any[]
  continue_requests?: ContinueResearchRequestItem[]
  fetched_pages?: any[]
  passages?: EvidencePassage[]
}

export interface ContinueResearchResponse {
  success: boolean
  thread_id: string
  status: string
  continue_request: Record<string, any>
  resume_input: string
  update_state: Record<string, any>
  stream_payload: Record<string, any>
  resume_state: Record<string, any>
}
