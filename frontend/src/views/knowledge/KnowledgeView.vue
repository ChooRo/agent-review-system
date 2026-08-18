<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { apiErrorMessage } from '../../api'
import {
  extractKnowledgeMetadata,
  getKnowledgeUploadTask,
  retryKnowledgeUploadTask,
  getKnowledge,
  listKnowledge,
  updateKnowledgeDocument,
  uploadKnowledgeDocument,
} from '../../api/knowledge'
import type {
  ApplicabilityEvidence,
  ApplicabilityItem,
  KnowledgeDetail,
  KnowledgeListItem,
  LegalApplicability,
  LegalUnit,
  MetadataExtractionStatus,
  KnowledgeUploadTask,
} from '../../api/knowledge'
import BaseModal from '../../components/base/BaseModal.vue'
import RuleGovernancePanel from '../../components/business/RuleGovernancePanel.vue'
import { canMaintainKnowledge, hasRole } from '../../policies/permissions'
import { useAuthStore } from '../../stores/auth'

type KnowledgeTab = 'docs' | 'rules' | 'biz'
type ApplicabilityKey = Exclude<keyof LegalApplicability, 'summary'>

const applicabilityGroups: { key: ApplicabilityKey; label: string }[] = [
  { key: 'activities', label: '适用活动' },
  { key: 'subjects', label: '适用主体' },
  { key: 'business_phases', label: '业务阶段' },
  { key: 'trigger_conditions', label: '触发条件' },
  { key: 'project_types', label: '项目类型' },
  { key: 'exclusions', label: '除外情形' },
  { key: 'precedence_rules', label: '优先适用规则' },
]

const kbtab = ref<KnowledgeTab>('docs')
const auth = useAuthStore()
const documents = ref<KnowledgeListItem[]>([])
const loading = ref(true)
const error = ref('')
const notice = ref('')
const expandedDoc = ref<KnowledgeListItem>()
const expandedDetail = ref<KnowledgeDetail>()
const legalUnits = ref<LegalUnit[]>([])
const detailLoading = ref(false)
const documentQuery = ref('')
const extractingKey = ref('')
const extractionError = ref<Record<string, string>>({})
const showUpload = ref(false)
const uploadFile = ref<File>()
const uploadTitle = ref('')
const uploadIssuer = ref('')
const uploadDepartment = ref('')
const uploadDocumentVersion = ref('')
const uploadApplicableScope = ref('')
const uploadEffectiveDate = ref('')
const uploadExpiryDate = ref('')
const uploading = ref(false)
const uploadError = ref('')
const uploadTask = ref<KnowledgeUploadTask>()
let uploadTimer: number | undefined
const isAdmin = computed(() => canMaintainKnowledge(auth.user))
const isSupervisor = computed(() => hasRole(auth.user, 'supervisor'))
const editingDocument = ref<KnowledgeListItem>()
const editingDetail = ref<KnowledgeDetail>()
const metadataLoading = ref(false)
const savingMetadata = ref(false)
const metadataError = ref('')
const metadataTitle = ref('')
const metadataCanonicalTitle = ref('')
const metadataLegalLevel = ref('')
const metadataDocumentNumber = ref('')
const metadataIssuer = ref('')
const metadataDocumentVersion = ref('')
const metadataDepartment = ref('')
const metadataScope = ref('')
const metadataAdoptionDate = ref('')
const metadataPromulgationDate = ref('')
const metadataOriginalEffectiveDate = ref('')
const metadataRevisionDate = ref('')
const metadataCurrentVersionEffectiveDate = ref('')
const metadataExpiryDate = ref('')
const metadataApplicability = ref<LegalApplicability>({})

async function loadDocuments() {
  loading.value = true
  error.value = ''
  try {
    documents.value = await listKnowledge()
  } catch (reason) {
    error.value = apiErrorMessage(reason)
  } finally {
    loading.value = false
  }
}

async function loadDetail(document: KnowledgeListItem) {
  const detail = await getKnowledge(document.document_key)
  expandedDetail.value = detail
  legalUnits.value = detail.units
  return detail
}

async function toggleDocument(document: KnowledgeListItem) {
  if (expandedDoc.value?.document_key === document.document_key) {
    expandedDoc.value = undefined
    expandedDetail.value = undefined
    legalUnits.value = []
    return
  }
  expandedDoc.value = document
  expandedDetail.value = undefined
  legalUnits.value = []
  detailLoading.value = true
  error.value = ''
  try {
    await loadDetail(document)
  } catch (reason) {
    extractionError.value[document.document_key] = apiErrorMessage(reason)
  } finally {
    detailLoading.value = false
  }
}

function openUpload() {
  uploadFile.value = undefined
  uploadTitle.value = ''
  uploadIssuer.value = ''
  uploadDepartment.value = ''
  uploadDocumentVersion.value = ''
  uploadApplicableScope.value = ''
  uploadEffectiveDate.value = ''
  uploadExpiryDate.value = ''
  uploadError.value = ''
  uploadTask.value = undefined
  showUpload.value = true
}

function selectUploadFile(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0]
  uploadError.value = ''
  if (!file) return
  if (!/\.(pdf|doc|docx)$/i.test(file.name)) {
    uploadError.value = '仅支持 PDF、DOC、DOCX 文件。'
    return
  }
  uploadFile.value = file
}

async function submitUpload() {
  if (!uploadFile.value) { uploadError.value = '请选择法规文档。'; return }
  uploading.value = true
  uploadError.value = ''
  try {
    const task = await uploadKnowledgeDocument({
      file: uploadFile.value,
      title: uploadTitle.value.trim() || undefined,
      issuer: uploadIssuer.value.trim() || undefined,
      department: uploadDepartment.value.trim() || undefined,
      document_version: uploadDocumentVersion.value.trim() || undefined,
      applicable_scope: uploadApplicableScope.value.trim() || undefined,
      effective_date: uploadEffectiveDate.value || undefined,
      expiry_date: uploadExpiryDate.value || undefined,
    })
    uploadTask.value = task
    await pollUploadTask(task.id)
  } catch (reason) {
    uploadError.value = apiErrorMessage(reason)
  } finally {
    uploading.value = false
  }
}

async function pollUploadTask(taskId: string) {
  const tick = async () => {
    uploadTask.value = await getKnowledgeUploadTask(taskId)
    const task = uploadTask.value
    if (task.status === 'completed' && task.result) {
      uploadTask.value = { ...task, status: 'storing', progress: 90, message: '正在提取基本信息和适用范围' }
      await runExtraction(task.result, false)
      uploadTask.value = { ...task, progress: 100, message: '法规解析和候选提取已完成' }
      showUpload.value = false
      return
    }
    if (task.status === 'failed') {
      uploadError.value = task.error || '法规文档解析失败，请稍后重试。'
      return
    }
    uploadTimer = window.setTimeout(tick, 1000)
  }
  await tick()
}

async function retryUpload() {
  if (!uploadTask.value) return
  uploading.value = true
  uploadError.value = ''
  try {
    uploadTask.value = await retryKnowledgeUploadTask(uploadTask.value.id)
    await pollUploadTask(uploadTask.value.id)
  } catch (reason) {
    uploadError.value = apiErrorMessage(reason)
  } finally {
    uploading.value = false
  }
}

async function runExtraction(document: KnowledgeListItem, expandDocument = true) {
  extractingKey.value = document.document_key
  extractionError.value[document.document_key] = ''
  if (expandDocument) {
    expandedDoc.value = document
    expandedDetail.value = undefined
    legalUnits.value = []
  }
  try {
    const detail = await extractKnowledgeMetadata(document.document_key)
    if (expandDocument) {
      expandedDetail.value = detail
      legalUnits.value = detail.units
    }
    if (detail.metadata_extraction?.status === 'failed') {
      extractionError.value[document.document_key] = extractionWarningText(detail) || '自动提取失败，请稍后重试。'
      notice.value = '法规已入库，但自动提取未完成，可在当前列表重试。'
    } else if (detail.metadata_extraction?.status === 'pending_ai') {
      notice.value = extractionWarningText(detail) || '法规已完成本地筛选，正在等待 AI 服务配置。'
    } else {
      notice.value = '基本信息和适用范围候选已生成，请核对证据后确认发布。'
    }
  } catch (reason) {
    extractionError.value[document.document_key] = apiErrorMessage(reason)
    notice.value = '法规已入库，但自动提取未完成，可在当前列表重试。'
    if (expandDocument) {
      try { await loadDetail(document) } catch { /* 列表中的真实错误已足够提示 */ }
    }
  } finally {
    extractingKey.value = ''
    await loadDocuments()
  }
}

async function openMetadata(document: KnowledgeListItem) {
  metadataLoading.value = true
  metadataError.value = ''
  editingDetail.value = undefined
  editingDocument.value = document
  try {
    const detail = expandedDetail.value?.legal_document.document_key === document.document_key
      ? expandedDetail.value
      : await getKnowledge(document.document_key)
    editingDetail.value = detail
    const metadata = displayDocument(detail)
    metadataTitle.value = metadata.title || ''
    metadataCanonicalTitle.value = metadata.canonical_title || metadata.title || ''
    metadataLegalLevel.value = metadata.legal_level || ''
    metadataDocumentNumber.value = metadata.document_number || ''
    metadataIssuer.value = metadata.issuer || ''
    metadataDocumentVersion.value = metadata.document_version || ''
    metadataDepartment.value = metadata.department || ''
    metadataScope.value = metadata.applicability?.summary || metadata.applicable_scope || metadata.applicability?.activities?.map((item) => item.value).join('；') || ''
    metadataAdoptionDate.value = metadata.adoption_date || ''
    metadataPromulgationDate.value = metadata.promulgation_date || ''
    metadataOriginalEffectiveDate.value = metadata.original_effective_date || ''
    metadataRevisionDate.value = metadata.revision_date || ''
    metadataCurrentVersionEffectiveDate.value = metadata.current_version_effective_date || metadata.effective_date || ''
    metadataExpiryDate.value = metadata.expiry_date || ''
    metadataApplicability.value = JSON.parse(JSON.stringify(metadata.applicability || {})) as LegalApplicability
  } catch (reason) {
    metadataError.value = apiErrorMessage(reason)
  } finally {
    metadataLoading.value = false
  }
}

async function saveMetadata(status?: 'effective' | 'repealed') {
  const document = editingDocument.value
  const detail = editingDetail.value
  const metadataVersion = detail?.legal_document.metadata_version ?? document?.metadata_version
  if (!document || metadataVersion === undefined) {
    metadataError.value = '缺少文档元数据版本，暂不能保存。'
    return
  }
  if (status === 'effective' && detail?.metadata_extraction?.status !== 'ready') {
    metadataError.value = '自动提取候选尚未就绪，不能确认发布。'
    return
  }
  savingMetadata.value = true
  metadataError.value = ''
  try {
    await updateKnowledgeDocument(document.document_key, {
      metadata_version: metadataVersion,
      title: metadataTitle.value.trim() || undefined,
      canonical_title: metadataCanonicalTitle.value.trim() || undefined,
      legal_level: metadataLegalLevel.value.trim() || undefined,
      document_number: metadataDocumentNumber.value.trim() || undefined,
      issuer: metadataIssuer.value.trim() || undefined,
      document_version: metadataDocumentVersion.value.trim() || undefined,
      department: metadataDepartment.value.trim() || undefined,
      applicable_scope: metadataScope.value.trim() || undefined,
      adoption_date: metadataAdoptionDate.value || undefined,
      promulgation_date: metadataPromulgationDate.value || undefined,
      original_effective_date: metadataOriginalEffectiveDate.value || undefined,
      revision_date: metadataRevisionDate.value || undefined,
      current_version_effective_date: metadataCurrentVersionEffectiveDate.value || undefined,
      effective_date: metadataCurrentVersionEffectiveDate.value || undefined,
      expiry_date: metadataExpiryDate.value || undefined,
      applicability: { ...metadataApplicability.value, summary: metadataScope.value.trim() || undefined },
      status,
    })
    editingDocument.value = undefined
    editingDetail.value = undefined
    notice.value = status === 'effective' ? '法规已确认发布，可以参与后续新建审查。' : status === 'repealed' ? '法规已标记失效。' : '法规候选信息已保存。'
    await loadDocuments()
    if (expandedDoc.value?.document_key === document.document_key) await loadDetail(document)
  } catch (reason) {
    metadataError.value = apiErrorMessage(reason)
    if ((reason as { status?: number }).status === 409) await loadDocuments()
  } finally {
    savingMetadata.value = false
  }
}

const filteredUnits = computed(() => {
  const keyword = documentQuery.value.trim()
  if (!keyword) return legalUnits.value
  return legalUnits.value.filter((unit) => [unit.article_no, unit.chapter, unit.section, unit.text].some((value) => value?.includes(keyword)))
})

const visibleDocuments = computed(() => isAdmin.value ? documents.value : documents.value.filter((document) => document.status === 'effective'))
const candidateDocument = computed(() => editingDetail.value ? displayDocument(editingDetail.value) : undefined)
const candidateSummary = computed(() => {
  const document = candidateDocument.value
  if (!document) return ''
  if (document.applicability?.summary) return document.applicability.summary
  if (document.applicable_scope) return document.applicable_scope
  const values: string[] = []
  for (const group of applicabilityGroups) {
    for (const item of document.applicability?.[group.key] ?? []) {
      if (item.value && values.length < 3) values.push(item.value)
    }
    if (values.length === 3) break
  }
  return values.join('；') || '尚未形成适用范围摘要。'
})
const candidateTags = computed(() => {
  const document = candidateDocument.value
  if (!document) return []
  return [
    ['法律层级', document.legal_level],
    ['文号', document.document_number],
    ['制定机关', document.issuer],
  ].filter(([, value]) => Boolean(value)) as [string, string][]
})
const businessWarnings = computed(() => (editingDetail.value?.metadata_extraction?.warnings || [])
  .map(warningText)
  .filter((text) => !text.includes('legal_unit_id/quote pair') && !text.includes('INVALID_AI_EVIDENCE')))
const metadataConfirmed = computed(() => editingDetail.value?.metadata_extraction?.status === 'confirmed')

function statusLabel(value?: string) {
  if (value === 'effective') return '现行有效'
  if (value === 'repealed') return '已失效'
  return '待确认'
}
function legalLevelLabel(value?: string) {
  return ({ law: '法律', administrative_regulation: '行政法规', department_rule: '部门规章', local_regulation: '地方性法规', internal_policy: '内部制度' } as Record<string, string>)[value || ''] || '待确认'
}

function extractionLabel(value?: MetadataExtractionStatus) {
  if (value === 'pending_ai') return '等待 AI 提取'
  if (value === 'processing') return '正在提取'
  if (value === 'ready') return '候选待确认'
  if (value === 'failed') return '提取失败'
  if (value === 'confirmed') return '已确认发布'
  return '状态待同步'
}

function confidenceLabel(value?: number) {
  if (value === undefined) return '置信度未提供'
  const percent = value <= 1 ? Math.round(value * 100) : Math.round(value)
  return `置信度 ${percent}%`
}

function itemEvidence(item: ApplicabilityItem): ApplicabilityEvidence[] {
  return item.evidence || []
}

function evidenceLabel(evidence: ApplicabilityEvidence) {
  return evidence.article_no || legalUnits.value.find((unit) => unit.legal_unit_id === evidence.legal_unit_id)?.article_no || evidence.legal_unit_id
}

function warningText(warning: { code?: string; message?: string; field?: string; reason?: string } | string) {
  if (typeof warning === 'string') return warning
  return warning.message || [warning.field, warning.reason].filter(Boolean).join('：') || warning.code || '提取结果需要人工核对'
}

function extractionWarningText(detail: KnowledgeDetail) {
  return detail.metadata_extraction?.warnings?.map(warningText).join('；') || detail.metadata_extraction?.error || ''
}

function displayDocument(detail: KnowledgeDetail) {
  if (detail.metadata_extraction?.status === 'confirmed') return detail.legal_document
  return {
    ...detail.legal_document,
    ...detail.metadata_extraction?.basic_information,
    applicability: detail.metadata_extraction?.applicability || detail.legal_document.applicability,
  }
}

function fieldEvidence(field: string) {
  return expandedDetail.value?.metadata_extraction?.field_evidence?.[field] || []
}

function fieldEvidenceLabel(evidence: ApplicabilityEvidence | string) {
  return typeof evidence === 'string' ? evidence : evidenceLabel(evidence)
}

function extractionStatus(document: KnowledgeListItem) {
  if (extractingKey.value === document.document_key) return 'processing'
  if (expandedDoc.value?.document_key === document.document_key && expandedDetail.value?.metadata_extraction?.status) return expandedDetail.value.metadata_extraction.status
  return document.extraction_status
}

onMounted(() => { void loadDocuments() })
</script>

<template>
  <div class="knowledge-view">
    <div class="page-head">
      <div class="crumb">知识资产 / 知识库</div>
      <div class="page-title-row"><h2>知识库</h2></div>
      <p>法规原文经规则筛选、分段提取与证据校验形成候选信息，由管理员确认发布后参与审查。</p>
    </div>

    <div class="page-body">
      <div class="tabs" role="tablist" aria-label="知识库层级">
        <button class="tab" :class="{ act: kbtab === 'docs' }" role="tab" :aria-selected="kbtab === 'docs'" @click="kbtab = 'docs'">文档层</button>
        <button class="tab" :class="{ act: kbtab === 'rules' }" role="tab" :aria-selected="kbtab === 'rules'" @click="kbtab = 'rules'">规则库</button>
        <button class="tab" :class="{ act: kbtab === 'biz' }" role="tab" :aria-selected="kbtab === 'biz'" @click="kbtab = 'biz'">业务知识库</button>
      </div>

      <section v-if="kbtab === 'docs'" class="knowledge-section" aria-label="法规文档">
        <div class="knowledge-toolbar">
          <span v-if="notice" class="knowledge-notice">{{ notice }}</span>
          <span v-else-if="isAdmin" class="knowledge-role-hint">系统管理员：负责文档入库、候选核验与发布。</span>
          <span v-else class="knowledge-role-hint">{{ isSupervisor ? '专业监督：仅查看已发布知识，纠偏请前往强制纠偏。' : '业务经办：仅可查看已发布知识。' }}</span>
          <button v-if="isAdmin" class="btn pri" type="button" @click="openUpload">上传法规文档</button>
        </div>
        <div v-if="loading" class="state-card">正在加载法规文档…</div>
        <div v-else-if="error" class="state-card error-state">{{ error }}<button class="btn inline-retry" @click="loadDocuments">重试</button></div>
        <template v-else>
          <div class="note">上传后先形成待确认候选：本地规则召回条款，AI 分段提取基本信息与适用范围，再校验证据。未确认文档不参与审查，也不等同于已发布执行规则。</div>
          <div class="knowledge-stats"><span>已入库文档 <strong>{{ visibleDocuments.length }}</strong></span><span>可展开查看解析条款</span></div>
          <div v-if="!visibleDocuments.length" class="empty knowledge-empty">暂无可查看的法规文档</div>
          <div v-for="document in visibleDocuments" :key="document.document_key" class="doc-wrap">
            <article class="kfile doc-layer-file">
              <span class="kf-ic" aria-hidden="true">§</span>
              <div class="document-main">
                <div class="kn">{{ document.canonical_title || document.title }}</div>
                <div class="km">{{ document.article_count ?? '待统计' }} 条 · {{ document.unit_count ?? '待统计' }} 个检索单元 · {{ document.document_version || '版本待维护' }}</div>
                <div class="doc-meta-row">
                  <span class="chip">{{ statusLabel(document.status) }}</span>
                  <span class="chip">归口：{{ document.department || '未标注' }}</span>
                  <span class="chip">适用：{{ document.applicable_scope || '待确认' }}</span>
                </div>
              </div>
              <button class="btn" @click="toggleDocument(document)">{{ expandedDoc?.document_key === document.document_key ? '收起条款' : '查看条款' }}</button>
              <button v-if="isAdmin" class="btn" type="button" :disabled="metadataLoading" @click="openMetadata(document)">维护信息</button>
            </article>
            <p v-if="isAdmin && extractionError[document.document_key]" class="document-error">{{ extractionError[document.document_key] }}</p>

            <div v-if="expandedDoc?.document_key === document.document_key" class="document-detail">
              <p v-if="detailLoading || extractingKey === document.document_key" class="state-card">正在加载解析条款…</p>
              <template v-else-if="expandedDetail">
                <div class="unit-head"><b>解析条款</b><span>{{ legalUnits.length }} 个检索单元</span></div>
                <label class="sr-only" for="unit-query">条款筛选</label>
                <input id="unit-query" v-model="documentQuery" class="kb-search unit-search" placeholder="按条号、章节或关键词筛选条款" />
                <p v-if="!filteredUnits.length" class="empty knowledge-empty">没有匹配的条款</p>
                <article v-for="unit in filteredUnits" :key="unit.legal_unit_id" class="rule">
                  <div class="rule-heading"><span class="rid">{{ unit.article_no }}</span><strong>{{ unit.chapter || unit.section || '未标注章节' }}</strong></div>
                  <p class="rd">{{ unit.text }}</p>
                  <div class="rmeta"><span class="chip">{{ unit.unit_type }}</span><span v-if="unit.evidence[0]?.page_no" class="chip">第 {{ unit.evidence[0].page_no }} 页</span></div>
                </article>
              </template>
            </div>
          </div>
        </template>
      </section>

      <RuleGovernancePanel v-else-if="kbtab === 'rules'" />
      <section v-else class="empty knowledge-empty unavailable"><div>业务知识库暂未开放</div><p>业务术语、历史项目文件和内部口径尚未纳入本期开放范围。</p></section>
    </div>
  </div>

  <BaseModal v-if="showUpload" title="上传法规文档" @close="!uploading && (showUpload = false)">
    <form @submit.prevent="submitUpload">
      <p class="note">上传后自动执行“规则筛选 + 分段提取 + 汇总校验”，形成待管理员确认的候选，不会直接参与审查。</p>
      <div class="field"><label>法规文档 <b class="required">*</b></label><input type="file" accept=".pdf,.doc,.docx" :disabled="uploading" @change="selectUploadFile" /><small>{{ uploadFile?.name || '支持 PDF、DOC、DOCX。' }}</small></div>
      <div class="field"><label>标题</label><input v-model="uploadTitle" :disabled="uploading" placeholder="可留空，由解析结果补充" /></div>
      <div class="upload-grid"><div class="field"><label>发布机关</label><input v-model="uploadIssuer" :disabled="uploading" /></div><div class="field"><label>归口部门</label><input v-model="uploadDepartment" :disabled="uploading" /></div></div>
      <div class="upload-grid"><div class="field"><label>文档版本</label><input v-model="uploadDocumentVersion" :disabled="uploading" /></div><div class="field"><label>适用范围（可选人工补充）</label><input v-model="uploadApplicableScope" :disabled="uploading" /></div><div class="field"><label>生效日期</label><input v-model="uploadEffectiveDate" type="date" :disabled="uploading" /></div><div class="field"><label>失效时间</label><input v-model="uploadExpiryDate" type="date" :disabled="uploading" /></div></div>
      <div v-if="uploadTask" class="upload-progress">
        <div class="progress"><i :style="{ width: `${uploadTask.progress}%` }"></i></div>
        <div class="upload-progress-meta"><span>{{ uploadTask.message || (uploadTask.status === 'retrying' ? '解析失败，正在自动重试…' : '正在处理法规文档…') }}</span><b>{{ uploadTask.progress }}%</b></div>
        <small v-if="uploadTask.status === 'failed' || uploadTask.status === 'retrying'">手动重试 {{ uploadTask.retry_count }} / {{ uploadTask.max_retries }} 次</small>
      </div>
      <p v-else class="upload-pending">解析和 AI 提取可能需要一些时间，提交后进度与错误均在文档列表原地显示。</p>
      <p v-if="uploadTask?.status === 'failed'" class="upload-error">解析失败：{{ uploadTask.error || '未知错误' }}</p>
      <p v-if="uploadError" class="upload-error">{{ uploadError }}</p>
      <div class="modal-foot"><button class="btn" type="button" :disabled="uploading" @click="showUpload = false">取消</button><button v-if="uploadTask?.status === 'failed'" class="btn pri" type="button" :disabled="uploading" @click="retryUpload">{{ uploading ? '正在重试…' : '手动重试' }}</button><button v-else class="btn pri" :disabled="uploading">{{ uploading ? '正在解析入库…' : '提交并自动提取' }}</button></div>
    </form>
  </BaseModal>

  <BaseModal v-if="editingDocument" title="维护信息" wide @close="!savingMetadata && (editingDocument = undefined)">
    <div v-if="metadataLoading" class="state-card">正在加载候选和原文证据…</div>
    <form v-else-if="editingDetail" class="metadata-editor" @submit.prevent="saveMetadata()">
      <p class="note"><b>Agent 生成候选 → 人工核对编辑 → 确认发布</b><br>{{ editingDocument?.title }} · 原始文件不可覆盖，候选及证据仅在此弹窗内核对和维护。</p>
      <section v-if="editingDetail.metadata_extraction?.status !== 'confirmed'" class="candidate-panel">
        <div class="candidate-head"><div><span class="candidate-kicker">Agent 生成候选</span><h3>{{ candidateDocument?.canonical_title || candidateDocument?.title }}</h3></div><span class="chip extraction-chip" :class="editingDetail.metadata_extraction?.status">{{ extractionLabel(editingDetail.metadata_extraction?.status) }}</span></div>
        <div v-if="businessWarnings.length" class="metadata-warnings"><b>需要人工确认</b><span v-for="warning in businessWarnings" :key="warning">{{ warning }}</span></div>
        <p class="candidate-summary">{{ candidateSummary }}</p>
        <div v-if="candidateTags.length" class="candidate-tags"><span v-for="[label, value] in candidateTags" :key="label" class="chip">{{ label }}：{{ value }}</span></div>
      </section>
      <section v-else class="confirmed-summary"><b>已确认发布</b><p>法规元数据已确认，当前已作为现行有效法规参与后续审查。</p></section>
      <details v-if="editingDetail.metadata_extraction?.audit" class="audit-panel"><summary>查看解析调用记录</summary><div class="audit-line"><b>原文解析</b><span>{{ editingDetail.metadata_extraction.audit.parser?.tool }} → {{ editingDetail.metadata_extraction.audit.parser?.output }}</span></div><div v-for="call in editingDetail.metadata_extraction.audit.calls" :key="call.file" class="audit-call"><div><b>{{ call.status === 'success' ? '成功' : '失败' }}</b><span>{{ call.file }} · {{ call.model || '未记录模型' }}</span></div><div>Skill：{{ call.skill }}　Tool：{{ call.tool }}</div><div>输入：{{ call.input_summary }}</div><div>输出：{{ call.output_summary }}</div><div v-if="call.error" class="audit-error">错误：{{ call.error }}</div></div></details>
      <div class="field"><label>法规正式名称 <small>{{ metadataConfirmed ? '已发布法规，可维护' : '解析候选，确认后作为列表主标题' }}</small></label><input v-model="metadataCanonicalTitle" :disabled="savingMetadata" /></div>
      <div class="upload-grid"><div class="field"><label>法律层级</label><select v-model="metadataLegalLevel" :disabled="savingMetadata"><option value="">请选择</option><option value="law">法律</option><option value="administrative_regulation">行政法规</option><option value="department_rule">部门规章</option><option value="local_regulation">地方性法规</option><option value="internal_policy">内部制度</option><option value="other">其他</option></select></div><div class="field"><label>文号 / 令号</label><input v-model="metadataDocumentNumber" :disabled="savingMetadata" /></div></div>
      <div class="upload-grid"><div class="field"><label>制定机关</label><input v-model="metadataIssuer" :disabled="savingMetadata" /></div><div class="field"><label>归口部门</label><input v-model="metadataDepartment" :disabled="savingMetadata" /></div></div>
      <div class="upload-grid"><div class="field"><label>文档版本</label><input v-model="metadataDocumentVersion" :disabled="savingMetadata" /></div><div class="field"><label>通过日期</label><input v-model="metadataAdoptionDate" type="date" :disabled="savingMetadata" /></div></div>
      <div class="upload-grid"><div class="field"><label>公布日期</label><input v-model="metadataPromulgationDate" type="date" :disabled="savingMetadata" /></div><div class="field"><label>原始施行日期</label><input v-model="metadataOriginalEffectiveDate" type="date" :disabled="savingMetadata" /></div></div>
      <div class="upload-grid"><div class="field"><label>最近修订日期</label><input v-model="metadataRevisionDate" type="date" :disabled="savingMetadata" /></div><div class="field"><label>当前版本施行日期</label><input v-model="metadataCurrentVersionEffectiveDate" type="date" :disabled="savingMetadata" /></div></div>
      <div class="field"><label>失效日期</label><input v-model="metadataExpiryDate" type="date" :disabled="savingMetadata" /></div>
      <div class="field"><label>适用范围摘要</label><textarea v-model="metadataScope" :disabled="savingMetadata" /></div>
      <section v-if="editingDetail.metadata_extraction?.status !== 'confirmed'" class="edit-scope">
        <div class="scope-group-title">结构化适用范围 <span>修改内容不删除提取证据</span></div>
        <template v-for="group in applicabilityGroups" :key="group.key">
          <div v-if="metadataApplicability[group.key]?.length" class="field"><label>{{ group.label }}</label><div v-for="(item, index) in metadataApplicability[group.key]" :key="`${group.key}-${index}`" class="candidate-edit-row"><input v-model="item.value" :disabled="savingMetadata" /><span class="chip">{{ confidenceLabel(item.confidence) }}</span><details v-if="item.evidence?.length"><summary>{{ item.evidence.length }} 条证据</summary><p v-for="evidence in item.evidence" :key="`${evidence.legal_unit_id}-${evidence.quote}`">{{ evidenceLabel(evidence) }}：{{ evidence.quote }}</p></details></div></div>
        </template>
      </section>
      <p v-if="metadataError" class="upload-error">{{ metadataError }}</p>
      <div class="modal-foot"><button class="btn" type="button" :disabled="savingMetadata" @click="editingDocument = undefined">取消</button><button v-if="metadataConfirmed" class="btn pri" type="submit" :disabled="savingMetadata">{{ savingMetadata ? '正在保存…' : '保存维护信息' }}</button><button v-else class="btn" type="submit" :disabled="savingMetadata">{{ savingMetadata ? '正在保存…' : '保存候选' }}</button><button v-if="editingDocument.status === 'effective'" class="btn" type="button" :disabled="savingMetadata" @click="saveMetadata('repealed')">标记失效</button><button v-if="(editingDocument.status ?? 'unknown') === 'unknown'" class="btn pri" type="button" :disabled="savingMetadata || editingDetail.metadata_extraction?.status !== 'ready'" @click="saveMetadata('effective')">确认并发布</button></div>
      <p v-if="editingDetail.metadata_extraction?.status !== 'ready' && editingDocument.status !== 'effective'" class="publish-hint">请先核对 Agent 候选信息，再点击“确认并发布”。该操作会同时保存信息并使法规生效。</p>
    </form>
    <p v-if="metadataError && !editingDetail" class="upload-error">{{ metadataError }}</p>
  </BaseModal>
</template>

<style scoped>
.knowledge-section { min-height:320px; }
.knowledge-toolbar { display:flex; align-items:center; justify-content:space-between; gap:12px; min-height:34px; margin:0 0 10px; }
.knowledge-notice { color:var(--olive); font-size:11px; }
.knowledge-role-hint { color:var(--terra); font-size:11px; font-weight:700; }
.knowledge-stats { display:flex; gap:18px; align-items:center; margin:0 0 14px; padding:10px 13px; border:1px solid var(--border); border-radius:10px; background:var(--white); color:var(--stone); font-size:11px; }
.knowledge-stats strong { color:var(--terra); font:600 16px var(--serif); }
.note { margin:0 0 14px; }
.doc-wrap + .doc-wrap { margin-top:10px; }
.document-main { flex:1; min-width:0; }
.document-detail { padding:12px 0 0 15px; border-left:1px solid var(--border); }
.document-error { margin:8px 0; padding:9px 11px; border:1px solid #e8c1bc; border-radius:8px; background:#fff7f5; color:var(--crimson); font-size:11px; line-height:1.6; }
.inline-retry { margin-left:12px; }
.extraction-chip.processing, .extraction-chip.pending_ai { color:var(--ochre); background:var(--ochre-soft); }
.extraction-chip.failed { color:var(--crimson); background:var(--crimson-soft); }
.extraction-chip.ready { color:var(--terra); background:var(--terra-soft); }
.extraction-chip.confirmed { color:var(--green); background:var(--green-soft); }
.candidate-panel { padding:16px; margin-bottom:16px; border:1px solid rgba(171,114,86,.35); border-left:3px solid var(--terra); border-radius:12px; background:#fffaf7; }
.candidate-panel.published { border-color:rgba(72,128,89,.3); border-left-color:var(--green); background:#fbfdf9; }
.candidate-head { display:flex; align-items:flex-start; justify-content:space-between; gap:12px; }
.candidate-head h3 { margin:4px 0 0; font:16px var(--serif); color:var(--ink); }
.candidate-kicker { color:var(--terra); font-size:10px; font-weight:800; letter-spacing:.08em; }
.candidate-summary { display:-webkit-box; overflow:hidden; margin:12px 0 0; color:var(--olive); font-size:11.5px; line-height:1.7; -webkit-box-orient:vertical; -webkit-line-clamp:3; }
.candidate-tags { display:flex; flex-wrap:wrap; gap:6px; margin-top:9px; }
.extraction-details { margin-top:13px; border-top:1px solid var(--border); }
.extraction-details > summary { padding-top:11px; color:var(--terra); cursor:pointer; font-size:11px; font-weight:700; }
.extraction-details[open] > summary { margin-bottom:2px; }
.field-evidence { display:block; margin-top:3px; color:var(--terra); font-size:9px; font-weight:500; }
.metadata-warnings { display:grid; gap:4px; margin:12px 0; padding:10px 12px; border-radius:8px; background:var(--ochre-soft); color:var(--ochre); font-size:11px; }
.confirmed-summary { margin-bottom:14px; padding:12px 14px; border:1px solid rgba(72,128,89,.3); border-left:3px solid var(--green); border-radius:9px; background:#fbfdf9; color:var(--olive); font-size:11px; }
.confirmed-summary b { color:var(--green); }
.confirmed-summary p { margin:5px 0 0; }
.metadata-grid { display:grid; grid-template-columns:repeat(3,1fr); gap:1px; margin:14px 0; overflow:hidden; border:1px solid var(--border); border-radius:9px; background:var(--border); }
.metadata-grid div { padding:9px 10px; background:var(--white); }
.metadata-grid dt { color:var(--stone); font-size:9.5px; }
.metadata-grid dd { margin:3px 0 0; color:var(--ink); font-size:11.5px; }
.scope-summary { padding:11px 12px; border-radius:9px; background:var(--ivory); color:var(--olive); font-size:11.5px; line-height:1.65; }
.scope-summary p { margin:4px 0 0; }
.scope-groups { display:grid; grid-template-columns:1fr 1fr; gap:10px; margin-top:12px; }
.scope-group { padding:11px; border:1px solid var(--border); border-radius:9px; background:var(--white); }
.scope-group-title { display:flex; justify-content:space-between; gap:8px; margin-bottom:8px; color:var(--ink); font-size:11px; font-weight:700; }
.scope-group-title span { color:var(--stone); font-weight:400; }
.scope-empty { margin:0; color:var(--stone); font-size:10.5px; }
.scope-item + .scope-item { margin-top:8px; padding-top:8px; border-top:1px dashed var(--border); }
.scope-value { display:flex; align-items:flex-start; justify-content:space-between; gap:8px; color:var(--olive); font-size:11px; }
.scope-value b { color:var(--ink); font-weight:600; }
.evidence-list { margin-top:6px; color:var(--terra); font-size:10px; }
.evidence-list summary { cursor:pointer; }
.evidence-list blockquote { margin:7px 0 0; padding:7px 9px; border-left:2px solid var(--terra); background:var(--ivory); color:var(--stone); }
.evidence-list blockquote p { margin:4px 0 0; color:var(--olive); line-height:1.55; }
.unit-head { display:flex; justify-content:space-between; margin:16px 0 8px; color:var(--ink); font-size:11px; }
.unit-head span { color:var(--stone); }
.kb-search { width:100%; padding:7px 11px; border:1px solid var(--border); border-radius:8px; background:var(--white); color:var(--ink); font:12px var(--sans); outline:none; }
.kb-search:focus { border-color:var(--terra); box-shadow:0 0 0 2px rgba(171,114,86,.12); }
.unit-search { margin-bottom:10px; }
.field { display:flex; flex-direction:column; gap:5px; margin-bottom:12px; font-size:12px; color:var(--ink); }
.field input, .field select, .field textarea { min-height:34px; padding:7px 9px; border:1px solid var(--border); border-radius:7px; background:var(--white); color:var(--ink); font:12px var(--sans); }
.field textarea { min-height:76px; resize:vertical; }
.field small { color:var(--stone); font-size:10px; }
.required, .upload-error { color:var(--crimson); }
.upload-error { margin:0 0 12px; font-size:11px; }
.upload-pending, .publish-hint { margin:0 0 12px; padding:9px 11px; border:1px solid var(--border); border-radius:8px; background:var(--ivory); color:var(--olive); font-size:11px; line-height:1.6; }
.upload-progress { margin:0 0 12px; padding:11px; border:1px solid var(--border); border-radius:8px; background:var(--ivory); }
.upload-progress-meta { display:flex; justify-content:space-between; margin-top:7px; color:var(--olive); font-size:11px; }
.upload-progress-meta b { color:var(--terra); }
.upload-progress small { display:block; margin-top:6px; color:var(--stone); }
.audit-panel { margin:12px 0; padding:11px 12px; border:1px solid var(--border); border-radius:9px; background:var(--ivory); color:var(--olive); font-size:10.5px; line-height:1.6; }
.audit-panel summary { color:var(--terra); cursor:pointer; font-weight:700; }
.audit-line, .audit-call { margin-top:8px; padding-top:8px; border-top:1px dashed var(--border); }
.audit-line span, .audit-call span { margin-left:8px; color:var(--stone); }
.audit-error { color:var(--crimson); word-break:break-word; }
.upload-grid { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
.edit-scope { margin-top:6px; padding-top:12px; border-top:1px solid var(--border); }
.candidate-edit-row { display:grid; grid-template-columns:minmax(0,1fr) auto; gap:6px; align-items:center; }
.candidate-edit-row + .candidate-edit-row { margin-top:7px; }
.candidate-edit-row details { grid-column:1/-1; color:var(--terra); font-size:10px; }
.candidate-edit-row details p { margin:5px 0; padding:6px 8px; border-left:2px solid var(--terra); background:var(--ivory); color:var(--olive); line-height:1.5; }
.metadata-editor { display:grid; grid-template-columns:1fr 1fr; gap:0 14px; }
.metadata-editor > .note,.metadata-editor > .candidate-panel,.metadata-editor > .confirmed-summary,.metadata-editor > .audit-panel,.metadata-editor > .field,.metadata-editor > .edit-scope,.metadata-editor > .upload-error,.metadata-editor > .modal-foot,.metadata-editor > .publish-hint { grid-column:1/-1; }
.metadata-editor > .note { padding:13px 15px; border:1px solid var(--border); border-radius:10px; background:var(--ivory); line-height:1.65; }
.metadata-editor .candidate-panel,.metadata-editor .confirmed-summary { margin-bottom:14px; }
.metadata-editor > .field { padding:12px 14px; margin-bottom:10px; border:1px solid var(--border); border-radius:10px; background:var(--white); }
.metadata-editor > .upload-grid { margin-bottom:10px; padding:12px 14px 0; border:1px solid var(--border); border-radius:10px; background:var(--white); }
.metadata-editor .field label { display:flex; align-items:center; justify-content:space-between; gap:8px; font-weight:700; }
.metadata-editor .field label small { font-weight:400; }
.metadata-editor .edit-scope { margin-top:4px; padding:15px; border:1px solid var(--border); border-radius:10px; background:var(--ivory); }
.metadata-editor .modal-foot { position:sticky; bottom:-20px; z-index:3; margin:12px -20px -20px; background:rgba(255,255,255,.96); box-shadow:0 -8px 18px rgba(35,40,36,.06); backdrop-filter:blur(8px); }
.rule-heading { display:flex; align-items:center; gap:8px; min-width:0; }
.rule-heading strong { min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:12px; color:var(--ink); }
.rd { margin:9px 0; line-height:1.75; }
.knowledge-empty { min-height:260px; display:grid; place-items:center; color:var(--stone); font-size:12px; }
.unavailable { align-content:center; text-align:center; }
.unavailable div { font:20px var(--serif); color:var(--ink); }
.unavailable p { max-width:470px; margin:7px auto 0; line-height:1.75; }
.sr-only { position:absolute; width:1px; height:1px; padding:0; margin:-1px; overflow:hidden; clip:rect(0,0,0,0); white-space:nowrap; border:0; }
@media (max-width:800px) { .metadata-grid, .scope-groups { grid-template-columns:1fr; } }
@media (max-width:680px) { .knowledge-toolbar, .knowledge-stats { align-items:flex-start; flex-direction:column; gap:7px; } .upload-grid,.metadata-editor { grid-template-columns:1fr; } .doc-layer-file { align-items:flex-start; flex-wrap:wrap; } }
</style>
