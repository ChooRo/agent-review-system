<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { apiErrorMessage } from '../../api'
import { getKnowledge, listKnowledge } from '../../api/knowledge'
import type { KnowledgeListItem, LegalUnit } from '../../api/knowledge'

type KnowledgeTab = 'docs' | 'rules' | 'biz'

const kbtab = ref<KnowledgeTab>('docs')
const documents = ref<KnowledgeListItem[]>([])
const loading = ref(true)
const error = ref('')
const expandedDoc = ref<KnowledgeListItem>()
const legalUnits = ref<LegalUnit[]>([])
const detailLoading = ref(false)
const documentQuery = ref('')

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

async function toggleDocument(document: KnowledgeListItem) {
  if (expandedDoc.value?.document_key === document.document_key) {
    expandedDoc.value = undefined
    legalUnits.value = []
    return
  }
  expandedDoc.value = document
  legalUnits.value = []
  detailLoading.value = true
  try {
    legalUnits.value = (await getKnowledge(document.document_key)).units
  } catch (reason) {
    error.value = apiErrorMessage(reason)
  } finally {
    detailLoading.value = false
  }
}

const filteredUnits = computed(() => {
  const keyword = documentQuery.value.trim()
  if (!keyword) return legalUnits.value
  return legalUnits.value.filter((unit) => [unit.article_no, unit.chapter, unit.section, unit.text].some((value) => value?.includes(keyword)))
})

function statusLabel(value?: string) {
  if (value === 'effective') return '现行有效'
  if (value === 'repealed') return '已废止'
  return value || '未标注'
}

onMounted(() => { void loadDocuments() })
</script>

<template>
  <div class="knowledge-view">
    <div class="page-head">
      <div class="crumb">知识资产 / 知识库</div>
      <div class="page-title-row"><h2>知识库</h2></div>
      <p>收录采购审查相关法规文件，解析为可检索的章、条、款、项单元，供人工查阅和审查证据引用。</p>
    </div>

    <div class="page-body">
      <div class="tabs" role="tablist" aria-label="知识库层级">
        <button class="tab" :class="{ act: kbtab === 'docs' }" role="tab" :aria-selected="kbtab === 'docs'" @click="kbtab = 'docs'">文档层</button>
        <button class="tab" :class="{ act: kbtab === 'rules' }" role="tab" :aria-selected="kbtab === 'rules'" @click="kbtab = 'rules'">规则库</button>
        <button class="tab" :class="{ act: kbtab === 'biz' }" role="tab" :aria-selected="kbtab === 'biz'" @click="kbtab = 'biz'">业务知识库</button>
      </div>

      <section v-if="kbtab === 'docs'" class="knowledge-section" aria-label="法规文档">
        <div v-if="loading" class="state-card">正在加载法规文档…</div>
        <div v-else-if="error" class="state-card error-state">
          {{ error }}
          <button class="btn" style="margin-left:12px" @click="loadDocuments">重试</button>
        </div>
        <template v-else>
          <div class="note">文档层保留原始法规、规范与标准，并展示后端已解析的条款单元；这些单元尚未等同于可执行规则。</div>
          <div class="knowledge-stats" aria-label="文档统计">
            <span>已入库文档 <strong>{{ documents.length }}</strong></span>
            <span>可展开查看章、条、款、项</span>
          </div>
          <div v-if="!documents.length" class="empty knowledge-empty">暂无法规文档入库</div>
          <div v-for="document in documents" :key="document.document_key" class="doc-wrap">
            <article class="kfile doc-layer-file">
              <span class="kf-ic" aria-hidden="true">§</span>
              <div style="flex:1;min-width:0">
                <div class="kn">{{ document.title }}</div>
                <div class="km">{{ document.article_count ?? '待统计' }} 条 · {{ document.unit_count ?? '待统计' }} 个检索单元 · 归口：{{ document.issuer || '未标注' }}</div>
                <div class="doc-meta-row">
                  <span class="chip">{{ statusLabel(document.status) }}</span>
                  <span class="chip">{{ document.effective_date || '生效日期未标注' }}</span>
                  <span v-if="document.quality_status" class="chip">{{ document.quality_status }}</span>
                </div>
              </div>
              <button class="btn" @click="toggleDocument(document)">{{ expandedDoc?.document_key === document.document_key ? '收起条款' : '查看条款' }}</button>
            </article>
            <div v-if="expandedDoc?.document_key === document.document_key" class="document-units">
              <p v-if="detailLoading" class="state-card">正在加载条款…</p>
              <template v-else>
                <label class="sr-only" for="unit-query">条款筛选</label>
                <input id="unit-query" v-model="documentQuery" class="kb-search unit-search" placeholder="按条号、章节或关键词筛选条款" />
                <p v-if="!filteredUnits.length" class="empty knowledge-empty">没有匹配的条款</p>
                <article v-for="unit in filteredUnits" :key="unit.legal_unit_id" class="rule">
                  <div class="rule-heading"><span class="rid">{{ unit.article_no }}</span><strong>{{ unit.chapter || unit.section || '未标注章节' }}</strong></div>
                  <p class="rd">{{ unit.text }}</p>
                  <div class="rmeta"><span class="chip">{{ unit.unit_type }}</span><span class="chip">{{ statusLabel(unit.status) }}</span><span v-if="unit.evidence[0]?.page_no" class="chip">第 {{ unit.evidence[0].page_no }} 页</span></div>
                </article>
              </template>
            </div>
          </div>
        </template>
      </section>

      <section v-else-if="kbtab === 'rules'" class="empty knowledge-empty unavailable" aria-label="规则库尚未开放">
        <div>尚未抽取并确认可执行规则</div>
        <p>当前已开放的是法规文档及条款单元。规则抽取、专业确认与启用管理将在后续版本开放。</p>
      </section>

      <section v-else class="empty knowledge-empty unavailable" aria-label="业务知识库暂未开放">
        <div>业务知识库暂未开放</div>
        <p>业务术语、历史项目文件和内部口径尚未纳入本期开放范围。</p>
      </section>
    </div>
  </div>
</template>

<style scoped>
.knowledge-section { min-height:320px; }
.knowledge-stats { display:flex; gap:18px; align-items:center; margin:0 0 14px; padding:10px 13px; border:1px solid var(--border); border-radius:10px; background:var(--white); color:var(--stone); font-size:11px; }
.knowledge-stats strong { color:var(--terra); font:600 16px var(--serif); }
.note { margin:0 0 14px; }
.doc-wrap + .doc-wrap { margin-top:10px; }
.document-units { padding:12px 0 0 15px; border-left:1px solid var(--border); }
.kb-search { width:100%; padding:7px 11px; border:1px solid var(--border); border-radius:8px; background:var(--white); color:var(--ink); font:12px var(--sans); outline:none; }
.kb-search:focus { border-color:var(--terra); box-shadow:0 0 0 2px rgba(171,114,86,.12); }
.unit-search { margin-bottom:10px; }
.rule-heading { display:flex; align-items:center; gap:8px; min-width:0; }
.rule-heading strong { min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:12px; color:var(--ink); }
.rd { margin:9px 0; line-height:1.75; }
.knowledge-empty { min-height:260px; display:grid; place-items:center; color:var(--stone); font-size:12px; }
.unavailable { align-content:center; text-align:center; }
.unavailable div { font:20px var(--serif); color:var(--ink); }
.unavailable p { max-width:470px; margin:7px auto 0; line-height:1.75; }
.sr-only { position:absolute; width:1px; height:1px; padding:0; margin:-1px; overflow:hidden; clip:rect(0,0,0,0); white-space:nowrap; border:0; }
@media (max-width:680px) { .knowledge-stats { align-items:flex-start; flex-direction:column; gap:5px; } }
</style>
