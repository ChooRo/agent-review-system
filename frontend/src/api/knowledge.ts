import { request } from './index'

export interface KnowledgeListItem {
  document_key: string
  directory: string
  title: string
  issuer?: string
  effective_date?: string
  status?: string
  unit_count?: number
  article_count?: number
  quality_status?: string
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
    issuer?: string
    promulgation_date?: string
    revision_date?: string
    effective_date?: string
    status?: string
    applicable_region?: string
    parser?: { name: string; source: string }
  }
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

export function listKnowledge(): Promise<KnowledgeListItem[]> {
  return request('/knowledge')
}

export function getKnowledge(documentKey: string): Promise<KnowledgeDetail> {
  return request(`/knowledge/${encodeURIComponent(documentKey)}`)
}
