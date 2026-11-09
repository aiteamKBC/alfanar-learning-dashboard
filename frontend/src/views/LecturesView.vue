<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, qs } from '@/api/client'
import { useAsync } from '@/api/useAsync'
import type { FiltersResponse } from '@/api/types'
import type { LectureDetail, LectureList, LectureParticipant } from '@/api/lectures'
import { lectureDate, lectureDuration, lectureTime } from '@/api/lectures'
import Avatar from '@/components/common/Avatar.vue'
import FilterSelect from '@/components/common/FilterSelect.vue'
import Icon from '@/components/common/Icon.vue'
import StateBlock from '@/components/common/StateBlock.vue'

const route = useRoute()
const router = useRouter()
const q = (key: string) => typeof route.query[key] === 'string' ? route.query[key] as string : ''
function updateQuery(patch: Record<string, string | undefined>) {
  const query = { ...route.query, ...patch }
  for (const key in query) if (!query[key]) delete query[key]
  router.replace({ query })
}
const course = computed({ get: () => q('course'), set: (value: string) => updateQuery({ course: value, lecture: undefined, page: undefined }) })
const from = computed({ get: () => q('from'), set: (value: string) => updateQuery({ from: value, lecture: undefined, page: undefined }) })
const to = computed({ get: () => q('to'), set: (value: string) => updateQuery({ to: value, lecture: undefined, page: undefined }) })
const page = computed(() => Math.max(1, Number(q('page')) || 1))
const filters = useAsync(() => api.get<FiltersResponse>('filters'))
const list = useAsync(() => api.get<LectureList>(`lectures${qs({ course: course.value, from: from.value, to: to.value, page: page.value, page_size: 100 })}`), [course, from, to, page])
const activeId = computed(() => {
  if (list.loading.value) return ''
  const rows = list.data.value?.results ?? []
  return q('lecture') || rows[0]?.id || ''
})
const detail = useAsync<LectureDetail | null>(() => activeId.value ? api.get<LectureDetail>(`lectures/${activeId.value}`) : Promise.resolve(null), [activeId])
function select(id: string) { updateQuery({ lecture: id }) }
const participantSearch = ref('')
const participantPage = ref(1)
const participantsPerPage = 8
watch([participantSearch, activeId], () => { participantPage.value = 1 })
const playing = ref<number | null>(null)
const transcriptId = ref<number | null>(null)
const videoError = ref(false)
watch(activeId, () => { playing.value = null; transcriptId.value = null; videoError.value = false; participantSearch.value = '' })
const current = computed(() => detail.data.value)
watch(current, lecture => {
  if (lecture && q('lecture') && lecture.id !== q('lecture')) updateQuery({ lecture: lecture.id })
})
const selectionOutsidePage = computed(() => current.value && !(list.data.value?.results ?? []).some(row => row.id === current.value?.id))
const recordingFiles = computed(() => current.value?.resources.filter(x => x.kind === 'recording') ?? [])
const recordings = computed(() => recordingFiles.value.filter(x => x.available))
const transcripts = computed(() => current.value?.resources.filter(x => x.kind === 'transcript' && x.available) ?? [])
const player = computed(() => recordings.value.find(x => x.id === playing.value))
const transcript = computed(() => transcripts.value.find(x => x.id === transcriptId.value) ?? transcripts.value[0])
const participants = computed(() => {
  const term = participantSearch.value.trim().toLocaleLowerCase()
  return (current.value?.participants ?? []).filter(x => !term || `${x.name} ${x.learner_id} ${x.email}`.toLocaleLowerCase().includes(term))
})
const participantPages = computed(() => Math.max(1, Math.ceil(participants.value.length / participantsPerPage)))
watch(participantPages, pages => { participantPage.value = Math.min(participantPage.value, pages) })
const participantOffset = computed(() => (participantPage.value - 1) * participantsPerPage)
const visibleParticipants = computed(() => participants.value.slice(participantOffset.value, participantOffset.value + participantsPerPage))
function play(id: number) { videoError.value = false; playing.value = id }
const statusText: Record<LectureParticipant['attendance'], string> = { present: 'Present', absent: 'Absent', review: 'Short visit', not_available: 'Not available' }
const statusStyle: Record<LectureParticipant['attendance'], string> = {
  present: 'bg-brand-soft text-brand', absent: 'bg-red-50 text-red-700', review: 'bg-amber-50 text-amber-800', not_available: 'bg-subtle text-ink-3',
}
function speakingNote(learner: LectureParticipant) {
  if (learner.speaking_seconds === null) return 'No readable transcript or no unambiguous speaker match for this learner.'
  const basis = learner.speaking_match === 'verified_identity' ? 'Transcript speech attributed through an explicitly verified participant identity.' : 'Estimated transcript duration: exact speaker label matched to one email-identified participant in this lecture.'
  return basis + (learner.speaking_partial ? ' Some segments fell outside recorded presence and were excluded.' : '')
}
</script>

<template>
  <div class="space-y-5">
    <div class="flex flex-wrap items-end justify-between gap-3">
      <div>
        <p class="eyebrow">Lectures</p>
        <h1 class="mt-1 text-[26px] font-semibold leading-tight text-ink">Every lecture, in one place</h1>
        <p class="mt-1.5 text-sm text-ink-3">Recordings, transcripts and participation for the learners in your directory.</p>
      </div>
    </div>

    <div class="panel flex flex-wrap items-end gap-3 p-4">
      <FilterSelect v-model="course" label="Module" all-label="All modules" :options="(filters.data.value?.courses ?? []).map(x => ({ value: x.course_id, label: x.title }))" />
      <label class="flex min-w-36 flex-1 flex-col gap-1"><span class="eyebrow">From date</span><input v-model="from" type="date" :max="to || undefined" class="focus-ring h-9 rounded-md border border-line bg-white px-3 text-sm" /></label>
      <label class="flex min-w-36 flex-1 flex-col gap-1"><span class="eyebrow">To date</span><input v-model="to" type="date" :min="from || undefined" class="focus-ring h-9 rounded-md border border-line bg-white px-3 text-sm" /></label>
      <button v-if="course || from || to" class="focus-ring inline-flex h-9 items-center gap-1.5 rounded-md px-3 text-xs text-ink-3 hover:bg-subtle" @click="updateQuery({ course: undefined, from: undefined, to: undefined, lecture: undefined, page: undefined })"><Icon name="x" :size="14" />Clear filters</button>
    </div>

    <StateBlock :loading="list.loading.value && !list.data.value" :error="list.error.value" :empty="list.data.value?.count === 0" empty-title="No lectures in this selection" empty-text="Try a different module or date range." @retry="list.reload">
      <div class="grid items-start gap-5 lg:grid-cols-[280px_minmax(0,1fr)]" :class="{ 'opacity-60': list.loading.value }">
        <aside class="panel hidden overflow-hidden lg:block" aria-label="Lecture index">
          <div class="flex items-center justify-between border-b border-line px-4 py-3.5"><h2 class="text-sm font-semibold">Lecture index</h2><span class="text-[11px] text-ink-3">Latest first</span></div>
          <div class="matrix-scroll max-h-[72vh] overflow-y-auto p-2">
            <button v-for="lecture in list.data.value?.results ?? []" :key="lecture.id" :aria-pressed="activeId === lecture.id" class="focus-ring mb-1 flex w-full gap-3 rounded-lg border p-3 text-left transition-colors" :class="activeId === lecture.id ? 'border-navy/15 bg-navy-soft' : 'border-transparent hover:bg-subtle'" @click="select(lecture.id)">
              <span class="mt-0.5 grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-white text-navy ring-1 ring-line"><Icon name="calendar" :size="16" /></span>
              <span class="min-w-0 flex-1"><span class="block text-[13px] font-semibold text-ink">{{ lectureDate(lecture.date, true) }}</span><span class="mt-1 block text-[11px] leading-relaxed text-ink-2">{{ lecture.course }}</span><span v-if="lecture.recording_session" class="mt-1 block text-[10px] font-medium text-ink-3">Recording session</span><span v-if="lecture.recordings || lecture.course_id === null" class="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[10.5px] text-ink-3"><span v-if="lecture.course_id === null">Participation not linked</span><span v-if="lecture.recordings" class="inline-flex items-center gap-1"><Icon name="video" :size="12" />{{ lecture.recordings }}</span></span></span>
              <Icon v-if="activeId === lecture.id" name="chevron" :size="13" class="mt-1 shrink-0 text-navy" />
            </button>
          </div>
          <div v-if="(list.data.value?.pages ?? 1) > 1" class="flex items-center justify-between border-t border-line p-3 text-xs"><button :disabled="page <= 1" class="focus-ring rounded border border-line px-2 py-1 disabled:opacity-30" @click="updateQuery({ page: String(page - 1), lecture: undefined })">Previous</button><span>{{ page }} / {{ list.data.value?.pages }}</span><button :disabled="page >= (list.data.value?.pages ?? 1)" class="focus-ring rounded border border-line px-2 py-1 disabled:opacity-30" @click="updateQuery({ page: String(page + 1), lecture: undefined })">Next</button></div>
        </aside>

        <section class="min-w-0 space-y-4" aria-label="Selected lecture">
          <label class="panel flex flex-col gap-2 p-4 lg:hidden"><span class="eyebrow">Choose a lecture</span><select :value="activeId" class="focus-ring h-10 w-full rounded-md border border-line bg-white px-2 text-sm" @change="select(($event.target as HTMLSelectElement).value)"><option v-if="selectionOutsidePage && current" :value="current.id">{{ lectureDate(current.date, true) }} / {{ current.course }}</option><option v-for="lecture in list.data.value?.results ?? []" :key="lecture.id" :value="lecture.id">{{ lectureDate(lecture.date, true) }} · {{ lecture.course }}</option></select></label>

          <StateBlock :loading="detail.loading.value || !activeId" :error="detail.error.value" height="30rem" @retry="detail.reload">
            <div v-if="current" class="space-y-4">
              <div class="panel overflow-hidden">
                <div class="flex flex-wrap items-start justify-between gap-3 border-b border-line p-5">
                  <div><p class="eyebrow">Selected lecture</p><h2 class="font-heading mt-1.5 text-[23px] font-semibold">{{ lectureDate(current.date) }}</h2><p class="mt-1 text-sm text-ink-2">{{ current.course }}</p><p v-if="current.recording_session" class="mt-2 text-xs text-ink-3">{{ current.course_id === null ? 'Archive recording; module and meeting unconfirmed. Time comes from the original recording filename.' : 'Recording session; attendance may be incomplete' }}</p><p v-if="current.starts_at" class="mt-2 inline-flex items-center gap-1.5 text-xs text-ink-3"><Icon name="clock" :size="13" />{{ lectureTime(current.starts_at) }}<span v-if="current.ends_at">– {{ lectureTime(current.ends_at) }}</span><span>· Cairo</span></p></div>
                  <span v-if="current.course_id === null" class="rounded-full border border-line bg-subtle px-3 py-1 text-[11px] font-medium text-ink-2">Module unconfirmed</span>
                </div>
                <div v-if="current.course_id !== null" class="grid divide-y divide-line sm:grid-cols-3 sm:divide-x sm:divide-y-0">
                  <div class="p-4"><p class="eyebrow">Present</p><p class="font-heading mt-2 text-xl font-semibold tabular">{{ current.present }}</p></div>
                  <div class="p-4"><p class="eyebrow">Attendance evidence</p><p class="font-heading mt-2 text-xl font-semibold tabular">{{ current.recorded }}<span class="ml-1 text-sm font-normal text-ink-3">learners</span></p></div>
                  <div class="p-4"><p class="eyebrow">Speaking time from transcript</p><p class="font-heading mt-2 text-xl font-semibold tabular">{{ current.speaking_covered }}<span class="ml-1 text-sm font-normal text-ink-3">learners</span></p></div>
                </div>
              </div>

              <div class="grid gap-3 xl:grid-cols-2">
                <div class="panel flex items-start gap-3 p-4">
                  <span class="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-navy-soft text-navy"><Icon name="video" :size="20" /></span>
                  <div class="min-w-0 flex-1"><h3 class="text-sm font-semibold">Lecture recordings</h3><p class="mt-1 text-xs text-ink-3">{{ recordingFiles.length ? `${recordingFiles.length} recording ${recordingFiles.length === 1 ? 'part' : 'parts'} listed${recordings.length !== recordingFiles.length ? `; ${recordings.length} playable with current access` : ''}` : 'No recording linked to this lecture yet' }}</p><div v-if="recordingFiles.length" class="mt-3 flex flex-wrap gap-2"><button v-for="(asset, index) in recordingFiles" :key="asset.id" :disabled="!asset.available" :title="asset.available ? undefined : 'Microsoft lists this recording, but playback could not be accessed during the last check.'" class="focus-ring inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-xs font-medium disabled:cursor-not-allowed disabled:opacity-50" :class="playing === asset.id ? 'border-navy bg-navy text-white' : 'border-line bg-white text-navy hover:bg-navy-soft'" @click="play(asset.id)"><Icon name="play" :size="12" />{{ !asset.available ? `Part ${index + 1}: access unavailable` : recordingFiles.length > 1 ? `Part ${index + 1}` : 'Play recording' }}</button></div><a v-if="current.recording_folder_url" :href="current.recording_folder_url" target="_blank" rel="noopener noreferrer" class="focus-ring mt-3 inline-flex items-center gap-1 text-[11px] text-ink-3 underline underline-offset-2">Open recordings folder<Icon name="external" :size="11" /></a></div>
                </div>
                <div class="panel flex items-start gap-3 p-4">
                  <span class="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-brand-soft text-brand"><Icon name="sheet" :size="19" /></span>
                  <div class="min-w-0 flex-1"><h3 class="text-sm font-semibold">Lecture transcripts</h3><p class="mt-1 text-xs text-ink-3">{{ transcripts.length ? `${transcripts.length} readable ${transcripts.length === 1 ? 'transcript' : 'transcripts'}` : current.transcripts ? 'A transcript is listed, but its content could not be retrieved' : 'No transcript linked to this lecture yet' }}</p><div v-if="transcript?.url" class="mt-3 flex flex-wrap items-center gap-2"><select v-if="transcripts.length > 1" v-model="transcriptId" aria-label="Transcript part" class="focus-ring h-8 max-w-36 rounded-md border border-line bg-white px-2 text-xs"><option :value="null">First transcript</option><option v-for="(asset, index) in transcripts" :key="asset.id" :value="asset.id">Part {{ index + 1 }}</option></select><a :href="transcript.url" class="focus-ring inline-flex items-center gap-1.5 rounded-md border border-line px-2.5 py-1.5 text-xs font-medium text-brand hover:bg-brand-soft"><Icon name="download" :size="13" />Download text</a><a :href="`${transcript.url}?format=vtt`" class="focus-ring rounded-md px-2 py-1.5 text-xs text-ink-3 hover:bg-subtle">VTT</a></div></div>
                </div>
              </div>

              <div v-if="!current.recordings && !current.transcripts && current.source.startsWith('Historical')" class="flex items-start gap-2 rounded-lg border border-line bg-white px-4 py-3 text-xs leading-relaxed text-ink-3"><Icon name="info" :size="15" class="mt-0.5 shrink-0" /><p>Attendance is available from the historical archive. Recording and transcript files have not been retrieved and linked for this lecture; this does not confirm that no files exist.</p></div>

              <div v-if="player?.url" class="panel overflow-hidden">
                <div class="flex items-center justify-between px-4 py-3"><h3 class="text-sm font-semibold">Lecture recording · Part {{ recordingFiles.findIndex(x => x.id === playing) + 1 }}</h3><button aria-label="Close recording" class="focus-ring rounded-md p-1 text-ink-3 hover:bg-subtle" @click="playing = null"><Icon name="x" :size="16" /></button></div>
                <video :key="player.id" controls preload="metadata" class="aspect-video w-full bg-ink" :src="player.url" @error="videoError = true">Your browser does not support video playback.</video>
                <p v-if="videoError" role="alert" class="p-4 text-sm text-red-700">This recording could not be opened. Try the recordings folder or another recording part.</p>
              </div>

              <div class="panel overflow-hidden">
                <div class="flex flex-wrap items-center justify-between gap-3 border-b border-line p-4"><div><h3 class="text-sm font-semibold">Learner participation</h3><p class="mt-1 text-xs text-ink-3">{{ current.course_id === null ? 'The module has not been confirmed, so no learners have been attributed to this recording.' : 'Only directory learners enrolled in this module are shown.' }}</p></div><label class="relative"><span class="sr-only">Search lecture learners</span><Icon name="search" :size="14" class="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-3" /><input v-model="participantSearch" type="search" placeholder="Find a learner" class="focus-ring h-8 w-48 rounded-md border border-line bg-white pl-8 pr-2 text-xs" /></label></div>
                <div class="matrix-scroll overflow-x-auto">
                  <table class="w-full min-w-[800px] text-left text-xs">
                    <thead class="border-b border-line bg-subtle text-[9.5px] uppercase tracking-wider text-ink-3"><tr><th class="px-4 py-3 font-semibold">Learner</th><th class="px-3 py-3 font-semibold">Attendance</th><th class="px-3 py-3 font-semibold">First join</th><th class="px-3 py-3 font-semibold">Last leave</th><th class="px-3 py-3 font-semibold">In lecture</th><th class="px-3 py-3 font-semibold">Speaking</th></tr></thead>
                    <tbody class="divide-y divide-line"><tr v-for="learner in visibleParticipants" :key="learner.learner_id" class="hover:bg-canvas/60"><td class="px-4 py-3.5"><RouterLink :to="`/learners/${learner.learner_id}`" class="focus-ring flex items-center gap-2.5 rounded-sm"><Avatar :name="learner.name" :size="32" /><span class="min-w-0"><span class="block max-w-48 truncate text-[12px] font-semibold text-ink" :title="learner.name">{{ learner.name }}</span><span class="mt-0.5 block text-[10px] text-ink-3">ID {{ learner.learner_id }}</span></span></RouterLink></td><td class="px-3 py-3"><span class="inline-flex whitespace-nowrap rounded-full px-2 py-1 text-[10px] font-medium" :class="statusStyle[learner.attendance]">{{ statusText[learner.attendance] }}</span></td><td class="whitespace-nowrap px-3 py-3 tabular" :class="learner.first_join ? 'text-ink-2' : 'text-ink-3'"><span :title="learner.first_join ? lectureDate(learner.first_join, true) : undefined">{{ lectureTime(learner.first_join) }}</span></td><td class="whitespace-nowrap px-3 py-3 tabular" :class="learner.last_leave ? 'text-ink-2' : 'text-ink-3'"><span :title="learner.last_leave ? lectureDate(learner.last_leave, true) : undefined">{{ lectureTime(learner.last_leave) }}</span></td><td class="whitespace-nowrap px-3 py-3 tabular" :class="learner.duration_seconds === null ? 'text-ink-3' : 'text-ink-2'">{{ lectureDuration(learner.duration_seconds) }}</td><td class="whitespace-nowrap px-3 py-3 tabular" :class="learner.speaking_seconds === null ? 'text-ink-3' : 'font-medium text-brand'" :title="speakingNote(learner)"><span>{{ lectureDuration(learner.speaking_seconds) }}</span><span v-if="learner.speaking_seconds !== null" class="mt-0.5 block text-[9px] font-normal text-ink-3">{{ learner.speaking_partial ? 'Partial transcript estimate' : learner.speaking_match === 'meeting_name' ? 'Transcript estimate' : 'Verified identity' }}</span></td></tr><tr v-if="!participants.length"><td colspan="6" class="px-4 py-10 text-center text-ink-3">{{ current.course_id === null ? 'Learner participation is unavailable until the module and meeting are verified.' : 'No learners match this search.' }}</td></tr></tbody>
                  </table>
                </div>
                <nav v-if="participants.length" aria-label="Learner participation pages" class="flex flex-wrap items-center justify-between gap-3 border-t border-line px-4 py-3 text-xs">
                  <p role="status" aria-live="polite" class="text-ink-3">Learners {{ participantOffset + 1 }}–{{ Math.min(participantOffset + participantsPerPage, participants.length) }}</p>
                  <div v-if="participantPages > 1" class="flex items-center gap-3">
                    <button :disabled="participantPage === 1" class="focus-ring rounded-md border border-line px-3 py-1.5 text-ink-2 hover:bg-subtle disabled:cursor-not-allowed disabled:opacity-30" @click="participantPage--">Previous</button>
                    <span class="tabular text-ink-3">{{ participantPage }} / {{ participantPages }}</span>
                    <button :disabled="participantPage === participantPages" class="focus-ring rounded-md border border-line px-3 py-1.5 text-ink-2 hover:bg-subtle disabled:cursor-not-allowed disabled:opacity-30" @click="participantPage++">Next</button>
                  </div>
                </nav>
                <div class="flex items-start gap-2 border-t border-line bg-canvas/50 px-4 py-3 text-[11px] leading-relaxed text-ink-3"><Icon name="info" :size="14" class="mt-0.5 shrink-0" /><p>Speaking time is estimated from transcript segments matched to a unique participant in this meeting and their registered learner email. Missing speaking evidence is shown as “Not available”, never as zero or “No”.</p></div>
              </div>
            </div>
          </StateBlock>
        </section>
      </div>
    </StateBlock>
  </div>
</template>
