<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import FindingCard from '../../components/business/FindingCard.vue'
import { confirmTask, createCollaborativeComment, getEvents, getFindings, getTask, saveDisposition, savePrimaryDecision, submitTask, updateCollaborativeComment } from '../../api/procurement-review'
import { useAuthStore } from '../../stores/auth'
import type { Finding, ReviewTask, RiskLevel } from '../../types/procurement-review'

const route = useRoute()
const auth = useAuthStore()
const task = ref<ReviewTask>()
const findings = ref<Finding[]>([])
const filter = ref<'all' | RiskLevel>('all')
const selected = ref('')
const notice = ref('')
const busy = ref(false)
const events = ref<{ id: string }[]>([])

const isReadonly = computed(() => task.value?.status === 'completed')
const mode = computed<'operator' | 'primary_supervisor' | 'collaborative_supervisor' | 'readonly'>(() => {
  if (isReadonly.value || auth.user?.roles[0]?.code === 'admin') return 'readonly'
  return (task.value?.task_role as any) ?? 'readonly'
})
const visible = computed(() => findings.value.filter((item) => filter.value === 'all' || item.risk_level === filter.value))
const rechecks = computed(() => findings.value.filter((item) => item.recheck_required && !item.primary_decision))
const selectedFinding = computed(() => findings.value.find((item) => item.id === selected.value) ?? findings.value[0])
const cardMode = (item: Finding) => task.value?.status === 'primary_recheck' && !item.recheck_required ? 'readonly' : mode.value

async function load() {
  const projectId = String(route.params.projectId)
  const taskId = String(route.params.taskId)
  task.value = await getTask(projectId, taskId)
  if (!task.value) return
  findings.value = await getFindings(projectId, taskId)
  events.value = await getEvents(projectId, taskId)
  selected.value ||= findings.value[0]?.id ?? ''
}

function setFilter(value: string) { filter.value = value as 'all' | RiskLevel }

function message(error: unknown) {
  const status = (error as { status?: number }).status
  return status === 409 ? '数据已被他人修改，请刷新后重新确认。' : status === 403 ? '无权执行该操作。' : status === 422 ? '填写内容不符合要求。' : '操作失败，请稍后重试。'
}

async function disposition(item: Finding, action: 'accept' | 'partial_accept' | 'reject' | 'edit', comment: string) {
  if (!task.value) return; busy.value = true
  try { await saveDisposition(task.value.project_id, task.value.id, item.id, action, comment, item.version); notice.value = '经办处置已保存。'; await load() }
  catch (error) { notice.value = message(error) } finally { busy.value = false }
}

async function primary(item: Finding, decision: 'receive' | 'adjust' | 'reject', comment: string, risk: RiskLevel | undefined) {
  if (!task.value) return; busy.value = true
  try { await savePrimaryDecision(task.value.project_id, task.value.id, item.id, decision, comment, risk, item.version); notice.value = '主责复核意见已保存。'; await load() }
  catch (error) { notice.value = message(error) } finally { busy.value = false }
}

async function collaborative(item: Finding, comment: string) {
  if (!task.value) return
  const own = item.collaborative_comments.find((entry) => entry.author === auth.user?.display_name)
  try {
    if (own) await updateCollaborativeComment(task.value.project_id, task.value.id, item.id, own.id, comment, own.version)
    else await createCollaborativeComment(task.value.project_id, task.value.id, item.id, comment, item.version)
    notice.value = '协同监督意见已保存。'; await load()
  } catch (error) { notice.value = message(error) }
}

async function submit() {
  if (!task.value) return; busy.value = true
  try { await submitTask(task.value.project_id, task.value.id); notice.value = '已提交采购部门主责监督复核。'; await load() }
  catch (error) { notice.value = message(error) } finally { busy.value = false }
}

async function confirm() {
  if (!task.value) return; busy.value = true
  try { await confirmTask(task.value.project_id, task.value.id); notice.value = '正式复核结果已确认，任务已锁定为只读。'; await load() }
  catch (error) { notice.value = message(error) } finally { busy.value = false }
}

const statusLabel: Record<string, string> = {
  draft: '草稿', parsing: '解析中', reviewing: 'AI 审查中',
  operator_review: '待经办处理', primary_review: '待主责复核',
  primary_recheck: '待主责再次复核', completed: '已完成',
  failed: '处理失败', cancelled: '已取消', queued: '排队中',
}

const filterLabels: [string, string][] = [
  ['all', '全部'], ['high', '高风险'], ['medium', '中风险'], ['low', '低风险'], ['unknown', '证据不足'],
]

function onDisposition(action: 'accept' | 'partial_accept' | 'reject' | 'edit', comment: string) { if (selectedFinding.value) disposition(selectedFinding.value, action, comment) }
function onDecision(decision: 'receive' | 'adjust' | 'reject', comment: string, risk: RiskLevel | undefined) { if (selectedFinding.value) primary(selectedFinding.value, decision, comment, risk) }
function onOpinion(comment: string) { if (selectedFinding.value) collaborative(selectedFinding.value, comment) }

onMounted(load)
</script>

<template>
  <!-- Page Head -->
  <div class="page-head">
    <div class="crumb">审查任务 / {{ task?.project_id }} / 采购文件审核</div>
    <div class="page-title-row">
      <h2>
        {{ task?.title ?? '审查工作台' }}
        <span class="title-status">{{ isReadonly ? '已完成 · 只读' : statusLabel[task?.status ?? ''] ?? '审查进行中' }}</span>
      </h2>
    </div>
    <p>AI 输出仅为候选问题，原文、规则依据与人工处置在同一审查记录中留痕。</p>
  </div>

  <!-- Page Body -->
  <div class="page-body workbench-page">
    <div v-if="notice" class="status-banner">{{ notice }}</div>
    <div v-if="!task" class="state-card error-state">资源不存在或无权查看。</div>

    <template v-else>
      <!-- Review Actions Bar -->
      <div class="review-actions">
        <div class="ra-t">
          <b>当前阶段：</b>
          {{ isReadonly ? '正式复核已确认' : task.status === 'operator_review' ? '业务经办处置 AI 候选问题' : task.status === 'primary_recheck' ? '主责监督再次复核受影响条目' : '采购部门主责监督复核' }}
        </div>
        <span class="sp" />
        <button
          v-if="mode === 'operator'"
          class="btn pri"
          :disabled="busy || findings.some((item) => !item.operator_disposition)"
          @click="submit"
        >
          确认审查结果
        </button>
        <button
          v-if="mode === 'primary_supervisor'"
          class="btn pri"
          :disabled="busy || (task.status === 'primary_recheck' ? rechecks.length > 0 : findings.some((item) => !item.primary_decision))"
          @click="confirm"
        >
          确认复核结果
        </button>
      </div>

      <div v-if="isReadonly" class="status-banner done">此任务已完成，所有写入动作均已禁用；可继续查看完整证据链和审计记录。</div>

      <!-- Three Column Workbench -->
      <div class="wb">
        <!-- Left: Document Column -->
        <div class="wcol">
          <div class="whead">
            <span>采购文档</span>
            <span class="ch-meta">{{ task.document?.file_name }}</span>
          </div>
          <div class="wbody">
            <div class="doc-item sel">
              <div class="dt">
                <svg class="ic-svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/></svg>
                采购文件
                <span class="role">主文件</span>
              </div>
              <div class="dm">{{ task.document?.file_name }} · V1</div>
            </div>

            <div class="clause hot">
              <div class="clabel">采购文件 §5.3 · 保证金条款 · 当前定位</div>
              中标人须按合同金额 5% 缴纳<span class="hl">履约保证金</span>，并<span class="hl">另行缴纳质量保证金</span> 3%。
            </div>
            <div class="clause">
              <div class="clabel">采购文件 §4.2 · 交货要求</div>
              交货期：合同签订后 <span class="hl">尽快交付</span>。
            </div>
            <div class="clause">
              <div class="clabel">采购文件 §8.1 · 付款期限</div>
              验收合格并收到发票后 <span class="hl">90 日内</span>完成付款。
            </div>
            <div class="clause">
              <div class="clabel">采购文件 §6.1 · 质量与质保</div>
              货物应符合国家及行业质量标准，质保期不少于 12 个月。
            </div>
            <div class="clause">
              <div class="clabel">采购文件 §7.2 · 验收要求</div>
              到货后 5 个工作日内，按照技术指标、数量和外观要求组织抽样验收。
            </div>
          </div>
        </div>

        <!-- Middle: Review Findings Column -->
        <div class="wcol">
          <div class="whead">
            <span>审查结论</span>
            <span class="ch-meta">{{ findings.length }} 项 AI 候选</span>
          </div>
          <div style="display:flex;align-items:center;gap:5px;flex-wrap:wrap;padding:10px 12px;border-bottom:1px solid var(--border);background:var(--ivory)">
            <span style="font-size:10px;color:var(--stone);margin-right:4px">风险筛选</span>
            <button
              v-for="[key, label] in filterLabels"
              :key="key"
              class="filter-btn"
              :class="{ on: filter === key }"
              @click="setFilter(key)"
            >{{ label }}</button>
          </div>
          <div class="wbody">
            <button
              v-for="item in visible"
              :key="item.id"
              style="display:grid;grid-template-columns:auto minmax(0,1fr) auto;gap:7px;width:100%;padding:9px 10px;border:1px solid transparent;border-radius:8px;background:transparent;color:var(--ink2);text-align:left;font-size:11px;cursor:pointer;margin-bottom:2px"
              :style="{
                borderColor: selected === item.id ? 'var(--terra)' : 'transparent',
                background: selected === item.id ? 'var(--terra-soft)' : 'transparent',
                boxShadow: item.recheck_required ? 'inset 2px 0 var(--ochre)' : 'none',
              }"
              @click="selected = item.id"
            >
              <span
                style="width:7px;height:7px;margin-top:4px;border-radius:50%"
                :style="{ background: { high: 'var(--crimson)', medium: 'var(--ochre)', low: 'var(--green)', unknown: 'var(--stone)' }[item.risk_level] }"
              />
              <span>{{ item.title }}</span>
              <small style="color:var(--stone);font-size:9px">{{ { high: '高风险', medium: '中风险', low: '低风险', unknown: '证据不足' }[item.risk_level] }}</small>
            </button>

            <FindingCard
              v-if="selectedFinding"
              :finding="selectedFinding"
              :mode="cardMode(selectedFinding)"
              @disposition="onDisposition"
              @decision="onDecision"
              @opinion="onOpinion"
            />

            <!-- Audit Trail -->
            <div style="margin-top:13px;padding:12px;border-top:1px dashed var(--ring);font-size:10.5px;color:var(--olive)">
              <h3 style="margin:0 0 9px;color:var(--olive);font:11px var(--serif)">审计时间线</h3>
              <p style="position:relative;margin:8px 0;padding-left:13px"><i style="position:absolute;left:0;top:4px;width:6px;height:6px;border-radius:50%;background:var(--terra)" />AI 审查生成候选问题与证据</p>
              <p v-for="event in events.slice(0, 4)" :key="event.id" style="position:relative;margin:8px 0;padding-left:13px"><i style="position:absolute;left:0;top:4px;width:6px;height:6px;border-radius:50%;background:var(--terra)" />任务事件 {{ event.id }}</p>
              <p style="position:relative;margin:8px 0;padding-left:13px"><i style="position:absolute;left:0;top:4px;width:6px;height:6px;border-radius:50%;background:var(--terra)" />当前状态：{{ statusLabel[task.status] ?? task.status }}</p>
            </div>
          </div>
        </div>

        <!-- Right: Evidence Column -->
        <div class="wcol">
          <div class="whead"><span>证据链 / 审查依据</span></div>
          <div class="wbody">
            <div style="font-size:11px;color:var(--stone);margin-bottom:12px">系统已自动匹配审查依据</div>

            <div style="border:1px solid var(--border);border-radius:10px;padding:11px 12px;margin-bottom:12px;background:var(--ivory)">
              <b style="display:block;font-size:11.5px;color:var(--ink)">系统已自动匹配 2 份现行有效制度</b>
              <span style="display:block;margin-top:4px;font-size:10.5px;color:var(--stone);line-height:1.55">依据项目类型和当前审查文件自动匹配</span>
            </div>

            <div style="font-size:10.5px;font-weight:700;color:var(--ink2);margin:12px 2px 7px">制度依据 <span style="font-weight:400;color:var(--stone)">2 份 · 现行有效</span></div>
            <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:6px;padding:8px 10px;border:1px solid var(--border2);border-radius:9px;background:var(--white);font-size:10.5px;color:var(--ink2)">
              <span>采购管理办法</span><small style="flex:none;color:var(--green)">内部制度</small>
            </div>
            <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:6px;padding:8px 10px;border:1px solid var(--border2);border-radius:9px;background:var(--white);font-size:10.5px;color:var(--ink2)">
              <span>卷烟材料采购规范</span><small style="flex:none;color:var(--green)">业务规范</small>
            </div>

            <div style="font-size:10.5px;font-weight:700;color:var(--ink2);margin:12px 2px 7px">当前结论命中规则 <span style="font-weight:400;color:var(--stone)">{{ selectedFinding?.rule_refs.length ?? 0 }} 条</span></div>
            <div v-for="rule in selectedFinding?.rule_refs" :key="rule.id" class="rule" style="padding:11px;box-shadow:none">
              <span class="rid">{{ rule.id }}</span>
              <div class="rt" style="font-size:13px">{{ rule.title }}</div>
              <div class="rmeta">
                <span class="chip r">强制</span>
                <span class="chip">置信度 高</span>
              </div>
            </div>

            <div class="note" style="font-size:10.5px">
              <b>文档层</b>提供法规原文，<b>规则层</b>精确命中规则编号，<b>经验案例层</b>关联已确认违规案例。
            </div>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>
