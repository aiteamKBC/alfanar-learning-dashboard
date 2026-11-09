import { createRouter, createWebHistory } from 'vue-router'
import { loadSession, session, setUnauthorizedHandler } from './api/client'

export const router = createRouter({
  history: createWebHistory(),
  scrollBehavior: (_to, _from, saved) => saved ?? { top: 0 },
  routes: [
    { path: '/', name: 'overview', component: () => import('./views/ExecutiveOverview.vue'), meta: { title: 'Executive Overview', section: 'overview' } },
    { path: '/learners', name: 'learners', component: () => import('./views/LearnersView.vue'), meta: { title: 'Learners', section: 'learners' } },
    { path: '/lectures', name: 'lectures', component: () => import('./views/LecturesView.vue'), meta: { title: 'Lectures', section: 'lectures' } },
    { path: '/learners/:id(\\d+)', name: 'learner', component: () => import('./views/LearnerDetailView.vue'), meta: { title: 'Learner profile', section: 'learners' } },
    { path: '/login', name: 'login', component: () => import('./views/LoginView.vue'), meta: { title: 'Sign in', public: true } },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

router.beforeEach(async (to) => {
  if (!session.checked) {
    try { await loadSession() } catch { session.checked = true }
  }
  if (!to.meta.public && !session.authenticated) return { name: 'login', query: { next: to.fullPath } }
  if (to.name === 'login' && session.authenticated) return { name: 'overview' }
})

router.afterEach((to) => {
  document.title = `${(to.meta.title as string) ?? 'Dashboard'} · Alfanar Learning Dashboard`
})

setUnauthorizedHandler(() => {
  const current = router.currentRoute.value
  if (!current.meta.public) router.replace({ name: 'login', query: { next: current.fullPath } })
})
