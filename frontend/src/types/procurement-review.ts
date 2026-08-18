export type ProjectStatus = 'draft' | 'active' | 'completed'
export type ReviewStatus = 'draft' | 'rectification_draft' | 'queued' | 'parsing' | 'reviewing' | 'applicability_review' | 'operator_review' | 'primary_review' | 'primary_recheck' | 'completed' | 'final_locked' | 'failed' | 'cancelled'
export type RiskLevel = 'high' | 'medium' | 'low' | 'pending' | 'unknown'
export type TaskRole = 'operator' | 'primary_supervisor' | 'collaborative_supervisor'
export type LegalApplicabilityStatus = 'applicable' | 'not_applicable' | 'potential' | 'insufficient_facts'

export interface LegalSourceFreeze {
  document_key: string
  metadata_version: number
  source_fingerprint: string
  content_fingerprint: string
  fallbacks: string[]
}

export interface LegalApplicability {
  document_key: string
  title?: string
  status: LegalApplicabilityStatus
  reasons: ({ field?: string; expected?: unknown; actual?: unknown; outcome?: string; profile_value?: string } | string)[]
  evidence: { task_facts: Record<string, unknown[]>; profile: unknown[] }
  missing_facts: string[]
  source_freeze: LegalSourceFreeze
}

export interface Project {
  id: string
  name: string
  project_code: string
  handling_department: string
  project_owner: string
  status: ProjectStatus
  created_at: string
  updated_at: string
}
export interface Finding {
  id: string; task_id: string; risk_level: RiskLevel; category: string; title: string
  description: string; suggestion: string
  finding_type?: string
  review_scope?: 'finding' | 'applicability_gate'
  evidence_status?: 'verified' | 'evidence_insufficient'
  evidence_validation?: { errors?: string[]; validation_basis?: string }
    source: { page: number; section_path: string[]; quote: string; block_id: string; bbox?: number[] | null }
    sources?: { page?: number; section_path?: string[]; quote: string; block_id?: string; bbox?: number[] | null }[]
  /** 法规文档解析出的引用，仅作法规依据展示，不等同于执行规则。 */
  legal_refs?: { legal_unit_id?: string; document_title: string; article_no: string; quote: string; page?: number }[]
  /** 已发布且命中的执行规则；当前任务可能为空。 */
  rule_refs: { id: string; title: string }[]
  operator_disposition: { action: 'accept' | 'partial_accept' | 'reject'; comment: string } | null
  primary_decision: { decision: 'receive' | 'adjust' | 'reject'; comment: string; risk_level?: RiskLevel } | null
  collaborative_comments: { id: string; department: string; author: string; comment: string; updated_at: string; version: number }[]
  recheck_required: boolean
  version: number
}
export interface ReviewTask {
  id: string; project_id: string; title: string; status: ReviewStatus
  document: { id: string; file_name: string; content_type: string; size: number; sha256: string; version: number } | null
  document_versions?: { id: string; file_name: string; content_type: string; size: number; sha256: string; version: number }[]
  final_baseline?: { document_id: string; document_version: number; sha256: string; engine_run_id?: string; locked_at: string } | null
  finding_summary: { total: number; high: number; medium: number; low: number; pending: number; unknown?: number }
  created_at: string; updated_at: string; progress: number; progress_step?: string; batch_completed?: number; batch_total?: number; parse_quality?: 'passed' | 'degraded' | 'unreliable'
  task_role: TaskRole
  module_scope?: string[]
  error?: string
  legal_facts?: {
    project_type?: string; procurement_method?: string; is_government_procurement?: boolean
    is_engineering_related?: boolean; is_mandatory_tender?: boolean; region?: string
    review_stage?: string; evidence?: Record<string, unknown>
  }
  /** 后端未返回该字段时保留 undefined，页面显示真实空态。 */
  legal_applicability?: LegalApplicability[]
  /** 仅含正式适用法规的冻结上下文。 */
  legal_context_freeze?: LegalSourceFreeze[]
  pipeline_status?: 'completed' | 'degraded'
  degraded_steps?: { step: string; reasons: string[] }[]
  system_warnings?: { finding_type?: string; title: string; description?: string; recommendation?: string; evidence_block_ids?: string[] }[]
  coverage_matrix?: { topic: string; coverage_status: 'reviewed' | 'evidence_insufficient'; fact_count?: number; legal_unit_count?: number }[]
  legal_applicability_confirmations?: Record<string, { decision: 'confirmed' | 'rejected' | 'needs_more_facts'; comment?: string; by_name?: string; at: string }>
  version: number
}
export interface CreateProjectPayload { name: string; project_code: string; handling_department: string; project_owner: number }
