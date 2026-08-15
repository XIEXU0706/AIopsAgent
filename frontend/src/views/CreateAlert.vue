<template>
  <el-card>
    <div slot="header" style="display: flex; justify-content: space-between; align-items: center;">
      <span>提交告警事件</span>
      <el-button size="small" @click="$emit('back')">← 返回列表</el-button>
    </div>

    <el-alert title="粘贴原始告警数据（JSON 或纯文本），Agent 会自动分析。" type="info" :closable="false" show-icon style="margin-bottom: 16px;" />

    <el-form :model="form" label-width="100px">
      <el-form-item label="告警来源">
        <el-select v-model="form.source" style="width: 100%">
          <el-option label="Prometheus" value="prometheus" />
          <el-option label="Elastic" value="elastic" />
          <el-option label="Zabbix" value="zabbix" />
          <el-option label="自定义" value="custom" />
        </el-select>
      </el-form-item>

      <el-form-item label="原始数据">
        <el-input
          v-model="form.raw_input"
          type="textarea"
          :rows="10"
          placeholder='支持 JSON 格式：{"alertname":"CPUHigh","instance":"web-01","value":95}
或纯文本格式：Host: db-01, Connections: 850/1000, max_connections reached'
        />
      </el-form-item>

      <el-form-item>
        <el-button type="primary" @click="submit" :loading="submitting">
          提交分析
        </el-button>
        <el-button @click="reset">重置</el-button>
        <el-button @click="fillSampleJSON" type="text">示例 JSON</el-button>
        <el-button @click="fillSampleText" type="text">示例纯文本</el-button>
      </el-form-item>
    </el-form>

    <el-tag v-if="inputType" :type="inputType === 'json' ? 'success' : 'info'" size="small" style="margin-bottom: 12px;">
      {{ inputType === 'json' ? '检测到 JSON 格式' : '检测到纯文本格式' }}
    </el-tag>

    <!-- 预览提取结果 -->
    <div v-if="preview" class="preview-box">
      <p style="font-weight: 600; margin: 0 0 8px; font-size: 13px;">📋 解析后的告警数据</p>
      <el-descriptions :column="2" size="small" border>
        <el-descriptions-item label="标题">{{ preview.title }}</el-descriptions-item>
        <el-descriptions-item label="来源">{{ preview.source }}</el-descriptions-item>
        <el-descriptions-item label="严重级别">{{ preview.severity }}</el-descriptions-item>
        <el-descriptions-item label="错误类型">{{ preview.error_type }}</el-descriptions-item>
        <el-descriptions-item label="告警消息" :span="2">{{ preview.message }}</el-descriptions-item>
      </el-descriptions>
    </div>

    <div v-if="result" style="margin-top: 20px; padding: 16px; background: #f6ffed; border-radius: 4px;">
      <p style="font-weight: 600; color: #52c41a;">✅ 告警已提交，Agent 分析中...</p>
      <p>告警 ID: {{ result }}</p>
    </div>
  </el-card>
</template>

<script>
import { severityLabel } from '../utils/severity'

export default {
  data() {
    return {
      form: {
        source: 'prometheus',
        raw_input: '',
      },
      submitting: false,
      result: null,
      preview: null,
    }
  },
  computed: {
    inputType() {
      if (!this.form.raw_input.trim()) return null
      try {
        JSON.parse(this.form.raw_input)
        return 'json'
      } catch {
        return 'text'
      }
    },
  },
  methods: {
    severityLabel,
    fillSampleJSON() {
      this.form.source = 'prometheus'
      this.form.raw_input = JSON.stringify({
        alertname: 'MySQL连接数暴涨',
        instance: 'db-01',
        job: 'mysql',
        severity: 'critical',
        value: 850,
        threshold: 1000,
        message: 'Host: db-01, Connections: 850/1000, max_connections reached',
        labels: { team: 'dba', env: 'production' },
      }, null, 2)
      this.parsePreview()
    },
    fillSampleText() {
      this.form.source = 'custom'
      this.form.raw_input = 'Host: web-02, CPU usage: 95%, memory: 78%, disk: /data 85%\nError: connection timeout after 30s\nProcess count: 1024, open files: 65535'
      this.parsePreview()
    },
    parsePreview() {
      const text = this.form.raw_input
      if (!text.trim()) { this.preview = null; return }

      try {
        const raw = JSON.parse(text)
        this.preview = {
          title: raw.alertname || raw.title || raw.event_type || '未命名告警',
          source: this.form.source,
          severity: this.severityLabel(raw.severity || raw.level || 'warning'),
          error_type: raw.error_type || this._detectErrorType(raw),
          message: raw.message || raw.description || raw.msg || text.slice(0, 200),
        }
      } catch {
        this.preview = {
          title: '原始告警',
          source: this.form.source,
          severity: this.severityLabel(text),
          error_type: this._detectErrorType({ message: text }),
          message: text.slice(0, 500),
        }
      }
    },
    _detectErrorType(raw) {
      const msg = (raw.message || raw.description || '').toLowerCase()
      if (msg.includes('connection') || msg.includes('connect')) return 'mysql_connection'
      if (msg.includes('oom') || msg.includes('memory')) return 'redis_oom'
      if (msg.includes('cpu')) return 'high_cpu'
      if (msg.includes('disk')) return 'disk_full'
      return 'custom'
    },
    async submit() {
      this.submitting = true
      this.result = null
      try {
        const text = this.form.raw_input
        if (!text.trim()) {
          this.$message.warning('请粘贴告警数据')
          this.submitting = false
          return
        }

        this.parsePreview()

        let rawData

        try {
          // JSON 格式
          rawData = JSON.parse(text)
        } catch {
          // 纯文本 → 作为 message 字段放入 raw_data
          rawData = { message: text.slice(0, 500) }
        }

        const body = {
          source: this.form.source,
          raw_data: rawData,
        }

        const res = await fetch('/api/v1/alerts', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        })
        const data = await res.json()
        this.result = data.alert_id
        this.$emit('created', data.alert_id)
      } catch (e) {
        this.$message.error('提交失败: ' + e.message)
      }
      this.submitting = false
    },
    reset() {
      this.form = { source: 'prometheus', raw_input: '' }
      this.result = null
      this.preview = null
    },
  },
}
</script>

<style scoped>
.preview-box {
  margin-top: 16px;
  padding: 14px;
  background: #fafbfc;
  border: 1px solid #e8eaed;
  border-radius: 6px;
}
</style>
