export type ResearchRequest = {
  topic: string
  project_path?: string
  profile?: 'fast' | 'standard' | 'deep'
  max_depth?: number
  num_papers?: number
  provider?: string
  openrouter_api_key?: string
  api_key?: string
  model?: string
  critic_strictness?: number
  uploaded_content?: string
  encrypted_api_key?: string
  encryption_iv?: string
  encryption_salt?: string
  encryption_passphrase?: string
  job_id?: string
}

export type RavenWorkflowEvent = {
  seq: number
  step: string
  status: 'started' | 'running' | 'completed' | 'failed' | 'disabled' | 'skipped'
  details: Record<string, any>
  timestamp: number
}

export type EvidenceItem = {
  evidence_id: string
  source_type: 'github' | 'documentation' | 'papers' | 'web' | 'community' | 'local_project'
  canonical_url: string
  title: string
  publisher_or_repo: string
  published_date?: string
  updated_date?: string
  checked_at: string
  excerpt: string
  location?: string
  objective_id: string
  verification_state: 'verified' | 'unverified' | 'disputed' | 'unreachable'
  verification_notes: string
  freshness_state: 'fresh' | 'aging' | 'stale' | 'unknown'
}

export type ResearchObjective = {
  id: string
  description: string
  questions: string[]
  acceptance_criteria: string[]
  assigned_tunnels: string[]
  freshness_requirement: string
}

export type ResearchPlan = {
  profile: string
  objectives: ResearchObjective[]
  is_approved: boolean
  approval_policy: string
  revision_count: number
}

export type AnalysisResult = {
  intent: 'idea' | 'project'
  goal: string
  constraints: string[]
  assumptions: string[]
  unknowns: string[]
  project_inspection?: {
    root_path: string
    is_valid: boolean
    detected_stack: string[]
    structure_summary: string
    manifest_files: string[]
    dependencies: string[]
    risks_and_gaps: string[]
  }
}

export type CritiqueGap = {
  objective_id: string
  description: string
  severity: string
  suggested_tunnel: string
}

export type CritiqueReport = {
  coverage_score: number
  unresolved_gaps: CritiqueGap[]
  contradictions: string[]
  risks: string[]
  should_continue: boolean
  reason: string
}

export type ImageResult = {
  title: string
  image_url: string
  thumbnail_url?: string
  source_url: string
  source_domain: string
  width?: number
  height?: number
}

export type StructuredSource = {
  title: string
  url: string
  summary: string
  relevant_excerpts: string[]
  objective_ids: string[]
  retrieval_status: 'discovered' | 'fetched' | 'extracted' | 'evidence_created' | 'verified' | 'failed'
  verification_status: string
  checked_at?: string | null
}

export type ResearchResponse = {
  title?: string
  final_report: string
  answer_markdown?: string
  direct_answer?: string
  detailed_explanation?: string
  key_findings?: string[]
  limitations?: string[]
  run_status?: 'completed' | 'partial' | 'failed'
  sources: string[]
  structured_sources?: StructuredSource[]
  topic: string
  stop_reason?: string
  events?: RavenWorkflowEvent[]
  evidence?: EvidenceItem[]
  plan?: ResearchPlan
  analysis?: AnalysisResult
  critique?: CritiqueReport
  graph_data?: {
    nodes: Array<{ id: string; type?: string; label?: string; url?: string }>
    links: Array<{ source: string; target: string; weight?: number }>
  }
  images?: ImageResult[]
  graphs?: ImageResult[]
}

export type ChatMessage = {
  id: string
  role: 'user' | 'assistant'
  content: string
  timestamp: number
}

export type TimelineEntry = {
  year: number
  label: string
}

export type CitationStyle = 'inline' | 'apa' | 'mla' | 'chicago' | 'ieee'
