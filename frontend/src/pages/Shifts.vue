<script setup>
import { computed, onMounted, ref } from 'vue'
import { getJson, putJson } from '../api'

const rows = ref([])
const err = ref('')
const msg = ref('')
const ok = ref(false)
const editingId = ref(null)
const draft = ref([])
const saving = ref(false)

const WEEKDAYS = [
  { value: 1, label: '周一' },
  { value: 2, label: '周二' },
  { value: 3, label: '周三' },
  { value: 4, label: '周四' },
  { value: 5, label: '周五' },
  { value: 6, label: '周六' },
  { value: 7, label: '周日' },
]

function weekdayLabel(v) {
  return WEEKDAYS.find((d) => d.value === Number(v))?.label || '—'
}

async function load() {
  rows.value = await getJson('/api/shifts')
}

onMounted(async () => {
  try {
    await load()
  } catch (e) {
    err.value = String(e.message || e)
  }
})

const editing = computed(() => rows.value.find((s) => s.id === editingId.value) || null)

function beginEdit(shift) {
  msg.value = ''
  ok.value = false
  editingId.value = shift.id
  draft.value = (shift.windows || []).map((w) => ({
    weekday: w.weekday,
    start: w.start,
    end: w.end,
  }))
}

function addWindow() {
  draft.value.push({ weekday: 1, start: '08:00', end: '09:00' })
}

function removeWindow(i) {
  draft.value.splice(i, 1)
}

function cancelEdit() {
  editingId.value = null
  draft.value = []
}

async function save() {
  if (!editing.value) return
  saving.value = true
  msg.value = ''
  ok.value = false
  try {
    await putJson(`/api/shifts/${editingId.value}/windows`, {
      windows: draft.value.map((w) => ({
        weekday: Number(w.weekday),
        start: w.start,
        end: w.end,
      })),
    })
    ok.value = true
    msg.value = `「${editing.value.name}」交班日历窗已保存；已完成单的裁决快照不受影响。`
    editingId.value = null
    await load()
  } catch (e) {
    msg.value = String(e.message || e)
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div>
    <h1>班次</h1>
    <p class="lead">
      每个班次有固定值班人；交班只能在交班方日历窗与接班方日历窗的交集内发起和完成。
      日历窗按本地墙钟裁决，未配置窗口即禁止交班。
    </p>
    <p v-if="err" class="msg">{{ err }}</p>
    <p v-if="msg" class="msg" :class="{ ok }">{{ msg }}</p>

    <div class="panel">
      <table>
        <thead>
          <tr>
            <th>编号</th><th>班次</th><th>值班人</th><th>状态</th>
            <th>交班日历窗（本地墙钟）</th><th>当前是否在窗</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="s in rows" :key="s.id">
            <td>{{ s.id }}</td>
            <td>{{ s.name }}</td>
            <td>{{ s.keeper }}</td>
            <td>{{ s.status }}</td>
            <td>{{ s.windows_text }}</td>
            <td>
              <span class="tag" :class="{ on: s.in_window }">{{ s.in_window ? '在窗内' : '出窗' }}</span>
            </td>
            <td><button class="btn ghost" @click="beginEdit(s)">配置日历窗</button></td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="panel" v-if="editing">
      <h2>配置「{{ editing.name }}」交班日历窗</h2>
      <p class="lead" style="margin-bottom:12px">
        每行一段窗：星期几 + 起止时刻（HH:MM，结束可为 24:00）。结束早于起始视为跨午夜；
        清空全部行表示该班次不可交班。
      </p>
      <table>
        <thead><tr><th>星期</th><th>起</th><th>止</th><th>操作</th></tr></thead>
        <tbody>
          <tr v-for="(w, i) in draft" :key="i">
            <td>
              <select v-model.number="w.weekday">
                <option v-for="d in WEEKDAYS" :key="d.value" :value="d.value">{{ d.label }}</option>
              </select>
            </td>
            <td><input class="time" v-model="w.start" placeholder="08:00" /></td>
            <td><input class="time" v-model="w.end" placeholder="09:00" /></td>
            <td><button class="btn ghost" @click="removeWindow(i)">删除</button></td>
          </tr>
        </tbody>
      </table>
      <div class="row" style="margin-top:14px">
        <button class="btn ghost" @click="addWindow">添加一段</button>
        <button class="btn" :disabled="saving" @click="save">保存</button>
        <button class="btn ghost" @click="cancelEdit">取消</button>
      </div>
      <p class="hint">当前草稿：{{ draft.length ? draft.map((w) => weekdayLabel(w.weekday) + ' ' + w.start + '–' + w.end).join('；') : '空（禁止交班）' }}</p>
    </div>
  </div>
</template>
