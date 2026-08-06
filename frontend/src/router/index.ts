import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', name: 'login', component: () => import('../views/LoginView.vue') },
    {
      path: '/',
      component: () => import('../layouts/AppLayout.vue'),
      meta: { requiresAuth: true },
      children: [
        { path: '', redirect: '/projects' },
        { path: 'procurement-reviews', redirect: '/projects' },
        {
          path: 'projects',
          name: 'projects',
          component: () => import('../views/procurement-review/ProjectListView.vue'),
        },
        {
          path: 'projects/:projectId',
          name: 'procurement-project',
          component: () => import('../views/procurement-review/ProjectDetailView.vue'),
        },
        {
          path: 'projects/:projectId/tasks/:taskId/progress',
          name: 'procurement-progress',
          component: () => import('../views/procurement-review/TaskProgressView.vue'),
        },
        {
          path: 'projects/:projectId/tasks/:taskId/workbench',
          name: 'procurement-workbench',
          component: () => import('../views/procurement-review/ReviewWorkbenchView.vue'),
        },
        {
          path: 'projects/:projectId/archive',
          name: 'project-archive',
          component: () => import('../views/procurement-review/LockedFeatureView.vue'),
          props: { title: '项目档案', detail: '项目档案将在采购文件审查闭环稳定后开放。' },
        },
        {
          path: 'rectification',
          name: 'rectification',
          component: () => import('../views/procurement-review/LockedFeatureView.vue'),
          props: { title: '整改核销', detail: '整改核销仅在形成整改问题后开放。' },
        },
        {
          path: 'knowledge',
          name: 'knowledge',
          component: () => import('../views/procurement-review/LockedFeatureView.vue'),
          props: { title: '知识库', detail: '知识治理能力暂未开放。' },
        },
        {
          path: 'cases',
          name: 'cases',
          component: () => import('../views/procurement-review/LockedFeatureView.vue'),
          props: { title: '经验案例库', detail: '经验案例库暂未开放。' },
        },
        {
          path: 'corrections',
          name: 'corrections',
          component: () => import('../views/procurement-review/LockedFeatureView.vue'),
          props: { title: '强制纠偏', detail: '强制纠偏能力暂未开放。' },
        },
        {
          path: 'admin/users',
          name: 'admin-users',
          component: () => import('../views/procurement-review/LockedFeatureView.vue'),
          props: { title: '用户与权限', detail: '用户与权限管理暂未开放。' },
        },
      ],
    },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

router.beforeEach((to) => {
  if (to.meta.requiresAuth && !localStorage.getItem('access_token')) return '/login'
})

export default router
