<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { getEvents, getTask, usingMockProcurementService } from '../../services/procurement-review'
import type { ReviewTask } from '../../types/procurement-review'

const route = useRoute()
const router = useRouter()
const task = ref<ReviewTask>()
const error = ref('')
let timer: number | undefined
let lastEventId: string | undefined

async function load() {
  try {
    const projectId = String(route.params.projectId)
    const taskId = String(route.params.taskId)
    const serverTask = await getTask(projectId, taskId)
    if (!serverTask) return
    if (usingMockProcurementService && task.value?.status === 'parsing') {
      serverTask.progress = Math.min(100, (task.value.progress ?? 0) + 20)
      if (serverTask.progress >= 100) serverTask.status = 'reviewing'
    }
    task.value = serverTask
    if (!usingMockProcurementService) {
      const events = await getEvents(projectId, taskId, lastEventId)
      lastEventId = events.at(-1)?.id ?? lastEventId
    }
  } catch {
    error.value = '进度读取失败，请稍后重试。'
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
    <div v-if="!task" class="state-card error-state">资源不存在或尚未加载。</div>

    <div v-else class="progress-card">
      <span class="pill" :class="task.status === 'failed' ? 'done' : 'doing'">
        {{ task.status === 'failed' ? '处理失败' : '处理中' }}
      </span>
      <h2>{{ task.title }}</h2>
      <p>{{ task.document?.file_name }}</p>

      <div class="progress" style="margin:25px 0 8px">
        <i :style="{ width: `${task.progress ?? 0}%` }"></i>
      </div>
      <div style="display:flex;justify-content:space-between;font-size:11px;color:var(--stone)">
        <span>{{ (task.progress ?? 0) < 35 ? '文件安全校验与登记' : (task.progress ?? 0) < 65 ? '文档解析与质量检查' : (task.progress ?? 0) < 88 ? 'AI 审查与证据校验' : '等待人工确认' }}</span>
        <b style="color:var(--terra)">{{ task.progress ?? 0 }}%</b>
      </div>

      <ol class="timeline">
        <li :class="{ done: (task.progress ?? 0) >= 20 }">文件安全校验与登记</li>
        <li :class="{ done: (task.progress ?? 0) >= 45 }">文档解析与质量检查</li>
        <li :class="{ done: (task.progress ?? 0) >= 80 }">AI 审查与证据校验</li>
        <li :class="{ done: (task.progress ?? 0) >= 100 }">等待人工确认</li>
      </ol>

      <button
        v-if="(task.progress ?? 0) >= 100 || task.status === 'operator_review'"
        class="btn pri"
        @click="router.push({ name: 'procurement-workbench', params: { projectId: task.project_id, taskId: task.id } })"
      >
        进入审核工作台
      </button>
    </div>
  </div>
</template>
