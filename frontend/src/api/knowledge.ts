import { request } from './index'

export interface KnowledgeListItem {
  document_key: string
  title: string
  issuer?: string
  effective_date?: string
  status?: string
  unit_count?: number
  article_count?: number
  quality_status?: string
  document_version?: string
  department?: string
  applicable_scope?: string
  expiry_date?: string
  metadata_version?: number
  updated_at?: string
  updated_by?: string
  extraction_status?: MetadataExtractionStatus
}

export type MetadataExtractionStatus = 'pending_ai' | 'processing' | 'ready' | 'failed' | 'confirmed'

export interface ApplicabilityEvidence {
  legal_unit_id: string
  article_no?: string
  quote: string
  page_no?: number
}

export interface ApplicabilityItem {
  value: string
  evidence: ApplicabilityEvidence[]
  confidence?: number
}

export interface LegalApplicability {
  summary?: string
  activities?: ApplicabilityItem[]
  subjects?: ApplicabilityItem[]
  business_phases?: ApplicabilityItem[]
  trigger_conditions?: ApplicabilityItem[]
  project_types?: ApplicabilityItem[]
  exclusions?: ApplicabilityItem[]
  precedence_rules?: ApplicabilityItem[]
}

export interface LegalBasicInformation {
  canonical_title?: string
  legal_level?: string
  document_number?: string
  issuer?: string
  adoption_date?: string
  promulgation_date?: string
  original_effective_date?: string
  revision_date?: string
  current_version_effective_date?: string
}

export interface MetadataExtraction {
  status: MetadataExtractionStatus
  candidate_unit_ids?: string[]
  warnings?: ({ code?: string; message?: string; field?: string; reason?: string } | string)[]
  field_evidence?: Record<string, (ApplicabilityEvidence | string)[]>
  basic_information?: LegalBasicInformation
  applicability?: LegalApplicability
  updated_at?: string
  error?: string
}

export interface LegalUnit {
  legal_unit_id: string
  unit_type: string
  document_title: string
  chapter?: string
  section?: string
  article_no: string
  article_index?: number
  paragraph_no: number
  item_no?: string
  text: string
  parent_context?: string
  search_text: string
  references: string[]
  effective_date?: string
  status?: string
  evidence: { block_id: string; page_no?: number; quote: string }[]
}

export interface KnowledgeDetail {
  schema_version: string
  legal_document: {
    document_key: string
    title: string
    canonical_title?: string
    legal_level?: string
    document_number?: string
    issuer?: string
    adoption_date?: string
    promulgation_date?: string
    original_effective_date?: string
    revision_date?: string
    current_version_effective_date?: string
    effective_date?: string
    status?: string
    document_version?: string
    department?: string
    applicable_scope?: string
    expiry_date?: string
    metadata_version?: number
    updated_at?: string
    updated_by?: string
    applicable_region?: string
    applicability?: LegalApplicability
    parser?: { name: string; source: string }
  }
  metadata_extraction?: MetadataExtraction
  units: LegalUnit[]
  quality: {
    status: string
    unit_count: number
    article_count: number
    first_article?: number
    last_article?: number
    issues: { code: string; message: string }[]
  }
}

export interface UploadKnowledgeDocumentPayload {
  file: File
  title?: string
  issuer?: string
  department?: string
  document_version?: string
  applicable_scope?: string
  effective_date?: string
  expiry_date?: string
}
export interface KnowledgeUploadTask {
  id: string
  task_id?: string
  status: 'queued' | 'parsing' | 'retrying' | 'storing' | 'completed' | 'failed'
  progress: number
  retry_count: number
  max_retries: number
  message?: string
  error?: string | null
  document_key?: string | null
  result?: KnowledgeListItem
}

export interface UpdateKnowledgeDocumentPayload {
  metadata_version: number
  title?: string
  canonical_title?: string
  legal_level?: string
  document_number?: string
  issuer?: string
  adoption_date?: string
  promulgation_date?: string
  original_effective_date?: string
  revision_date?: string
  current_version_effective_date?: string
  department?: string
  document_version?: string
  applicable_scope?: string
  effective_date?: string
  expiry_date?: string
  applicability?: LegalApplicability
  status?: 'unknown' | 'effective' | 'repealed'
}

export function listKnowledge(): Promise<KnowledgeListItem[]> {
  return request('/knowledge')
}

export function getKnowledge(documentKey: string): Promise<KnowledgeDetail> {
  return request(`/knowledge/${encodeURIComponent(documentKey)}`)
}

export function uploadKnowledgeDocument(payload: UploadKnowledgeDocumentPayload): Promise<KnowledgeUploadTask> {
  const body = new FormData()
  body.append('file', payload.file)
  if (payload.title) body.append('title', payload.title)
  if (payload.issuer) body.append('issuer', payload.issuer)
  if (payload.department) body.append('department', payload.department)
  if (payload.document_version) body.append('document_version', payload.document_version)
  if (payload.applicable_scope) body.append('applicable_scope', payload.applicable_scope)
  if (payload.effective_date) body.append('effective_date', payload.effective_date)
  if (payload.expiry_date) body.append('expiry_date', payload.expiry_date)
  return request('/knowledge/documents', { method: 'POST', body })
}

export function getKnowledgeUploadTask(taskId: string): Promise<KnowledgeUploadTask> {
  return request(`/knowledge/documents/tasks/${encodeURIComponent(taskId)}`)
}
export function retryKnowledgeUploadTask(taskId: string): Promise<KnowledgeUploadTask> {
  return request(`/knowledge/documents/tasks/${encodeURIComponent(taskId)}/retry`, { method: 'POST' })
}

export function updateKnowledgeDocument(documentKey: string, payload: UpdateKnowledgeDocumentPayload): Promise<KnowledgeListItem> {
  return request(`/knowledge/documents/${encodeURIComponent(documentKey)}`, { method: 'PATCH', body: JSON.stringify(payload) })
}

export function extractKnowledgeMetadata(documentKey: string): Promise<KnowledgeDetail> {
  return request(`/knowledge/documents/${encodeURIComponent(documentKey)}/extract-metadata`, { method: 'POST' })
}
