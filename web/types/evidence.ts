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

export interface ResearchTodo {
  id?: string
  title?: string
  status?: 'pending' | 'running' | 'completed' | 'blocked' | 'cancelled' | string
  progress?: number
  source?: string
  result_preview?: string
  updated_at?: string
}

export interface EvidenceResponse {
  sources?: EvidenceSource[]
  claims?: EvidenceClaim[]
  quality_summary?: Record<string, any>
  research_brief?: Record<string, any>
  research_todos?: ResearchTodo[]
  todo_summary?: Record<string, any>
  retrieval_policy?: Record<string, any>
  evidence_store?: Record<string, any>
  access_policy?: Record<string, any>
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
  research_pipeline?: Record<string, any>
  stage_runtime?: Record<string, any>
  source_quality?: Record<string, any>
  browser_reader_plan?: Record<string, any>
  worker_orchestration?: Record<string, any>
  branch_diagnostics?: Record<string, any>
  brief_review?: Record<string, any>
  fallback?: Record<string, any>
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
