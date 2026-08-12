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
  return Number.isFinite(value) ? Math.max(0, Math.min(100, value)) : 0
}
function taskPhase() {
  if (!task.value) return '等待处理'
  if (task.value.status === 'queued') return '等待解析'
  if (task.value.status === 'parsing') return '文档解析与质量检查'
  if (task.value.status === 'reviewing') return 'AI 审查与适用法规匹配'
  if (task.value.status === 'completed') return '审查已完成'
  return '适用法规匹配完成，正在人工复核'
}
function phaseState(index: number) {
  const status = task.value?.status
  if (status === 'queued') return ''
  if (status === 'parsing') return index === 0 ? 'current' : ''
  if (status === 'reviewing') return index < 2 ? 'done' : index === 2 ? 'current' : ''
  return index < 4 ? 'done' : 'current'
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
        <span class="pill doing">处理中</span>
        <h2>{{ task.title }}</h2>
        <p>{{ task.document?.file_name }}</p>

        <div class="progress" style="margin:25px 0 8px">
          <i :style="{ width: `${progressValue()}%` }"></i>
        </div>
        <div style="display:flex;justify-content:space-between;font-size:11px;color:var(--stone)">
          <span>{{ taskPhase() }}</span>
          <b style="color:var(--terra)">{{ progressValue() }}%</b>
        </div>

        <ol class="timeline">
          <li :class="phaseState(0)">文件安全校验与登记</li>
          <li :class="phaseState(1)">文档解析与质量检查</li>
          <li :class="phaseState(2)">适用法规匹配</li>
          <li :class="phaseState(3)">AI 审查与证据校验</li>
          <li :class="phaseState(4)">等待人工确认</li>
        </ol>

        <button
          v-if="progressValue() >= 100 || task.status === 'operator_review'"
          class="btn pri"
          @click="router.push({ name: 'procurement-workbench', params: { projectId: task.project_id, taskId: task.id } })"
        >
          进入审核工作台
        </button>
      </template>
    </div>
  </div>
</template>
