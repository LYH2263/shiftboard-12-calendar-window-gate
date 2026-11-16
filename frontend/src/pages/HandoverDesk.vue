<script setup>
import { onMounted, ref, watch } from 'vue'
import { getJson, postJson } from '../api'

const shifts = ref([])
const fromId = ref(1)
const toId = ref(2)
const current = ref(null)
const status = ref(null)
const msg = ref('')
const ok = ref(false)

async function loadStatus() {
  try {
    status.value = await getJson(
      `/api/handovers/window-status?from_shift_id=${Number(fromId.value)}&to_shift_id=${Number(toId.value)}`,
    )
  } catch (e) {
    status.value = null
    msg.value = String(e.message || e)
  }
}

async function refresh() {
  shifts.value = await getJson('/api/shifts')
  const logs = await getJson('/api/handovers')
  current.value = logs.find((h) => h.status === '进行中') || null
  if (current.value) {
    current.value = await getJson(`/api/handovers/${current.value.id}`)
    fromId.value = current.value.from_shift_id
    toId.value = current.value.to_shift_id
  }
  await loadStatus()
}

onMounted(async () => {
  try {
    await refresh()
  } catch (e) {
    msg.value = String(e.message || e)
  }
})

watch([fromId, toId], () => {
  if (!current.value) loadStatus()
})

async function start() {
  msg.value = ''
  ok.value = false
  try {
    current.value = await postJson('/api/handovers/start', {
      from_shift_id: Number(fromId.value),
      to_shift_id: Number(toId.value),
    })
    ok.value = true
    msg.value = `已开交班单 #${current.value.id}，清单 ${current.value.batch_count} 批`
    await refresh()
  } catch (e) {
    msg.value = String(e.message || e)
    await loadStatus()
  }
}

async function complete() {
  if (!current.value) return
  msg.value = ''
  ok.value = false
  try {
    current.value = await postJson(`/api/handovers/${current.value.id}/complete`)
    ok.value = current.value.status === '已完成'
    msg.value = current.value.status === '已完成'
      ? `交班单 #${current.value.id} 已完成，批次已整批过户`
      : `交班单 #${current.value.id} 失败：${current.value.reason || '未知原因'}`
    await refresh()
  } catch (e) {
    msg.value = String(e.message || e)
  }
}
</script>

<template>
  <div>
    <h1>交班办理</h1>
    <p class="lead">
      同一时刻只允许一单进行中的交班；发起与完成都必须落在「交班方日历窗 ∩ 接班方日历窗」内，
      清单内在途批次整批过户。完成时若已出窗，本单判失败且不过户。
    </p>

    <div class="panel">
      <div class="row">
        <label>交班方
          <select v-model.number="fromId" :disabled="!!current">
            <option v-for="s in shifts" :key="s.id" :value="s.id">{{ s.name }} · {{ s.keeper }}</option>
          </select>
        </label>
        <label>接班方
          <select v-model.number="toId" :disabled="!!current">
            <option v-for="s in shifts" :key="'t'+s.id" :value="s.id">{{ s.name }} · {{ s.keeper }}</option>
          </select>
        </label>
        <button class="btn" :disabled="!!current || (status && !status.in_window)" @click="start">
          发起交班
        </button>
        <button class="btn ghost" :disabled="!current" @click="complete">完成交班</button>
      </div>

      <div class="window-box" v-if="status">
        <div class="window-line">
          <span class="tag" :class="{ on: status.in_window }">
            {{ status.in_window ? '双方均在交班日历窗内' : '不在交班日历窗内，禁止交班' }}
          </span>
          <span class="muted">裁决时刻 {{ status.at_text }} · 本地墙钟</span>
        </div>
        <div class="window-cols">
          <div>
            <div class="window-k">交班方 · {{ status.from.name }}</div>
            <div class="window-v">{{ status.from.windows_text }}</div>
            <span class="tag" :class="{ on: status.from.in_window }">
              {{ status.from.in_window ? '在窗内' : '出窗' }}
            </span>
          </div>
          <div>
            <div class="window-k">接班方 · {{ status.to.name }}</div>
            <div class="window-v">{{ status.to.windows_text }}</div>
            <span class="tag" :class="{ on: status.to.in_window }">
              {{ status.to.in_window ? '在窗内' : '出窗' }}
            </span>
          </div>
        </div>
        <p class="msg" v-if="!status.in_window">{{ status.reason }}</p>
        <p class="hint" v-else-if="status.next_shared && status.next_shared.length">
          双方交集最近：{{ status.next_shared[0].text }}
        </p>
      </div>

      <p class="msg" :class="{ ok }">{{ msg }}</p>
    </div>

    <div class="panel" v-if="current">
      <h2>当前交班单 #{{ current.id }} · {{ current.status }}</h2>
      <p class="lead" style="margin-bottom:12px">
        {{ current.from_shift_name }} → {{ current.to_shift_name }} · 清单 {{ current.batch_count }} 批
      </p>
      <div class="window-line" v-if="current.start_snapshot">
        <span class="tag" :class="{ on: current.start_snapshot.in_window }">
          发起时{{ current.start_snapshot.in_window ? '在窗' : '出窗' }}
        </span>
        <span class="muted">
          快照 {{ current.start_snapshot.decision_at }} · 交班方 {{ current.start_snapshot.from_windows_text }}
          · 接班方 {{ current.start_snapshot.to_windows_text }}
        </span>
      </div>
      <table>
        <thead><tr><th>业务键</th><th>标题</th><th>状态</th></tr></thead>
        <tbody>
          <tr v-for="b in current.items || []" :key="b.id">
            <td>{{ b.biz_key }}</td>
            <td>{{ b.title }}</td>
            <td>{{ b.status }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
