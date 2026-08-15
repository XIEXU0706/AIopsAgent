<template>
  <div class="alert-center">
    <div class="page-header">
      <div>
        <h2>告警中心</h2>
        <p class="page-desc">告警事件列表与 Agent 处置状态跟踪</p>
      </div>
      <div class="header-actions">
        <el-button size="small" @click="$emit('view-dashboard')">← 返回仪表盘</el-button>
        <el-button type="primary" size="small" @click="$emit('create')">+ 新告警</el-button>
      </div>
    </div>

    <el-card shadow="hover">
      <el-table :data="alerts" v-loading="loading" stripe style="width: 100%">
        <el-table-column prop="id" label="ID" width="200" />
        <el-table-column label="标题" min-width="200">
          <template slot-scope="{ row }">
            <el-tag :type="severityTag(row.severity)" size="mini" style="margin-right: 8px">
              {{ severityLabel(row.severity) }}
            </el-tag>
            {{ row.title || fallbackTitle(row) }}
          </template>
        </el-table-column>
        <el-table-column prop="source" label="来源" width="100" />
        <el-table-column label="错误类型" width="130">
          <template slot-scope="{ row }">
            {{ row.error_type || fallbackErrorType(row) }}
          </template>
        </el-table-column>
        <el-table-column prop="create_time" label="时间" width="180" />
        <el-table-column label="状态" width="110">
          <template slot-scope="{ row }">
            <el-tag :type="statusTag(row.status)" size="mini">
              {{ statusText(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="120" fixed="right">
          <template slot-scope="{ row }">
            <el-button type="text" size="small" @click="viewDetail(row)" :disabled="row.status === 'processing'">
              {{ row.status === 'processing' ? '分析中...' : '查看分析' }}
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script>
import { severityLabel } from '../utils/severity'

export default {
  data() {
    return {
      alerts: [],
      loading: false,
      pollTimer: null,
    }
  },
  mounted() {
    this.loadAlerts()
  },
  beforeDestroy() {
    if (this.pollTimer) clearInterval(this.pollTimer)
  },
  methods: {
    severityLabel,
    severityTag(s) {
      return { critical: 'danger', warning: 'warning', info: 'info' }[s] || 'info'
    },
    statusTag(s) {
      return { completed: 'success', processing: 'warning', error: 'danger' }[s] || 'info'
    },
    statusText(s) {
      return { completed: '已分析', processing: '分析中', error: '失败' }[s] || s
    },
    async loadAlerts() {
      this.loading = true
      try {
        const res = await fetch('/api/v1/alerts')
        if (res.ok) {
          this.alerts = await res.json()
        }
      } catch (e) {
        this.$message.error('加载告警列表失败: ' + e.message)
      }
      this.loading = false

      const hasProcessing = this.alerts.some(a => a.status === 'processing')
      if (hasProcessing) {
        this.startPolling()
      } else {
        this.stopPolling()
      }
    },
    startPolling() {
      if (this.pollTimer) return
      this.pollTimer = setInterval(() => this.loadAlerts(), 3000)
    },
    stopPolling() {
      if (this.pollTimer) {
        clearInterval(this.pollTimer)
        this.pollTimer = null
      }
    },
    fallbackTitle(row) {
      if (!row.raw_data) return '未知告警'
      return row.raw_data.alertname || row.raw_data.title || row.raw_data.event_type || '原始告警'
    },
    fallbackErrorType(row) {
      if (!row.raw_data) return '-'
      const raw = row.raw_data
      const msg = (raw.message || raw.description || '').toLowerCase()
      if (msg.includes('connection') || msg.includes('connect')) return 'mysql_connection'
      if (msg.includes('oom') || msg.includes('memory')) return 'redis_oom'
      if (msg.includes('cpu')) return 'high_cpu'
      if (msg.includes('disk')) return 'disk_full'
      return 'custom'
    },
    viewDetail(row) {
      this.$emit('view-detail', row.id)
    },
  },
}
</script>

<style scoped>
.alert-center {
  max-width: 1400px;
  margin: 0 auto;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 20px;
}

.page-header h2 {
  margin: 0;
  font-size: 22px;
}

.page-desc {
  margin: 4px 0 0;
  color: #999;
  font-size: 13px;
}

.header-actions {
  display: flex;
  gap: 8px;
}
</style>
