<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()

const navSections = [
  {
    section: '审查中心',
    items: [
      { id: 'tasks', icon: 'tasks', label: '审查任务', to: '/projects' },
      { id: 'workbench', icon: 'search', label: '审查工作台', to: '/projects', lock: false },
    ],
  },
  {
    section: '项目闭环',
    items: [
      { id: 'rereview', icon: 'refresh', label: '整改核销', to: '/rectification', lock: false },
      { id: 'archive', icon: 'folder', label: '项目档案', to: '/projects', lock: false },
    ],
  },
  {
    section: '知识资产',
    items: [
      { id: 'kb', icon: 'book', label: '知识库', to: '/knowledge', lock: false },
      { id: 'cases', icon: 'bulb', label: '经验案例库', to: '/cases', lock: false },
      { id: 'fix', icon: 'wrench', label: '强制纠偏', to: '/corrections', lock: false },
    ],
  },
  {
    section: '治理',
    items: [
      { id: 'perm', icon: 'key', label: '用户与权限', to: '/admin/users', lock: false, adminOnly: true },
    ],
  },
]

// SVG icons (Lucide-style)
const icons: Record<string, string> = {
  leaf: '<path d="M11 20A7 7 0 0 1 9.8 6.1C15.5 5 17 4.48 19 2c1 2 2 4.18 2 8 0 5.5-4.78 10-10 10Z"/><path d="M2 21c0-3 1.85-5.36 5.08-6"/>',
  tasks: '<rect x="8" y="2" width="8" height="4" rx="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><path d="M12 11h4"/><path d="M12 16h4"/><path d="M8 11h.01"/><path d="M8 16h.01"/>',
  search: '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
  refresh: '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/>',
  folder: '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>',
  book: '<path d="M12 7v14"/><path d="M3 18a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h5a4 4 0 0 1 4 4 4 4 0 0 1 4-4h5a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1h-6a3 3 0 0 0-3 3 3 3 0 0 0-3-3z"/>',
  bulb: '<path d="M15 14c.2-1 .7-1.7 1.5-2.5 1-.9 1.5-2.2 1.5-3.5A6 6 0 0 0 6 8c0 1 .2 2.2 1.5 3.5.7.7 1.3 1.5 1.5 2.5"/><path d="M9 18h6"/><path d="M10 22h4"/>',
  wrench: '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>',
  key: '<path d="m15.5 7.5 2.3 2.3a1 1 0 0 0 1.4 0l2.1-2.1a1 1 0 0 0 0-1.4L19 4"/><path d="m21 2-9.6 9.6"/><circle cx="7.5" cy="15.5" r="5.5"/>',
  dot: '<circle cx="12" cy="12" r="3"/>',
  filetext: '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M16 13H8"/><path d="M16 17H8"/><path d="M10 9H8"/>',
  chart: '<path d="M3 3v18h18"/><path d="M18 17V9"/><path d="M13 17V5"/><path d="M8 17v-3"/>',
}

function iconSvg(name: string, size = 18) {
  const paths = icons[name] || icons.dot
  return `<svg class="ic-svg" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${paths}</svg>`
}

const isAdmin = computed(() => auth.user?.roles.some((r) => r.code === 'admin'))

function isActive(navId: string, to: string) {
  if (route.path === to) return true
  return false
}

function navClick(item: { id: string; to: string; lock?: boolean }) {
  if (item.lock) return
  router.push(item.to)
}

onMounted(async () => {
  if (!(await auth.restore())) {
    router.replace('/login')
  }
})

function logout() {
  auth.logout()
  router.push('/login')
}
</script>

<template>
  <div class="shell">
    <!-- Header -->
    <header>
      <div class="brand">
        <div class="logo" v-html="iconSvg('leaf', 17)"></div>
        <div>
          <span class="bt">厦门烟草采购管理智能辅助平台</span>
          <small>AI 一期 · 最新方案交互演示</small>
        </div>
      </div>
      <div class="hright">
        <span class="accept-tag">方案对齐版 · 2026.07</span>
        <div class="rolepill">
          <div class="av">{{ auth.user?.display_name?.slice(0, 1) ?? '·' }}</div>
          <div class="rn">
            {{ auth.user?.display_name ?? '正在验证身份' }}
            <small>{{ auth.user?.department }} · {{ auth.user?.roles[0]?.name ?? '身份验证中' }}</small>
          </div>
        </div>
        <button class="btn" style="background:#1d1c1a;border-color:var(--dark-surf);color:var(--ivory);font-size:10px;padding:4px 10px" @click="logout">退出</button>
      </div>
    </header>

    <!-- Main: Sidebar + Content -->
    <div class="main">
      <nav class="sidebar">
        <template v-for="section in navSections" :key="section.section">
          <div class="navsec">{{ section.section }}</div>
          <button
            v-for="item in section.items"
            v-show="!item.adminOnly || isAdmin"
            :key="item.id"
            class="navitem"
            :class="{ on: isActive(item.id, item.to), locked: item.lock }"
            @click="navClick(item)"
          >
            <span class="ic" v-html="iconSvg(item.icon, 17)"></span>
            {{ item.label }}
            <span v-if="item.lock" class="lk">🔒</span>
            <span v-if="item.id === 'perm' && isAdmin" class="badge-lite">管理</span>
          </button>
        </template>
      </nav>
      <div class="content">
        <RouterView />
      </div>
    </div>
  </div>
</template>
