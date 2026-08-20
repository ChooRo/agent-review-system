<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { apiErrorMessage } from '../../api'
import { getDebugTraces } from '../../api/procurement-review'
import type { DebugStage, DebugTrace } from '../../api/procurement-review'

const route = useRoute()
const router = useRouter()
const trace = ref<DebugTrace>()
const loading = ref(true)
const error = ref('')
const selected = ref('')
const blockType = ref('all')
const expandedBlock = ref('')
const expandedTrace = ref('')
const expandedUnit = ref('')
const expandedBatch = ref<number | null>(null)
const projectId = String(route.params.projectId)
const taskId = String(route.params.taskId)

const stages = computed(() => trace.value?.stage_results ?? [])
const active = computed(() => stages.value.find(stage => stage.key === selected.value) || stages.value[0])
const activeData = computed<any>(() => active.value?.data ?? {})
const procurementLedger = computed<any>(() => activeData.value.ledgers?.procurement ?? {})
const parseBlocks = computed<any[]>(() => list(activeData.value.documents?.procurement?.blocks))
const blockTypes = computed(() => [...new Set(parseBlocks.value.map(block => String(block.block_type)).filter(Boolean))])
const visibleBlocks = computed(() => parseBlocks.value.filter(block => blockType.value === 'all' || block.block_type === blockType.value))
const logicalUnits = computed<any[]>(() => list(activeData.value.manifests?.procurement?.units))

async function load() {
  loading.value = true
  error.value = ''
  try {
    trace.value = await getDebugTraces(projectId, taskId)
    if (!stages.value.some(stage => stage.key === selected.value)) selected.value = stages.value[0]?.key || ''
  } catch (reason) {
    error.value = apiErrorMessage(reason)
  } finally {
    loading.value = false
  }
}

function list(value: any): any[] { return Array.isArray(value) ? value : [] }
function entries(value: any): [string, any][] { return Object.entries(value || {}) }
function named(value: any) { return entries(value).map(([name, item]) => ({ name, value: item })) }
function typeName(type: string) { return ({ heading: '标题', paragraph: '正文', table: '表格', image: '图片', header: '页眉', footer: '页脚', page_number: '页码' } as Record<string, string>)[type] || type || '内容块' }
function unitName(type: string) { return ({ section_unit: '章节单元', clause_unit: '条款单元', table_unit: '表格单元', figure_unit: '图片单元', attachment_unit: '附件单元', paragraph_unit: '段落单元' } as Record<string, string>)[type] || type }
function qualityName(status: string) { return ({ passed: '通过', degraded: '降级继续', retryable: '需要重试', unreliable: '不可靠，已阻断' } as Record<string, string>)[status] || status || '未知' }
function ocrStatusName(status: string) { return ({ available: '已评估', low_confidence: '低置信度', unavailable: '已执行但无置信度', not_assessed: '未评估' } as Record<string, string>)[status] || status || '未知' }
function relationName(type: string) { return ({ duplicate: '重复', equivalent: '等价', supplementary: '补充', conflicting: '冲突', uncertain: '不确定' } as Record<string, string>)[type] || type }
function validationErrors(finding: any) { return list(finding.evidence_validation?.errors) }
function display(value: unknown) { return typeof value === 'string' ? value : JSON.stringify(value, null, 2) }
function aiTraceTitle(key: string) { return ({ semantic_structure: '疑难结构理解', business_understanding: '分批业务理解', professional_review: '专业审查判断' } as Record<string, string>)[key] || '模型调用' }
function json(value: unknown): any { if (typeof value !== 'string') return value || {}; try { return JSON.parse(value) } catch { return value } }
function callInput(call: any) { const messages = list(json(call.request)?.messages); const content = [...messages].reverse().find(message => message.role === 'user')?.content; return json(content) }
function callOutput(call: any) { return json(call.response ?? call.error ?? {}) }
function inputBlocks(call: any) {
  return list(callInput(call)?.blocks).map(block => ({
    ...block,
    block_id: block.block_id || block.id,
    type: block.type || block.t,
    page: block.page ?? block.p,
    text: block.text ?? block.x,
  }))
}
function outputItems(call: any) {
  const value = callOutput(call)
  return list(value?.candidate_items || value?.findings || value?.section_responsibilities || value?.sections).filter(item => {
    if (!item || typeof item !== 'object') return false
    const candidate = item.candidate_id || item.requirement_type || item.primary_category
    if (!candidate) return Boolean(item.title || item.heading || item.responsibility)
    const statement = String(item.statement || '').trim()
    return statement.length >= 6 && !/(?:并在|以及|并且|且|并|或|在|为|符合|标识|包括|如下|下列|[：:、，,])$/.test(statement)
      && Boolean(item.evidence_quote) && list(item.evidence_block_ids).length > 0
  })
}
function itemTitle(item: any) { return item.statement || item.title || item.heading || item.responsibility || '未命名结果' }
function itemCategory(item: any) { return item.primary_category || item.finding_type || item.category || item.section_type || '识别结果' }
function modelName(call: any) { return json(call.request)?.model || '未记录' }
function blockTypeLabel(type: string) { return ({ paragraph: '正文', heading: '标题', table: '表格', image: '图片' } as Record<string, string>)[type] || type || '原文' }
function cleanText(value: unknown) { return String(value || '无文本').replace(/<[^>]+>/g, ' ').replace(/\*\*/g, '').replace(/\s+/g, ' ').trim() }
function tableHtml(block: any) {
  const text = String(block?.text || '').trim()
  return block?.table_html || block?.source?.table_html || (text.toLowerCase().startsWith('<table') ? text : '')
}
function safeTableHtml(value: unknown) {
  const document = new DOMParser().parseFromString(String(value || ''), 'text/html')
  document.querySelectorAll('script,style,iframe,object,embed').forEach(node => node.remove())
  document.querySelectorAll('*').forEach(node => [...node.attributes].forEach(attribute => {
    if (!['rowspan', 'colspan'].includes(attribute.name.toLowerCase())) node.removeAttribute(attribute.name)
  }))
  const table = document.querySelector('table')
  if (!table) return ''
  const header = [...table.querySelectorAll(':scope > tr, :scope > tbody > tr')].find(row => {
    const text = row.textContent?.replace(/\s+/g, '') || ''
    return text.includes('条款号') && text.includes('条款名称') && text.includes('编列内容')
  })
  if (header && header.parentElement?.tagName !== 'THEAD') {
    const thead = document.createElement('thead')
    header.querySelectorAll(':scope > td').forEach(cell => {
      const th = document.createElement('th')
      for (const attribute of [...cell.attributes]) th.setAttribute(attribute.name, attribute.value)
      th.innerHTML = cell.innerHTML
      cell.replaceWith(th)
    })
    thead.append(header)
    table.prepend(thead)
  }
  return table.outerHTML
}
function ruleLibraryLabel(status: string) { return ({ loaded: '已加载', degraded: '加载异常', not_configured: '未配置' } as Record<string, string>)[status] || status || '未知' }
function primaryBlocks(value: any) { return list(value?.blocks).filter(block => block.role === 'primary') }
function unitTitle(unit: any) { return cleanText(unit.heading_path?.at(-1) || primaryBlocks(unit)[0]?.text || '无可显示内容') }
function batchBlocks(batch: any) { return primaryBlocks(batch).length ? primaryBlocks(batch) : list(batch?.blocks) }
function systemPrompt(call: any) { return list(json(call.request)?.messages).find(message => message.role === 'system')?.content || '未记录 System Prompt' }
function outputCoverage(call: any) { return list(callOutput(call)?.coverage || callOutput(call)?.coverage_summary) }
function outputRejected(call: any) { return list(callOutput(call)?.rejected_items || callOutput(call)?.unresolved) }
function outputWarnings(call: any) { return list(callOutput(call)?.warnings) }
function promptLength(call: any) { return String(systemPrompt(call)).length }
function payloadLength(call: any) { return JSON.stringify(callInput(call) || {}).length }
const legalFactLabels: Record<string, string> = {
  project_type: '采购项目类型', procurement_method: '采购方式', is_government_procurement: '是否属于政府采购',
  is_engineering_related: '是否与工程建设有关', is_mandatory_tender: '是否属于依法必须招标', region: '项目所在地区', review_stage: '当前业务阶段',
}
const legalFactValues: Record<string, string> = {
  goods: '货物采购', services: '服务采购', engineering: '工程采购', unknown: '尚未可靠识别', yes: '是', no: '否',
  open_tender: '公开招标', invited_tender: '邀请招标', competitive_negotiation: '竞争性谈判', inquiry: '询价', single_source: '单一来源',
  procurement_document_review: '采购文件审查',
}
function legalFactLabel(value: string) { return legalFactLabels[value] || value }
function legalFactValue(value: unknown) { return legalFactValues[String(value)] || String(value ?? '尚未可靠识别') }
function factEvidence() {
  const evidence = activeData.value.task_legal_facts?.evidence || {}
  return Object.entries(evidence).flatMap(([field, sources]: [string, any]) => list(sources).map(source => ({ field, ...source })))
}
function toggleBatch(batchNo: unknown) {
  const value = typeof batchNo === 'number' ? batchNo : null
  expandedBatch.value = expandedBatch.value === value ? null : value
}
function selectStage(key: string) { selected.value = key }

function count(stage: DebugStage) {
  const current: any = stage.data || {}
  if (stage.key === 'mineru_parse') return `${current.documents?.procurement?.blocks?.length || 0} 个原子 Block`
  if (stage.key === 'quality_check') return qualityName(current.quality?.procurement?.status)
  if (stage.key === 'deterministic_structure') return `${current.profiles?.procurement?.outline?.length || 0} 个结构节点`
  if (stage.key === 'semantic_structure') return `${current.profiles?.procurement?.section_responsibilities?.length || 0} 个章节职责`
  if (stage.key === 'logical_units') return `${current.manifests?.procurement?.units?.length || 0} 个逻辑单元`
  if (stage.key === 'review_batches') return `${stage.batches?.length || 0} 批 · ${stage.validation?.status === 'passed' ? '校验通过' : stage.validation?.status || '待校验'}`
  if (stage.key === 'business_understanding') return `${stage.batches?.length || 0} 批 · ${current.candidates?.procurement?.length || 0} 条候选`
  if (stage.key === 'global_ledger') {
    const ledger = current.ledgers?.procurement
    if (Array.isArray(ledger)) return `${ledger.length} 条历史台账`
    return `${ledger?.source_assertions?.length || 0} 条断言 · ${ledger?.business_item_clusters?.length || 0} 个事项簇`
  }
  if (stage.key === 'legal_facts') return `${current.missing_facts?.length || 0} 项事实待补充`
  if (stage.key === 'legal_candidates') return `${current.rules?.length || 0} 条可执行规则 · 实际匹配 ${current.matched_count || 0} 条`
  if (stage.key === 'legal_applicability_gate') {
    const decisions = list(current.decisions)
    return `${decisions.filter(item => item.status === 'applicable').length} 份适用 · ${decisions.filter(item => ['potential', 'insufficient_facts'].includes(item.status)).length} 份待确认`
  }
  if (stage.key === 'professional_review') return `${stage.tools?.filter(tool => tool.triggered).length || 0} 类 Tool · ${current.findings?.length || 0} 个问题`
  if (stage.key === 'evidence_validation') return `${current.verified_count || 0} 已验证 · ${current.insufficient_count || 0} 待人工`
  return stage.title
}

onMounted(load)
</script>

<template>
  <div class="debug-page">
    <header>
      <div><span class="eyebrow">PROCESS RESULTS</span><h1>逐阶段审查结果</h1><p>查看采购文件十层流程的真实输入、产物、质量门禁和证据校验。</p></div>
      <div class="actions"><button @click="load">刷新</button><button @click="router.back()">返回任务</button></div>
    </header>
    <div v-if="loading" class="state">正在读取阶段结果…</div>
    <div v-else-if="error" class="state error">{{ error }}</div>
    <div v-else-if="!stages.length" class="state">当前任务尚未产生可查看的阶段产物。</div>
    <main v-else>
      <aside class="process">
        <div class="process-title"><span class="eyebrow">STAGES</span><b>{{ stages.length }} 个业务阶段</b></div>
        <button v-for="(stage, index) in stages" :key="stage.key" type="button" :class="{ active: active?.key === stage.key, ai: stage.kind === 'ai' }" @click.stop="selectStage(stage.key)">
          <small>{{ String(index + 1).padStart(2, '0') }}</small><strong>{{ stage.title }}</strong><span>{{ count(stage) }}</span>
        </button>
      </aside>

      <section v-if="active" class="content">
        <div class="content-head"><span class="eyebrow">{{ active.kind === 'ai' ? 'AI UNDERSTANDING' : 'SYSTEM CHECK' }}</span><h2>{{ active.title }}</h2><p>{{ count(active) }}</p></div>

        <template v-if="active.key === 'mineru_parse'">
          <article v-for="item in named(activeData.documents)" :key="item.name" class="card"><h3>{{ item.name === 'procurement' ? '采购文件' : item.name }}</h3><dl><div><dt>解析引擎</dt><dd>{{ item.value.parser?.name || 'MinerU' }}</dd></div><div><dt>识别内容块</dt><dd>{{ item.value.blocks?.length || 0 }} 个</dd></div><div><dt>文档编号</dt><dd>{{ item.value.document_id || '—' }}</dd></div></dl></article>
          <section class="blocks"><div class="blocks-head"><div><span class="eyebrow">PARSED BLOCKS</span><h3>原子 Block</h3><p>点击查看完整文本、标题级别、阅读顺序和解析来源。</p></div><select v-model="blockType"><option value="all">全部类型（{{ parseBlocks.length }}）</option><option v-for="type in blockTypes" :key="type" :value="type">{{ typeName(type) }}</option></select></div>
            <article v-for="block in visibleBlocks" :key="block.block_id" class="block" :class="{ expanded: expandedBlock === block.block_id }" @click="expandedBlock = expandedBlock === block.block_id ? '' : block.block_id"><div class="block-meta"><span>第 {{ block.page_no || '—' }} 页</span><b>{{ typeName(block.block_type) }}</b><small>{{ block.block_id }}</small></div><strong>{{ block.heading_path?.at(-1) || '文档正文' }}</strong><div v-if="block.block_type === 'table' && block.source?.table_html" class="table-shell" v-html="safeTableHtml(block.source.table_html)"></div><p v-else :class="{ clamp: expandedBlock !== block.block_id }">{{ block.text || '未识别到可显示文本' }}</p><div v-if="expandedBlock === block.block_id" class="block-more"><span>阅读顺序：{{ block.reading_order || '—' }}</span><span v-if="block.heading_level">标题级别：{{ block.heading_level }}</span><span v-if="block.source?.ocr_confidence != null">OCR：{{ block.source.ocr_confidence }}</span><span v-if="block.source?.table_html">已识别表格结构</span></div></article>
          </section>
        </template>

        <template v-else-if="active.key === 'quality_check'">
          <article v-for="item in named(activeData.quality)" :key="item.name" class="card" :class="`quality-${item.value.status}`"><h3>采购文件解析质量 · {{ qualityName(item.value.status) }}</h3><dl><div><dt>页面 / 内容块</dt><dd>{{ item.value.page_count || 0 }} 页 / {{ item.value.block_count || 0 }} 块</dd></div><div><dt>有效文本</dt><dd>{{ item.value.char_count || 0 }} 字</dd></div><div><dt>OCR 状态</dt><dd>{{ ocrStatusName(item.value.ocr_status) }}<template v-if="item.value.ocr_status === 'available' || item.value.ocr_status === 'low_confidence'"> · 覆盖 {{ Math.round((item.value.ocr_confidence_coverage || 0) * 100) }}%</template></dd></div><div><dt>自动清洗</dt><dd>{{ item.value.automatic_action_count || 0 }} 块</dd></div><div><dt>生成待复核问题</dt><dd>{{ list(item.value.review_finding_block_ids).length }} 块</dd></div></dl><p v-if="item.value.retry" class="notice">已使用 {{ item.value.retry.backend || 'MinerU' }} / {{ item.value.retry.parse_method }} 模式重试；重试前状态：{{ qualityName(item.value.retry.previous_status) }}</p><ul v-if="list(item.value.issues).length"><li v-for="issue in list(item.value.issues)" :key="issue.code"><b>[{{ issue.severity }}] {{ issue.code }}</b>：{{ issue.message }}</li></ul><p v-else class="good">未发现解析质量风险。</p></article>
        </template>

        <template v-else-if="active.key === 'deterministic_structure'">
          <article class="card"><h3>结构关系摘要</h3><dl><div><dt>章节</dt><dd>{{ list(activeData.profiles?.procurement?.outline).length }}</dd></div><div><dt>条款父子关系</dt><dd>{{ list(activeData.profiles?.procurement?.clause_relations).length }}</dd></div><div><dt>附件引用</dt><dd>{{ list(activeData.profiles?.procurement?.references).length }}</dd></div></dl></article>
          <div class="card-list"><article v-for="item in list(activeData.profiles?.procurement?.outline).slice(0, 40)" :key="item.block_id" class="line"><b>第 {{ item.page_no || '—' }} 页</b><span>{{ item.text }}</span></article></div>
          <article v-if="list(activeData.profiles?.procurement?.references).length" class="card"><h3>附件与跨文档引用</h3><ul><li v-for="item in list(activeData.profiles.procurement.references)" :key="`${item.source_block_ids}-${item.reference_text}`">{{ item.reference_text }} · {{ item.status }} · {{ item.source_block_ids?.join('、') }} → {{ item.target_block_ids?.join('、') || '未定位' }}</li></ul></article>
        </template>

        <template v-else-if="active.key === 'semantic_structure'">
          <article v-for="item in list(activeData.profiles?.procurement?.section_responsibilities).slice(0, 24)" :key="item.section_id || item.block_id" class="card"><h3>{{ item.heading || '待理解章节' }}</h3><p>{{ item.responsibility || '已完成结构语义判断。' }}</p></article>
        </template>

        <template v-else-if="active.key === 'logical_units'">
          <article class="card"><h3>Block 唯一归属</h3><p>每个非噪声 Block 只属于一个最具体的 primary 逻辑单元；标题等必要内容可作为 context 重复携带。</p><dl><div><dt>逻辑单元</dt><dd>{{ logicalUnits.length }}</dd></div><div><dt>主要 Block</dt><dd>{{ Object.keys(activeData.manifests?.procurement?.block_owner || {}).length }}</dd></div><div><dt>单元类型</dt><dd>{{ new Set(logicalUnits.map(unit => unit.unit_type)).size }}</dd></div></dl></article>
            <article v-for="unit in logicalUnits" :key="unit.unit_id" class="card unit-content"><div class="unit-head"><div><span>{{ unitName(unit.unit_type) }}</span><h3>{{ unitTitle(unit) }}</h3></div><small>{{ unit.unit_id }}</small></div><p class="meta">第 {{ unit.page_range?.[0] || '—' }}–{{ unit.page_range?.[1] || '—' }} 页 · {{ unit.token_estimate }} Token · {{ unit.primary_block_ids?.length || 0 }} 个主要 Block</p><div class="unit-blocks"><article v-for="block in (expandedUnit === unit.unit_id ? primaryBlocks(unit) : primaryBlocks(unit).slice(0, 3))" :key="`${block.block_id}-${block.role}`"><div><b>{{ blockTypeLabel(block.type) }} · 第 {{ block.page || '—' }} 页</b><small>{{ block.block_id }}</small></div><div v-if="tableHtml(block)" class="table-shell" v-html="safeTableHtml(tableHtml(block))"></div><p v-else>{{ cleanText(block.text) }}</p></article></div><button v-if="primaryBlocks(unit).length > 3" class="content-toggle" @click="expandedUnit = expandedUnit === unit.unit_id ? '' : unit.unit_id">{{ expandedUnit === unit.unit_id ? '收起内容' : `查看全部 ${primaryBlocks(unit).length} 个 Block` }}</button></article>
        </template>

        <template v-else-if="active.key === 'review_batches'">
          <article class="card" :class="active.validation?.status === 'passed' ? 'quality-passed' : 'quality-unreliable'"><h3>BatchValidator · {{ active.validation?.status === 'passed' ? '通过' : active.validation?.status || '历史批次未记录校验' }}</h3><dl><div><dt>批次数</dt><dd>{{ active.batches?.length || 0 }}</dd></div><div><dt>主要 Block</dt><dd>{{ active.validation?.primary_block_count ?? '—' }}</dd></div><div><dt>硬失败</dt><dd>{{ active.validation?.issues?.length || 0 }}</dd></div></dl><ul v-if="active.validation?.issues?.length"><li v-for="issue in active.validation.issues" :key="`${issue.code}-${issue.batch_no}`">{{ issue.code }} · {{ display(issue) }}</li></ul></article>
          <article v-for="batch in active.batches" :key="batch.batch_no" class="card batch-content"><div class="batch-head"><div><span>BATCH {{ String(batch.batch_no).padStart(2, '0') }}</span><h3>第 {{ batch.batch_no }} 批 · {{ batch.purpose || '采购文件理解' }}</h3></div><b>{{ batch.primary_block_count ?? batchBlocks(batch).length }} Blocks</b></div><p class="meta">{{ batch.token_estimate ?? batch.character_count ?? 0 }} {{ batch.token_estimate != null ? 'Token' : '字符' }} · 预计候选 {{ batch.candidate_estimate ?? '—' }} 条 · 表格业务行 {{ batch.table_row_count ?? 0 }} 行 · {{ batch.coverage_strategy || '完整逻辑单元装批' }}</p><div class="batch-blocks"><article v-for="block in (expandedBatch === batch.batch_no ? batchBlocks(batch) : batchBlocks(batch).slice(0, 8))" :key="`${block.block_id}-${block.role}-${block.text}`"><div><b>{{ blockTypeLabel(block.type) }} · 第 {{ block.page || '—' }} 页</b><small>{{ block.block_id }}</small></div><div v-if="tableHtml(block)" class="table-shell" v-html="safeTableHtml(tableHtml(block))"></div><p v-else>{{ cleanText(block.text) }}</p></article></div><button v-if="batchBlocks(batch).length > 8" class="content-toggle" @click="toggleBatch(batch.batch_no)">{{ expandedBatch === batch.batch_no ? '收起批次内容' : `查看全部 ${batchBlocks(batch).length} 个输入 Block` }}</button></article>
        </template>

        <template v-else-if="active.key === 'business_understanding'">
          <article v-for="batch in active.batches" :key="batch.file || batch.batch_no" class="card"><h3>第 {{ batch.batch_no || '—' }} 批 · {{ batch.status === 'retry_pending' ? '局部重提中' : batch.status === 'failed' ? '调用失败' : '已完成' }}</h3><p class="meta">主要 Block {{ batch.primary_block_count ?? '—' }} · 预计候选 {{ batch.candidate_estimate ?? '—' }} · 请求 {{ batch.request_tokens ?? '—' }} Token · 输出 {{ batch.output_characters ?? '—' }} 字符</p><p class="meta">保留 {{ batch.accepted_count || 0 }} 条 · 证据待补查 {{ batch.evidence_pending_count || 0 }} 条 · 结构无效排除 {{ batch.rejected_count || 0 }} 条</p><ul><li v-for="item in list(batch.accepted).slice(0, 8)" :key="item.candidate_id || item.statement">{{ item.statement || item.text || '候选采购事项' }} <small>{{ item.evidence_status === 'verified' ? '证据已定位' : '证据待补查' }} · {{ item.evidence_block_ids?.join('、') || '暂无 Block' }}</small></li></ul></article>
        </template>

        <template v-else-if="active.key === 'global_ledger'">
          <template v-if="!Array.isArray(procurementLedger)"><article class="card"><h3>三层可追溯台账</h3><dl><div><dt>提取发生记录</dt><dd>{{ list(procurementLedger.extraction_occurrences).length }}</dd></div><div><dt>原文断言</dt><dd>{{ list(procurementLedger.source_assertions).length }}</dd></div><div><dt>业务事项簇</dt><dd>{{ list(procurementLedger.business_item_clusters).length }}</dd></div></dl></article><article v-for="cluster in list(procurementLedger.business_item_clusters)" :key="cluster.item_id" class="card"><h3>{{ cluster.category }} · {{ cluster.item_id }}</h3><p class="meta">{{ cluster.assertion_ids?.length || 0 }} 条原文断言</p><ul><li v-for="relation in list(cluster.relations)" :key="`${relation.left_assertion_id}-${relation.right_assertion_id}`">{{ relationName(relation.relation_type) }}：{{ relation.left_assertion_id }} ↔ {{ relation.right_assertion_id }}</li></ul></article></template>
          <article v-else class="card"><h3>历史扁平台账</h3><p class="meta">此 run 创建于三层台账上线前，共 {{ procurementLedger.length }} 条。</p><ul><li v-for="item in procurementLedger.slice(0, 20)" :key="item.item_id">{{ item.statement }}</li></ul></article>
        </template>

        <template v-else-if="active.key === 'legal_facts'">
          <article class="card method-card"><div><span class="method-badge">确定性规则</span><h3>本阶段不调用大模型</h3><p>系统只依据明确关键词和结构化台账识别法律事实；证据冲突或没有明确表述时输出“尚未可靠识别”，不让模型猜测。</p></div><b>LLM<br><em>0 次</em></b></article>
          <section class="stage-io-grid">
            <article class="human-panel input-panel"><div class="panel-title"><div><label>INPUT</label><h4>识别输入</h4></div><span>{{ activeData.input ? '完整留痕' : '历史产物' }}</span></div><div class="artifact-summary"><span>采购文件 Block <b>{{ list(activeData.input?.document_blocks).length || '—' }}</b></span><span>业务台账断言 <b>{{ list(activeData.input?.ledger_assertions).length || '—' }}</b></span><span>任务信息 <b>{{ Object.keys(activeData.input?.task_context || {}).length || '—' }}</b></span><span>实际命中证据 <b>{{ factEvidence().length }}</b></span></div><p v-if="!activeData.input" class="notice">该历史任务创建时尚未保存完整阶段输入，以下仅展示输出中保留的命中证据。</p><details v-if="activeData.input?.task_context" class="artifact-detail"><summary>任务与项目信息</summary><pre>{{ display(activeData.input.task_context) }}</pre></details><details v-if="activeData.input?.document_blocks" class="artifact-detail"><summary>采购文件原文 Block <small>{{ activeData.input.document_blocks.length }} 条</small></summary><div class="source-list"><article v-for="block in activeData.input.document_blocks" :key="block.block_id"><div><b>第 {{ block.page_no || '—' }} 页 · {{ block.heading_path?.at(-1) || '正文' }}</b><small>{{ block.block_id }}</small></div><p>{{ cleanText(block.text) }}</p></article></div></details><details v-if="activeData.input?.ledger_assertions" class="artifact-detail"><summary>业务理解台账断言 <small>{{ activeData.input.ledger_assertions.length }} 条</small></summary><pre>{{ display(activeData.input.ledger_assertions) }}</pre></details></article>
            <article class="human-panel output-panel"><div class="panel-title"><div><label>OUTPUT</label><h4>标准化法律事实</h4></div><span>{{ Object.keys(activeData.task_legal_facts || {}).filter(key => key !== 'evidence').length }} 项</span></div><div class="legal-fact-list"><div v-for="(value, key) in activeData.task_legal_facts" v-show="key !== 'evidence'" :key="key" :class="{ unresolved: value === 'unknown' }"><span>{{ legalFactLabel(String(key)) }}</span><b>{{ legalFactValue(value) }}</b><small>{{ key }}</small></div></div><p v-if="activeData.missing_facts?.length" class="notice">待人工补充：{{ activeData.missing_facts.map(legalFactLabel).join('、') }}</p><div class="matched-evidence"><div class="list-title"><b>输出对应的命中证据</b><span>{{ factEvidence().length }} 条</span></div><article v-for="(source, index) in factEvidence()" :key="`${source.field}-${source.block_id || source.item_id}-${index}`"><div><b>{{ legalFactLabel(source.field) }}</b><small>{{ source.source === 'ledger' ? '业务台账' : source.source === 'document' ? `采购文件 · 第 ${source.page_no || '—'} 页` : '任务信息' }} · {{ source.block_id || source.item_id || source.field || '' }}</small></div><p>{{ cleanText(source.quote) }}</p></article><p v-if="!factEvidence().length" class="plain-copy">当前没有可支持已识别事实的原文证据。</p></div></article>
          </section>
        </template>

        <template v-else-if="active.key === 'legal_candidates'">
          <article class="card"><h3>规则与全量法规上下文</h3><p v-if="activeData.execution_status === 'degraded'" class="notice">规则能力处于降级状态；本次不能视为已完成规则审查。</p><p v-else>规则库已加载并完成匹配，法规资料作为专业审查上下文使用。</p><dl><div><dt>规则库状态</dt><dd>{{ activeData.degraded_reasons?.includes('executable_rule_library_empty') ? '未配置' : activeData.execution_status === 'degraded' ? '加载异常' : '已加载' }}</dd></div><div><dt>可执行规则总数</dt><dd>{{ activeData.rules?.length || 0 }} 条</dd></div><div><dt>实际匹配规则</dt><dd>{{ activeData.matched_count || 0 }} 条</dd></div></dl><ul v-if="activeData.warnings?.length"><li v-for="warning in activeData.warnings" :key="warning">{{ warning }}</li></ul></article>
          <article v-for="source in list(activeData.legal_sources)" :key="source.document_key" class="card"><h3>{{ source.title || source.document_key }}</h3><p :class="source.included ? 'good' : 'muted'">{{ source.included ? '已纳入本次审查法规范围' : '未纳入（法规未生效或已失效）' }} · 效力状态 {{ source.status || '未知' }}</p></article>
        </template>

        <template v-else-if="active.key === 'legal_applicability_gate'">
          <article class="card quality-degraded"><h3>审查前法规适用性门禁</h3><p>专业审查 Agent 只能使用本阶段经系统匹配并经人工确认的法规。存在待确认项时，流程在这里暂停，不会进入专业审查。</p><dl><div><dt>候选法规</dt><dd>{{ list(activeData.decisions).length }} 份</dd></div><div><dt>正式法规条文</dt><dd>{{ list(activeData.applicable_legal_units).length }} 条</dd></div><div><dt>候选法规条文</dt><dd>{{ list(activeData.candidate_legal_units).length }} 条</dd></div></dl></article>
          <article v-for="decision in list(activeData.decisions)" :key="decision.document_key" class="card"><h3>{{ decision.title || decision.document_key }}</h3><p>系统判断：{{ decision.status === 'applicable' ? '适用' : decision.status === 'potential' ? '可能适用，待人工确认' : decision.status === 'insufficient_facts' ? '事实不足，待补充' : '不适用' }}</p><p v-if="decision.missing_facts?.length" class="notice">缺少事实：{{ decision.missing_facts.join('、') }}</p></article>
        </template>

        <template v-else-if="active.key === 'professional_review'">
          <section class="review-tools"><article v-for="tool in active.tools" :key="tool.key" class="card"><h3>{{ tool.title }} <small :class="tool.triggered ? 'good' : 'muted'">{{ tool.triggered ? '已执行' : '历史运行无结果' }}</small></h3><template v-if="tool.key === 'rule_coverage'"><dl><div><dt>规则库状态</dt><dd>{{ ruleLibraryLabel(tool.data.library_status) }}</dd></div><div><dt>可执行规则</dt><dd>{{ tool.data.executable_rule_count || 0 }} 条</dd></div><div><dt>实际匹配</dt><dd>{{ tool.data.matched_count || 0 }} 条</dd></div></dl><p v-if="tool.data.library_status === 'not_configured'" class="notice">规则库未配置，本次未执行规则覆盖审查。</p><ul><li v-for="item in list(tool.data.results).slice(0, 12)" :key="item.rule_id">{{ item.rule_id }} · {{ item.status }}</li></ul></template><template v-else-if="tool.key === 'required_elements'"><p :class="tool.data.complete ? 'good' : ''">{{ tool.data.complete ? '必备主题完整。' : `缺少：${list(tool.data.missing).join('、')}` }}</p></template><template v-else-if="tool.key === 'section_conflicts'"><p>{{ tool.data.count || 0 }} 组跨章节冲突候选。</p></template><template v-else-if="tool.key === 'follow_up'"><p>{{ list(tool.data).length }} 次疑点定向补查。</p></template><template v-else><pre>{{ display(tool.data) }}</pre></template></article></section>
          <article class="card"><h3>Agent 专业判断</h3><p>{{ activeData.overall_conclusion || '未生成总体结论。' }}</p><p class="meta">候选问题：{{ list(activeData.findings).length }} 个</p></article>
        </template>

        <template v-else-if="active.key === 'evidence_validation'">
          <article class="card"><h3>独立证据校验</h3><dl><div><dt>已验证</dt><dd>{{ activeData.verified_count || 0 }}</dd></div><div><dt>证据不足</dt><dd>{{ activeData.insufficient_count || 0 }}</dd></div><div><dt>总体结论</dt><dd>{{ activeData.overall_conclusion || '—' }}</dd></div></dl></article>
          <article v-for="finding in list(activeData.findings)" :key="finding.finding_id" class="card" :class="finding.evidence_status === 'verified' ? 'quality-passed' : 'quality-unreliable'"><h3>{{ finding.title || finding.finding_id }} · {{ finding.evidence_status === 'verified' ? '证据充分' : '待人工确认' }}</h3><p>{{ finding.description }}</p><p class="meta">Block：{{ finding.evidence_block_ids?.join('、') || '无' }}；法规：{{ finding.legal_unit_ids?.join('、') || '无' }}</p><ul v-if="validationErrors(finding).length"><li v-for="code in validationErrors(finding)" :key="code">{{ code }}</li></ul></article>
        </template>

        <template v-else><article class="card"><h3>阶段原始结果</h3><pre>{{ display(activeData) }}</pre></article></template>

        <section v-if="active.kind === 'ai'" class="trace-section">
          <span class="eyebrow">AI INPUT / OUTPUT</span>
          <div class="trace-heading"><div><h3>{{ aiTraceTitle(active.key) }}过程产物</h3><p>核对 Prompt、批次 Payload、原文覆盖和结构化输出，定位流程遗漏与异常。</p></div><b>{{ active.llm_calls?.length || 0 }} 次调用</b></div>
          <article v-for="(call, index) in active.llm_calls" :key="call.id" class="trace-card">
            <button @click="expandedTrace = expandedTrace === call.id ? '' : call.id"><span><small>调用 {{ String(index + 1).padStart(2, '0') }}</small><b>{{ call.id }}</b></span><em>{{ expandedTrace === call.id ? '收起' : '查看输入与输出' }}</em></button>
            <div v-if="expandedTrace === call.id" class="human-trace observability-trace">
              <div class="call-metrics"><div><small>MODEL</small><b>{{ modelName(call) }}</b></div><div><small>PROMPT</small><b>{{ promptLength(call).toLocaleString() }} 字符</b></div><div><small>PAYLOAD</small><b>{{ payloadLength(call).toLocaleString() }} 字符</b></div><div><small>INPUT BLOCKS</small><b>{{ inputBlocks(call).length }}</b></div><div><small>OUTPUT ITEMS</small><b>{{ outputItems(call).length }}</b></div><div :class="{ alert: outputRejected(call).length || outputWarnings(call).length }"><small>EXCEPTIONS</small><b>{{ outputRejected(call).length + outputWarnings(call).length }}</b></div></div>
              <section class="human-panel input-panel">
                <div class="panel-title"><div><label>REQUEST ASSEMBLY</label><h4>Prompt 与批次输入</h4></div><span>{{ inputBlocks(call).length }} Blocks</span></div>
                <div class="fact-strip"><span>temperature：{{ json(call.request)?.temperature ?? '—' }}</span><span v-if="callInput(call)?.batch_no">batch_no：{{ callInput(call).batch_no }}</span><span v-if="callInput(call)?.batch_purpose">purpose：{{ callInput(call).batch_purpose }}</span><span v-if="callInput(call)?.coverage_strategy">strategy：{{ callInput(call).coverage_strategy }}</span></div>
                <details class="artifact-detail" open><summary>System Prompt <small>{{ promptLength(call).toLocaleString() }} 字符</small></summary><pre class="prompt-view">{{ systemPrompt(call) }}</pre></details>
                <details class="artifact-detail" open><summary>Payload 元数据</summary><pre>{{ display(Object.fromEntries(entries(callInput(call)).filter(([key]) => key !== 'blocks'))) }}</pre></details>
                <div v-if="inputBlocks(call).length" class="source-list"><div class="list-title"><b>输入原文块</b><span>{{ inputBlocks(call).length }} 条</span></div><article v-for="block in inputBlocks(call)" :key="`${block.block_id}-${block.text}`"><div><b>{{ block.block_id }} · 第 {{ block.page || '—' }} 页 · {{ blockTypeLabel(block.type) }}</b><small>{{ String(block.text || '').length }} 字符</small></div><p>{{ cleanText(block.text) }}</p></article></div>
                <p v-else class="plain-copy">该调用没有 Blocks 数组；完整 Payload 可在上方查看。</p>
              </section>
              <section class="human-panel output-panel">
                <div class="panel-title"><div><label>STRUCTURED ARTIFACT</label><h4>模型结构化产物</h4></div><span class="result-count">{{ outputItems(call).length }} Items</span></div>
                <div class="artifact-summary"><span>有效项 <b>{{ outputItems(call).length }}</b></span><span>覆盖记录 <b>{{ outputCoverage(call).length }}</b></span><span :class="{ danger: outputRejected(call).length }">拒绝/未决 <b>{{ outputRejected(call).length }}</b></span><span :class="{ danger: outputWarnings(call).length }">警告 <b>{{ outputWarnings(call).length }}</b></span></div>
                <p v-if="callOutput(call)?.overall_conclusion" class="conclusion">{{ callOutput(call).overall_conclusion }}</p>
                <details v-if="outputCoverage(call).length" class="artifact-detail"><summary>覆盖报告 <small>{{ outputCoverage(call).length }} 条</small></summary><pre>{{ display(outputCoverage(call)) }}</pre></details>
                <details v-if="outputRejected(call).length || outputWarnings(call).length" class="artifact-detail exception-detail" open><summary>异常与未决项 <small>{{ outputRejected(call).length + outputWarnings(call).length }} 条</small></summary><pre>{{ display({ rejected_or_unresolved: outputRejected(call), warnings: outputWarnings(call) }) }}</pre></details>
                <div v-if="outputItems(call).length" class="result-list"><div class="list-title"><b>结构化条目</b><span>{{ outputItems(call).length }} 条</span></div><article v-for="(item, itemIndex) in outputItems(call)" :key="item.candidate_id || item.finding_id || item.section_id || itemIndex"><div class="result-head"><code>{{ item.candidate_id || item.finding_id || item.section_id || `ITEM-${itemIndex + 1}` }}</code><span>{{ itemCategory(item) }}</span><small v-if="item.page_no">P{{ item.page_no }}</small><small v-if="item.risk_level">risk={{ item.risk_level }}</small></div><h5>{{ itemTitle(item) }}</h5><p v-if="item.evidence_quote">evidence_quote：{{ item.evidence_quote }}</p><p v-else-if="item.description">{{ item.description }}</p><div class="result-meta"><span v-if="item.requirement_type">type={{ item.requirement_type }}</span><span v-if="item.mandatory_signal">mandatory={{ item.mandatory_signal }}</span><span v-if="item.source_value">value={{ item.source_value }}</span><span v-if="item.confidence != null">confidence={{ item.confidence }}</span><span v-if="item.evidence_block_ids?.length">blocks={{ item.evidence_block_ids.join(', ') }}</span></div><details><summary>完整字段</summary><pre>{{ display(item) }}</pre></details></article></div>
                <p v-else class="plain-copy">{{ typeof callOutput(call) === 'string' ? callOutput(call) : '本次调用没有返回可展示的业务条目。' }}</p>
              </section>
              <details class="raw-detail"><summary>RAW TRACE · 原始请求与响应</summary><div class="io-grid"><section><label>REQUEST</label><pre>{{ display(call.request ?? '无输入记录') }}</pre></section><section><label>RESPONSE</label><pre>{{ display(call.response ?? call.error ?? '无返回记录') }}</pre></section></div></details>
            </div>
          </article>
          <p v-if="!active.llm_calls?.length" class="empty-trace">本阶段没有可显示的模型调用记录；可能未触发模型，或任务创建于调用记录功能上线前。</p>
        </section>
      </section>
    </main>
  </div>
</template>

<style scoped>
.debug-page{min-height:100%;padding:34px 42px 60px;background:#f4f1eb;color:#24312d}header,main{max-width:1320px;margin:auto}header{display:flex;align-items:flex-end;justify-content:space-between;gap:20px;margin-bottom:24px}.eyebrow{color:#b9573e;font:700 10px ui-monospace,Consolas,monospace;letter-spacing:.13em}h1,h2,h3{font-family:Georgia,'Noto Serif SC',serif}h1{margin:8px 0;font-size:34px;font-weight:500}header p,.meta{margin:0;color:#7b8580;font-size:13px}.actions{display:flex;gap:8px}button{border:1px solid #dedbd3;border-radius:7px;background:#fffefa;padding:8px 12px;color:#31403a;cursor:pointer}.state{max-width:1320px;margin:auto;padding:28px;border:1px solid #dedbd3;border-radius:12px;background:#fffefa}.error{color:#b9573e}main{display:grid;grid-template-columns:360px minmax(0,1fr);gap:18px;align-items:start}.process,.content{border:1px solid #dedbd3;border-radius:13px;background:#fffefa;overflow:hidden}.process-title{padding:18px 20px;border-bottom:1px solid #ebe8e0;display:flex;justify-content:space-between}.process button{display:grid;width:100%;gap:5px;padding:12px 15px;border:0;border-left:3px solid #b9c4bc;border-radius:0;text-align:left;background:#fffefa}.process button.ai{border-left-color:#7668ad}.process button.active{background:#fff3ed;border-left-color:#c45f46}.process button.active.ai{background:#f0edf9;border-left-color:#7668ad}.process small{color:#b9573e;font:10px ui-monospace,monospace}.process strong{font-size:14px}.process span{color:#8b948e;font-size:11px}.content-head{padding:27px 30px 20px;border-bottom:1px solid #ebe8e0}.content-head h2{margin:8px 0 5px;font-size:28px;font-weight:500}.content{min-height:520px}.card,.card-list{margin:16px 22px;padding:17px;border:1px solid #e7e2d9;border-radius:10px;background:#faf8f3}.card h3{margin:0 0 10px;font-size:17px;font-weight:500}.card p,.card li{font-size:13px;line-height:1.75;color:#56635c}.card ul{margin:9px 0 0;padding-left:20px}.card li+li{margin-top:6px}.card pre{max-height:480px;overflow:auto;margin:0;padding:10px;background:#f4f1eb;font:11px/1.6 ui-monospace,Consolas,monospace;white-space:pre-wrap}dl{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:0}dt{color:#89918b;font-size:11px}dd{margin:5px 0 0;font-size:14px}.line{display:flex;gap:14px;padding:10px 0;border-bottom:1px solid #e9e4dc;font-size:13px;line-height:1.5}.line:last-child{border-bottom:0}.line b{flex:none;color:#b9573e;font-size:11px}.good{color:#57734f!important}.muted{color:#8b948e}.notice{padding:9px;border-radius:6px;background:#fff1d9}.quality-passed{border-left:3px solid #6c8a62}.quality-degraded{border-left:3px solid #c39243}.quality-retryable,.quality-unreliable{border-left:3px solid #b9573e}.unit{cursor:pointer}.blocks,.trace-section{margin:26px 22px 30px;border-top:1px solid #e7e2d9;padding-top:20px}.blocks-head{display:flex;justify-content:space-between;gap:18px;align-items:end;margin-bottom:12px}.blocks-head h3,.trace-section h3{margin:5px 0;font-size:19px;font-weight:500}.blocks-head p{margin:0;color:#7b8580;font-size:12px}.blocks select{min-width:150px;padding:8px;border:1px solid #dedbd3;border-radius:7px;background:#fffefa;color:#45534d}.block{padding:13px 15px;border-top:1px solid #ece7de;cursor:pointer}.block:hover,.block.expanded{background:#faf6f0}.block-meta{display:flex;align-items:center;gap:8px;margin-bottom:7px}.block-meta span{color:#b9573e;font-size:11px}.block-meta b{padding:2px 6px;border-radius:3px;background:#e8eee3;color:#5e7456;font-size:10px}.block-meta small{margin-left:auto;color:#99a09a;font:10px ui-monospace,monospace}.block>strong{font-size:12px}.block p{margin:6px 0 0;color:#59665e;font-size:13px;line-height:1.7;white-space:pre-wrap}.block p.clamp{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}.block-more{display:flex;flex-wrap:wrap;gap:14px;margin-top:10px;color:#8b948e;font-size:11px}.trace-card{margin-top:10px;border:1px solid #e7e2d9;border-radius:9px;overflow:hidden}.trace-card button{display:flex;justify-content:space-between;width:100%;border:0;border-radius:0}.trace-card>div{padding:12px}.trace-card label{display:block;margin:7px 0 4px;color:#8b948e;font-size:11px}.trace-card pre{max-height:300px;overflow:auto;margin:0;padding:10px;background:#f4f1eb;font:11px/1.6 ui-monospace,Consolas,monospace;white-space:pre-wrap}.review-tools .card{border-left:3px solid #b9c4bc}@media(max-width:850px){.debug-page{padding:24px 16px}header{display:block}.actions{margin-top:15px}main{grid-template-columns:1fr}.content{min-height:0}dl{grid-template-columns:1fr 1fr}.blocks-head{display:block}.blocks select{margin-top:12px}}
.trace-heading{display:flex;align-items:end;justify-content:space-between;gap:20px;margin-bottom:12px}.trace-heading h3{margin-bottom:4px}.trace-heading p{margin:0;color:#7b8580;font-size:12px}.trace-heading>b{flex:none;padding:5px 9px;border-radius:999px;background:#eeeaf7;color:#665a96;font-size:11px}.trace-card button{align-items:center;padding:11px 13px;text-align:left}.trace-card button>span{display:grid;gap:3px}.trace-card button small{color:#b9573e;font:9px ui-monospace,monospace;letter-spacing:.08em}.trace-card button b{font:12px ui-monospace,Consolas,monospace}.trace-card button em{color:#7668ad;font-size:11px;font-style:normal}.trace-card label{margin:0 0 5px;font-size:10px;letter-spacing:.06em}.trace-card pre{max-height:420px;padding:11px}.io-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;border-top:1px solid #e7e2d9}.io-grid section{min-width:0}.empty-trace{margin:12px 0 0;padding:14px;border:1px dashed #d9d4ca;border-radius:8px;color:#7b8580;font-size:12px}@media(max-width:850px){.io-grid{grid-template-columns:1fr}.trace-heading{align-items:start}}
.human-trace{display:grid!important;grid-template-columns:1fr 1fr;gap:12px;border-top:1px solid #e7e2d9;background:#f7f4ee}.human-panel{min-width:0;padding:16px;border:1px solid #e4dfd6;border-radius:9px;background:#fffefa}.panel-title{display:flex;align-items:center;justify-content:space-between;gap:12px}.panel-title label{color:#b9573e}.panel-title h4{margin:2px 0 0;font:500 18px Georgia,'Noto Serif SC',serif}.panel-title>span{flex:none;padding:4px 8px;border-radius:999px;background:#edf1eb;color:#587052;font-size:10px}.fact-strip{display:flex;flex-wrap:wrap;gap:7px;margin:12px 0}.fact-strip span{padding:4px 7px;border-radius:4px;background:#f3f0e9;color:#657169;font-size:10px}.category-row{display:flex;flex-wrap:wrap;align-items:center;gap:6px;margin-bottom:12px}.category-row b{margin-right:3px;color:#7b8580;font-size:11px}.category-row span{padding:3px 7px;border:1px solid #ddd7ca;border-radius:999px;color:#59665e;font-size:10px}.source-list,.result-list{max-height:520px;overflow:auto;padding-right:4px}.source-list article{padding:11px 0;border-top:1px solid #ece7de}.source-list article>div{display:flex;justify-content:space-between;gap:10px}.source-list b{color:#48564f;font-size:11px}.source-list small{color:#959d97;font:9px ui-monospace,monospace}.source-list p{margin:5px 0 0;color:#59665e;font-size:12px;line-height:1.7}.output-panel{background:#fbfaf5}.result-count{background:#eeeaf7!important;color:#665a96!important}.conclusion{padding:10px;border-left:3px solid #7668ad;background:#f3f0f9;color:#4f5c56;font-size:12px;line-height:1.7}.result-list article{margin-top:9px;padding:12px;border:1px solid #e5e0d6;border-radius:8px;background:#fff}.result-head{display:flex;align-items:center;gap:8px}.result-head span{padding:3px 7px;border-radius:4px;background:#eaf0e5;color:#587052;font-size:10px}.result-head small{color:#929a94;font-size:9px}.result-list h5{margin:8px 0 5px;color:#34413b;font-size:13px;line-height:1.55}.result-list p{margin:0;color:#6b756f;font-size:11px;line-height:1.65}.result-meta{display:flex;flex-wrap:wrap;gap:8px;margin-top:8px;color:#8b948e;font-size:9px}.plain-copy{margin:12px 0 0;color:#66726b;font-size:12px;line-height:1.7;white-space:pre-wrap}.human-trace>details{grid-column:1/-1;padding:9px 2px 0}.human-trace summary{color:#7668ad;font-size:11px;cursor:pointer}.human-trace details .io-grid{margin-top:10px;padding-top:10px}.human-trace details label{display:block;margin-bottom:5px;color:#7b8580}.human-trace details pre{max-height:320px}.human-trace::-webkit-scrollbar,.source-list::-webkit-scrollbar,.result-list::-webkit-scrollbar{width:7px}.source-list::-webkit-scrollbar-thumb,.result-list::-webkit-scrollbar-thumb{border-radius:8px;background:#c7c4bd}@media(max-width:1050px){.human-trace{grid-template-columns:1fr}.human-trace>details{grid-column:auto}}@media(max-width:850px){.panel-title{align-items:start}.source-list,.result-list{max-height:none}}
.observability-trace{padding-top:12px}.call-metrics{grid-column:1/-1;display:grid;grid-template-columns:repeat(6,1fr);gap:1px;border:1px solid #dfd9cf;border-radius:8px;overflow:hidden;background:#dfd9cf}.call-metrics>div{min-width:0;padding:10px;background:#fffefa}.call-metrics small{display:block;color:#929991;font:8px ui-monospace,Consolas,monospace;letter-spacing:.09em}.call-metrics b{display:block;overflow:hidden;margin-top:4px;color:#39463f;font:11px ui-monospace,Consolas,monospace;text-overflow:ellipsis;white-space:nowrap}.call-metrics .alert{background:#fff2ec}.call-metrics .alert b{color:#b9573e}.artifact-detail{margin:10px 0;border:1px solid #e5e0d6;border-radius:7px;background:#faf8f3;overflow:hidden}.artifact-detail summary{display:flex;justify-content:space-between;padding:9px 10px;color:#536159;font:11px ui-monospace,Consolas,monospace}.artifact-detail summary small{color:#969d97}.artifact-detail pre{margin:0!important;border-top:1px solid #e5e0d6;background:#f2efe8!important}.prompt-view{max-height:260px!important}.exception-detail{border-color:#e4c6b9;background:#fff7f3}.exception-detail summary{color:#a84f3b}.list-title{position:sticky;top:0;z-index:1;display:flex;justify-content:space-between;padding:9px 0 7px;background:inherit;color:#46534c;font-size:11px}.list-title span{color:#8d958f;font:9px ui-monospace,monospace}.artifact-summary{display:grid;grid-template-columns:repeat(4,1fr);gap:6px;margin:12px 0}.artifact-summary span{padding:8px;border:1px solid #e4dfd6;border-radius:6px;color:#7d8781;font-size:9px}.artifact-summary b{display:block;margin-top:2px;color:#405047;font:15px Georgia,serif}.artifact-summary .danger{border-color:#e1c1b5;background:#fff4ef;color:#a9513e}.result-head code{color:#695d98;font:9px ui-monospace,Consolas,monospace}.result-list article>details{margin-top:9px;border-top:1px dashed #ddd8cf;padding-top:7px}.result-list article>details summary{color:#7c6fa8;font-size:9px}.result-list article>details pre{max-height:240px;margin-top:7px!important}.raw-detail{margin-top:0}.raw-detail>summary{font:10px ui-monospace,Consolas,monospace;letter-spacing:.07em}@media(max-width:1150px){.call-metrics{grid-template-columns:repeat(3,1fr)}}@media(max-width:700px){.call-metrics{grid-template-columns:repeat(2,1fr)}.artifact-summary{grid-template-columns:1fr 1fr}}
.unit-content,.batch-content{padding:0;overflow:hidden}.unit-head,.batch-head{display:flex;align-items:start;justify-content:space-between;gap:16px;padding:16px 17px 10px}.unit-head>div>span,.batch-head>div>span{display:block;margin-bottom:4px;color:#b9573e;font:9px ui-monospace,Consolas,monospace;letter-spacing:.08em}.unit-head h3,.batch-head h3{margin:0}.unit-head>small{color:#9aa19b;font:9px ui-monospace,Consolas,monospace}.batch-head>b{flex:none;padding:4px 8px;border-radius:999px;background:#eaf0e5;color:#587052;font-size:10px}.unit-content>.meta,.batch-content>.meta{padding:0 17px 12px}.unit-blocks,.batch-blocks{border-top:1px solid #e7e2d9;background:#fffefa}.unit-blocks article,.batch-blocks article{padding:11px 17px;border-bottom:1px solid #eee9e1}.unit-blocks article:last-child,.batch-blocks article:last-child{border-bottom:0}.unit-blocks article>div:not(.table-shell),.batch-blocks article>div:not(.table-shell){display:flex;align-items:center;justify-content:space-between;gap:12px}.unit-blocks b,.batch-blocks b{color:#59675f;font-size:10px}.unit-blocks small,.batch-blocks small{color:#9aa19b;font:9px ui-monospace,Consolas,monospace}.unit-blocks p,.batch-blocks p{margin:5px 0 0;color:#435049;font-size:12px;line-height:1.7;white-space:pre-wrap}.content-toggle{width:100%;border:0;border-top:1px solid #e7e2d9;border-radius:0;padding:10px;background:#f6f2eb;color:#7668ad;font-size:11px}.content-toggle:hover{background:#f0ece4}.batch-blocks{display:grid;grid-template-columns:1fr 1fr}.batch-blocks article:nth-child(odd){border-right:1px solid #eee9e1}@media(max-width:850px){.batch-blocks{grid-template-columns:1fr}.batch-blocks article:nth-child(odd){border-right:0}.unit-head,.batch-head{display:block}.unit-head>small,.batch-head>b{display:inline-block;margin-top:8px}}
.debug-page>header{box-sizing:border-box;max-width:1440px;height:auto;min-height:124px;padding:22px 26px;align-items:center;border:1px solid #dedbd3;border-radius:14px;background:#fffefa;color:#24312d}.debug-page>header>div:first-child{display:grid;align-content:center;gap:0;min-width:0}.debug-page>header h1{margin:8px 0 10px;color:#24312d;line-height:1.08}.debug-page>header p{color:#7b8580;line-height:1.6}.debug-page>main{max-width:1440px;grid-template-columns:310px minmax(0,1fr)}.process{position:sticky;top:18px;max-height:calc(100vh - 36px);overflow:auto}.content{min-width:0}.source-list,.result-list{max-height:none;overflow:visible}.human-panel{align-self:start}.trace-card{background:#fffefa}.trace-card>button:hover{background:#f6f2eb}.content-head{background:linear-gradient(180deg,#fffefa,#fbf9f4)}@media(max-width:850px){.debug-page>header{height:auto;min-height:0;padding:18px}.debug-page>main{grid-template-columns:1fr}.process{position:static;max-height:none}}
.table-shell{max-height:420px;overflow:auto;margin-top:9px;border:1px solid #ddd8cf;border-radius:7px;background:#fff}.table-shell :deep(table){width:100%;border-collapse:collapse;font-size:12px;line-height:1.55}.table-shell :deep(th),.table-shell :deep(td){padding:8px 10px;border:1px solid #e3ded5;vertical-align:top;text-align:left;word-break:break-word;overflow-wrap:anywhere}.table-shell :deep(th){background:#f0ede5;color:#34413b;font-weight:600}.table-shell :deep(tr:nth-child(even) td){background:#fbfaf6}
</style>
