<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { getEvents, getTask, retryTask } from '../../api/procurement-review'
import type { ReviewTask } from '../../types/procurement-review'

const route = useRoute()
const router = useRouter()
const task = ref<ReviewTask>()
const error = ref('')
const loadError = ref('')
const retrying = ref(false)
let timer: number | undefined
let lastEventId: string | undefined

function progressValue() {
  const value = Number(task.value?.progress)
  return Number.isFinite(value) ? Math.round(Math.max(0, Math.min(100, value)) * 10) / 10 : 0
}
function batchProgressLabel() {
  if (task.value?.progress_step !== 'extract_candidates' || !task.value.batch_total) return ''
  return `已审完 ${task.value.batch_completed ?? 0} / ${task.value.batch_total} 批`
}
function isRunning() { return ['queued', 'parsing', 'reviewing'].includes(task.value?.status ?? '') }
function degradedLabels() {
  const labels: Record<string, string> = {
    match_rules: '未配置可执行规则库',
    match_legal_applicability: '法规适用性门禁未启用',
    validate_evidence: '部分问题的证据仍需人工确认',
  }
  return (task.value?.degraded_steps ?? []).map(item => labels[item.step]).filter(Boolean)
}
function taskPhase() {
  if (!task.value) return '等待处理'
  if (task.value.status === 'queued') return '等待解析'
  if (task.value.status === 'parsing') return '文档解析与质量检查'
  if (task.value.status === 'reviewing') return progressStepLabel(task.value.progress_step)
  if (task.value.status === 'applicability_review') return '法规适用性门禁已停用，请重新处理任务'
  if (task.value.status === 'completed') return task.value.pipeline_status === 'degraded' ? '审查已完成，部分能力降级' : '审查已完成'
  return 'AI 审查完成，正在人工复核'
}
function progressStepLabel(step?: string) {
  return ({
    parse_documents: 'MinerU 文档解析', quality_check: '解析质量检查', structure_profile: '文档结构理解',
    build_logical_units: '逻辑单元重建', assemble_review_batches: 'Review Batch 校验', extract_candidates: '大模型分批业务理解',
    build_ledger: '全局归并与采购台账', build_scene_view: '采购主题视图', global_validation: '文件全局检查',
    match_rules: '规则与全量法规装载', agent_review: '采购文件专业审查', validate_evidence: '独立证据校验', final_report: '生成审查结果',
  } as Record<string, string>)[step ?? ''] || '采购文件专业审查'
}
function phaseState(index: number) {
  const status = task.value?.status
  if (status === 'queued') return ''
  if (status === 'parsing') return index === 0 ? 'current' : ''
  if (status === 'reviewing') {
    const step = task.value?.progress_step
    const current = ['parse_documents', 'quality_check'].includes(step ?? '') ? 1
      : ['structure_profile', 'build_logical_units', 'assemble_review_batches', 'extract_candidates', 'build_ledger', 'build_scene_view'].includes(step ?? '') ? 2
        : ['global_validation', 'match_rules', 'agent_review', 'validate_evidence', 'final_report'].includes(step ?? '') ? 3 : 1
    return index < current ? 'done' : index === current ? 'current' : ''
  }
  if (status === 'applicability_review') return index < 2 ? 'done' : index === 2 ? 'current' : ''
  return index < 4 ? 'done' : index === 4 ? 'current' : ''
}

async function load() {
  try {
    const projectId = String(route.params.projectId)
    const taskId = String(route.params.taskId)
    const serverTask = await getTask(projectId, taskId)
    if (!serverTask) return
    task.value = serverTask
    loadError.value = ''
    const events = await getEvents(projectId, taskId, lastEventId)
    lastEventId = events.at(-1)?.id ?? lastEventId
  } catch {
    loadError.value = '进度读取失败，请稍后重试。'
  }
}

async function doRetry() {
  if (!task.value) return
  retrying.value = true
  error.value = ''
  try {
    task.value = await retryTask(task.value.project_id, task.value.id)
    error.value = ''
  } catch (e: any) {
    error.value = e.message || '重试失败'
  } finally {
    retrying.value = false
  }
}

onMounted(async () => {
  await load()
  timer = window.setInterval(load, 2000)
})
onUnmounted(() => clearInterval(timer))
</script>

<template>
  <div class="page-head">
    <div class="crumb">
      <a href="#" @click.prevent="router.push('/projects')">审查任务</a>
      / 解析与 AI 审查进度
    </div>
    <h2>正在准备候选问题与证据</h2>
    <p>解析质量、进度和失败原因均由后端任务状态决定；页面每 2 秒读取一次进度。</p>
  </div>

  <div class="page-body">
    <div v-if="error || loadError" class="state-card error-state" role="alert" style="margin-bottom:20px">
      <b>操作未完成</b>
      <div style="margin-top:8px;font-size:12px;line-height:1.7;word-break:break-word">{{ error || loadError }}</div>
    </div>

    <div v-if="!task" class="state-card error-state">资源不存在或尚未加载。</div>

    <div v-else class="progress-card">
      <!-- Failed state -->
      <template v-if="task.status === 'failed'">
        <span class="pill done">处理失败</span>
        <h2>{{ task.title }}</h2>
        <p>{{ task.document?.file_name }}</p>

        <div class="state-card error-state" style="margin:20px 0">
          <b>审查流程未能完成</b>
          <div style="margin-top:8px;font-size:12px;line-height:1.7;word-break:break-all">{{ task.error || '未知错误，请查看后端日志。' }}</div>
        </div>

        <div style="display:flex;gap:10px">
          <button class="btn" @click="router.push({ name: 'procurement-project', params: { projectId: String(route.params.projectId) } })">
            &larr; 返回项目
          </button>
          <button class="btn pri" :disabled="retrying" @click="doRetry">
            {{ retrying ? '重试中…' : '重新处理' }}
          </button>
        </div>
      </template>

      <!-- Running state -->
      <template v-else>
        <span class="pill doing">{{ isRunning() ? '处理中' : task.pipeline_status === 'degraded' ? 'AI初审完成（部分能力降级）' : 'AI初审完成' }}</span>
        <h2>{{ task.title }}</h2>
        <p>{{ task.document?.file_name }}</p>

        <div class="progress" :class="{ running: isRunning() }" style="margin:25px 0 8px" :aria-label="`${taskPhase()}，${progressValue()}%`">
          <i :style="{ width: `${progressValue()}%` }"></i>
        </div>
        <div style="display:flex;justify-content:space-between;font-size:11px;color:var(--stone)">
          <span>{{ taskPhase() }}<template v-if="batchProgressLabel()"> · {{ batchProgressLabel() }}</template></span>
          <b style="color:var(--terra)">{{ progressValue() }}%</b>
        </div>

        <div v-if="task.pipeline_status === 'degraded'" class="state-card" style="margin:18px 0">
          <b>本次 AI 初审存在能力降级</b>
          <div style="margin-top:8px;font-size:12px;line-height:1.7">{{ degradedLabels().join('；') }}</div>
        </div>

        <ol class="timeline">
          <li :class="phaseState(0)">文件安全校验与登记</li>
          <li :class="phaseState(1)">文档解析与质量检查</li>
          <li :class="phaseState(2)">业务理解与采购台账</li>
          <li :class="phaseState(3)">全量法规装载、专业审查与证据校验</li>
          <li :class="phaseState(4)">等待人工确认</li>
        </ol>

        <button
          v-if="task.status === 'applicability_review' || progressValue() >= 100 || task.status === 'operator_review'"
          class="btn pri"
          @click="router.push({ name: 'procurement-workbench', params: { projectId: task.project_id, taskId: task.id } })"
        >
          {{ task.status === 'applicability_review' ? '确认适用法规' : '进入审核工作台' }}
        </button>
      </template>
    </div>
  </div>
</template>
