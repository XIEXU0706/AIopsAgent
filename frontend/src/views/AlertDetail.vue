<template>
  <div class="alert-detail">
    <!-- 顶部导航 -->
    <div class="detail-header">
      <div class="header-left">
        <el-button type="text" class="back-btn" @click="$emit('back')">
          <i class="el-icon-arrow-left"></i> 返回
        </el-button>
        <span class="header-title">告警分析报告</span>
      </div>
      <div class="header-right">
        <el-button size="small" @click="handleExport" v-if="alert && alert.status === 'completed'">
          <i class="el-icon-download"></i> 导出报告
        </el-button>
      </div>
    </div>

    <div v-loading="loading">
      <template v-if="alert">
        <!-- 进度条 -->
        <el-steps :active="stepActive" finish-status="success" simple class="status-steps"
          v-if="alert.status !== 'completed'">
          <el-step title="告警接收" icon="el-icon-download" />
          <el-step title="Agent 分析" icon="el-icon-setting" />
          <el-step title="安全审查" icon="el-icon-shield" />
          <el-step title="完成" icon="el-icon-check" />
        </el-steps>

        <!-- 处理中 / 失败 -->
        <el-alert v-if="alert.status === 'processing'" title="Agent 正在分析告警..." type="warning" :closable="false"
          show-icon class="status-alert" />
        <el-alert v-else-if="alert.status === 'error'" title="分析失败" type="error" :closable="false" show-icon
          class="status-alert" />

        <!-- 实时处理日志（仅 processing 时显示） -->
        <el-card v-if="alert.status === 'processing' && eventLog.length > 0" shadow="never" class="section-card">
          <div slot="header" class="section-header">
            <i class="el-icon-loading" style="color: #e6a23c;"></i>
            <span>实时处理日志</span>
            <el-tag size="mini" type="warning">{{ eventLog.length }} 条事件</el-tag>
          </div>
          <div class="event-log">
            <div v-for="(evt, i) in eventLog" :key="i" class="event-item">
              <span class="event-time">{{ evt.timestamp ? evt.timestamp.slice(11, 19) : '' }}</span>
              <span class="event-type" :class="'evt-' + evt.type">{{ evt.type }}</span>
              <span class="event-summary">{{ eventSummary(evt) }}</span>
            </div>
          </div>
        </el-card>

        <!-- ======== 完成后的完整报告 ======== -->
        <template v-if="alert.status === 'completed'">
          <!-- 顶部指标卡 -->
          <div class="metric-row">
            <div class="metric-card metric-severity" :class="'severity-' + normalizeSeverity(alert.severity)">
              <div class="metric-icon">
                <i class="el-icon-warning"></i>
              </div>
              <div class="metric-body">
                <span class="metric-label">严重级别</span>
                <span class="metric-value">{{ severityLabel(alert.severity) }}</span>
              </div>
            </div>
            <div class="metric-card">
              <div class="metric-icon icon-blue">
                <i class="el-icon-sort"></i>
              </div>
              <div class="metric-body">
                <span class="metric-label">错误类型</span>
                <span class="metric-value">{{ alert.error_type || '-' }}</span>
              </div>
            </div>
            <div class="metric-card">
              <div class="metric-icon icon-green">
                <i class="el-icon-time"></i>
              </div>
              <div class="metric-body">
                <span class="metric-label">处理耗时</span>
                <span class="metric-value">{{ alert.duration_ms || '-' }} <small v-if="alert.duration_ms">ms</small></span>
              </div>
            </div>
            <div class="metric-card">
              <div class="metric-icon icon-orange">
                <i class="el-icon-share"></i>
              </div>
              <div class="metric-body">
                <span class="metric-label">来源</span>
                <span class="metric-value">{{ alert.source || '-' }}</span>
              </div>
            </div>
          </div>

          <!-- 分析流程时间线 -->
          <el-card shadow="never" class="section-card">
            <div slot="header" class="section-header">
              <i class="el-icon-s-claim"></i>
              <span>分析流程</span>
              <el-tag size="mini" type="success" v-if="!alert.has_safety_intercept">已通过</el-tag>
              <el-tag size="mini" type="danger" v-else>已拦截</el-tag>
            </div>
            <el-timeline>
              <el-timeline-item timestamp="Agent 接收告警" placement="top" type="primary" :icon="'el-icon-download'"
                color="#409eff">
                <div class="tl-title">告警接收</div>
                <div class="tl-desc">告警 ID: {{ alert.id }} | 来源: {{ alert.source }}</div>
              </el-timeline-item>
              <el-timeline-item timestamp="多 Agent 协同分析" placement="top" type="primary" :icon="'el-icon-setting'"
                color="#409eff">
                <div class="tl-title">Coordinator → LogAnalyzer + Retrieval</div>
                <div class="tl-desc">协调 Agent 分析告警内容，并行调日志分析和知识检索</div>
              </el-timeline-item>
              <el-timeline-item v-if="alert.safety_overlays && alert.safety_overlays.length > 0" timestamp="安全技能叠加"
                placement="top" type="warning" :icon="'el-icon-warning-outline'" color="#e6a23c">
                <div class="tl-title">高风险场景自动触发</div>
                <div class="tl-desc">已叠加 {{ alert.safety_overlays.length }} 个安全处置技能</div>
              </el-timeline-item>
              <el-timeline-item timestamp="安全审查" placement="top"
                :type="alert.has_safety_intercept ? 'danger' : 'success'" :icon="'el-icon-shield'"
                :color="alert.has_safety_intercept ? '#f56c6c' : '#67c23a'">
                <div class="tl-title">{{ alert.has_safety_intercept ? '被安全策略拦截' : '审查通过' }}</div>
                <div class="tl-desc" v-if="alert.safety_reason">{{ alert.safety_reason }}</div>
              </el-timeline-item>
            </el-timeline>
          </el-card>

          <!-- 分析结论 -->
          <el-card shadow="never" class="section-card">
            <div slot="header" class="section-header">
              <i class="el-icon-document-copy"></i>
              <span>分析结论</span>
            </div>
            <div class="conclusion-body">
              <template v-for="(sec, i) in conclusionSections">
                <!-- 告警概要行（元信息） -->
                <div v-if="sec.type === 'meta'" :key="'meta-' + i" class="meta-block">
                  <div v-for="(item, mi) in sec.items" :key="mi" class="meta-tag-row">
                    <span class="meta-tag-label">{{ item.label }}</span>
                    <span class="meta-tag-value">{{ item.value }}</span>
                  </div>
                </div>

                <!-- 其他 → markdown 渲染 -->
                <div v-else :key="'md-' + i" class="section-content" v-html="renderMarkdown(sec.content)"></div>
              </template>
            </div>
          </el-card>

          <!-- 处置计划 -->
          <el-card shadow="never" class="section-card">
            <div slot="header" class="section-header">
              <i class="el-icon-s-tools"></i>
              <span>处置计划</span>
            </div>
            <div class="markdown-body" v-html="renderMarkdown(alert.disposition_plan)"></div>
          </el-card>

          <!-- 安全审查详情 -->
          <el-card shadow="never" class="section-card">
            <div slot="header" class="section-header">
              <i class="el-icon-shield"></i>
              <span>安全审查</span>
              <el-tag :type="alert.has_safety_intercept ? 'danger' : 'success'" size="small" effect="dark"
                style="margin-left: 8px;">
                {{ alert.has_safety_intercept ? '已拦截' : '已通过' }}
              </el-tag>
            </div>
            <div class="safety-status">
              <i :class="alert.has_safety_intercept ? 'el-icon-close-notification' : 'el-icon-success'"
                :style="{ color: alert.has_safety_intercept ? '#f56c6c' : '#67c23a', fontSize: '32px' }"></i>
              <div class="safety-text">
                <span class="safety-title">{{ alert.has_safety_intercept ? '处置计划被安全策略拦截' : '处置计划已通过安全审查' }}</span>
                <span class="safety-desc" v-if="alert.safety_reason">{{ alert.safety_reason }}</span>
              </div>
            </div>
          </el-card>

          <!-- 安全技能叠加 -->
          <el-card v-if="alert.safety_overlays && alert.safety_overlays.length > 0" shadow="never" class="section-card">
            <div slot="header" class="section-header">
              <i class="el-icon-warning-outline" style="color: #e6a23c;"></i>
              <span>安全技能叠加</span>
              <el-tag type="warning" size="mini" style="margin-left: 8px;">高风险自动触发</el-tag>
            </div>
            <div v-for="(item, i) in alert.safety_overlays" :key="i" class="overlay-item">
              <div class="overlay-header">
                <i class="el-icon-caret-right"></i>
                {{ item.skill }}
              </div>
              <div v-if="item.output" class="overlay-output">
                <pre>{{ typeof item.output === 'object' ? JSON.stringify(item.output, null, 2) : item.output }}</pre>
              </div>
            </div>
          </el-card>

          <!-- 元信息 -->
          <el-card shadow="never" class="section-card meta-card">
            <div slot="header" class="section-header">
              <i class="el-icon-info"></i>
              <span>元信息</span>
            </div>
            <el-descriptions :column="2" size="small" border>
              <el-descriptions-item label="告警 ID">{{ alert.id }}</el-descriptions-item>
              <el-descriptions-item label="Trace ID">
                <span class="code-text">{{ alert.trace_id }}</span>
              </el-descriptions-item>
              <el-descriptions-item label="状态">
                <el-tag size="mini" type="success">已完成</el-tag>
              </el-descriptions-item>
              <el-descriptions-item label="处理耗时">{{ alert.duration_ms ? alert.duration_ms + 'ms' : '-' }}</el-descriptions-item>
              <el-descriptions-item label="来源">{{ alert.source || '-' }}</el-descriptions-item>
              <el-descriptions-item label="严重级别">{{ severityLabel(alert.severity) }}</el-descriptions-item>
            </el-descriptions>

            <!-- 原始数据 -->
            <div v-if="alert.raw_data && Object.keys(alert.raw_data).length" class="raw-data-section">
              <el-button type="text" @click="showRawData = !showRawData" class="raw-toggle">
                <i :class="showRawData ? 'el-icon-arrow-down' : 'el-icon-arrow-right'"></i>
                原始告警数据
                <el-tag size="mini" type="info" style="margin-left:6px">{{ Object.keys(alert.raw_data).length }} 个字段</el-tag>
              </el-button>
              <pre v-if="showRawData" class="raw-json">{{ JSON.stringify(alert.raw_data, null, 2) }}</pre>
            </div>
          </el-card>

          <!-- 相关故障案例 -->
          <el-card shadow="never" class="section-card">
            <div slot="header" class="section-header">
              <i class="el-icon-collection-tag"></i>
              <span>相关故障案例</span>
              <el-tag size="mini" type="info" v-if="alert.related_cases && alert.related_cases.length">
                {{ alert.related_cases.length }} 个案例
              </el-tag>
            </div>
            <template v-if="alert.related_cases && alert.related_cases.length">
              <div v-for="(caseItem, i) in alert.related_cases" :key="i" class="case-card">
                <div class="case-title">
                  <i class="el-icon-collection-tag"></i>
                  {{ caseItem.title }}
                </div>
                <div class="case-body">
                  <div class="case-row" v-if="caseItem.symptom">
                    <span class="case-label">症状：</span>
                    <span>{{ caseItem.symptom }}</span>
                  </div>
                  <div class="case-row" v-if="caseItem.root_cause">
                    <span class="case-label">根因：</span>
                    <span>{{ caseItem.root_cause }}</span>
                  </div>
                  <div class="case-row" v-if="caseItem.solution">
                    <span class="case-label">处置方案：</span>
                    <div class="case-solution">
                      <div v-for="(step, si) in caseSolutionSteps(caseItem.solution)" :key="si" class="solution-step">
                        <span class="step-num">{{ si + 1 }}.</span>
                        <span>{{ step }}</span>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </template>
            <el-empty v-else description="暂无相关案例" :image-size="60" />
          </el-card>

          <!-- 操作 -->
          <div class="action-bar">
            <el-button type="primary" size="medium" @click="handleApply">
              <i class="el-icon-check"></i> 采纳建议
            </el-button>
            <el-button size="medium" @click="handleReject">
              <i class="el-icon-close"></i> 拒绝建议
            </el-button>
          </div>
        </template>
      </template>

      <el-empty v-else-if="!loading" description="告警未找到" />
    </div>
  </div>
</template>

<script>
import { severityLabel, normalizeSeverity } from '../utils/severity'

export default {
  props: { alertId: String },
  data() {
    return {
      alert: null,
      loading: true,
      stepActive: 0,
      pollTimer: null,
      eventSource: null,
      eventLog: [],
      showRawData: false,
    }
  },
  computed: {
    conclusionSections() {
      if (!this.alert?.conclusion) return []
      const text = this.alert.conclusion
      const sections = []

      // Split by ## or ### headers
      const blocks = text.split(/\n(?=#{2,3}\s)/)
      for (const block of blocks) {
        if (!block.trim()) continue

        // Detect header
        const headerMatch = block.match(/^#{2,3}\s+(.+)$/m)
        const title = headerMatch ? headerMatch[1].trim() : ''
        const body = headerMatch ? block.slice(headerMatch[0].length).trim() : block.trim()

        // 告警概要 → meta tags
        if (!title && !body) continue
        if (title === '告警分析报告' || (!title && sections.length === 0 && block.includes('**告警**'))) {
          const items = []
          const metaBlock = title ? block : block
          const lineRe = /\*\*([^*]+)\*\*\s*:\s*(.+)/g
          let m
          while ((m = lineRe.exec(metaBlock)) !== null) {
            items.push({ label: m[1], value: m[2].trim() })
          }
          if (items.length > 0) {
            sections.push({ type: 'meta', items })
            // Remaining lines after meta
            const rest = metaBlock.replace(/\*\*[^*]+\*\*\s*:\s*.+/g, '').trim()
            if (rest) {
              const headerInRest = rest.match(/^#{2,3}\s+(.+)$/m)
              if (headerInRest) {
                const rt = headerInRest[1].trim()
                const rb = rest.slice(headerInRest[0].length).trim()
                sections.push({ type: 'markdown', title: rt, content: rb || rest })
              } else {
                sections.push({ type: 'markdown', title: '', content: rest })
              }
            }
            continue
          }
        }

        sections.push({ type: 'markdown', title, content: block })
      }

      return sections
    },
  },
  async mounted() {
    await this.loadAlert()
    if (this.alert && this.alert.status === 'processing' && this.alert.trace_id) {
      this.connectSSE()
    }
  },
  beforeDestroy() {
    this.disconnectSSE()
  },
  methods: {
    severityLabel,
    normalizeSeverity,
    async loadAlert() {
      this.loading = true
      try {
        const res = await fetch(`/api/v1/alerts/${this.alertId}`)
        if (res.ok) {
          this.alert = await res.json()
          this.stepActive = this.alert.status === 'completed' ? 4 : (this.alert.status === 'processing' ? 2 : 0)
        }
      } catch (e) {
        this.$message.error('加载失败: ' + e.message)
      }
      this.loading = false
    },

    // ── SSE 替代轮询 ──
    connectSSE() {
      this.disconnectSSE()
      const url = `/api/v1/alerts/${this.alert.trace_id}/events`
      this.eventSource = new EventSource(url)

      // 监听所有命名事件（实时处理日志）
      this.eventSource.addEventListener('alert_received', (e) => this._pushEvent(e))
      this.eventSource.addEventListener('coordinator_completed', (e) => this._pushEvent(e))
      this.eventSource.addEventListener('agent_started', (e) => this._pushEvent(e))
      this.eventSource.addEventListener('agent_completed', (e) => this._pushEvent(e))
      this.eventSource.addEventListener('skill_started', (e) => this._pushEvent(e))
      this.eventSource.addEventListener('skill_completed', (e) => this._pushEvent(e))
      this.eventSource.addEventListener('safety_completed', (e) => this._pushEvent(e))
      this.eventSource.addEventListener('safety_plan_overlay', (e) => this._pushEvent(e))

      // 处理完成 → 断开 SSE，拉取完整数据
      this.eventSource.addEventListener('completed', (e) => {
        this._pushEvent(e)
        this.disconnectSSE()
        this.loadAlert()
      })

      this.eventSource.onerror = () => {
        // SSE 连接异常，降级为轮询
        this.disconnectSSE()
        this.startPolling()
      }
    },
    disconnectSSE() {
      if (this.eventSource) {
        this.eventSource.close()
        this.eventSource = null
      }
    },
    _pushEvent(e) {
      try {
        const data = JSON.parse(e.data)
        this.eventLog.push(data)
        // 最多保留 50 条
        if (this.eventLog.length > 50) this.eventLog.shift()
      } catch (_) {}
    },
    eventSummary(evt) {
      const data = evt.data || {}
      switch (evt.type) {
        case 'alert_received': return '告警已接收，开始分析'
        case 'agent_started': return `Agent ${data.agent || ''} 启动`
        case 'coordinator_completed': return '协调者汇总完成'
        case 'task_claimed': return `任务 ${data.task_id || ''} 被 ${data.agent || ''} 认领`
        case 'artifact_produced': return `${data.agent || ''} 产出分析结果`
        case 'skill_started': return `执行技能: ${data.skill || ''}`
        case 'skill_completed': return `技能 ${data.skill || ''} 执行完毕`
        case 'safety_completed': return `安全审查完成 (${data.risk_level || ''})`
        case 'safety_plan_overlay': return '高风险场景，叠加安全处理计划'
        case 'completed': return '✅ 处置完成'
        default: return evt.type
      }
    },

    // ── 轮询降级 ──
    startPolling() {
      if (this.pollTimer) return
      this.pollTimer = setInterval(async () => {
        const res = await fetch(`/api/v1/alerts/${this.alertId}`)
        if (res.ok) {
          this.alert = await res.json()
          if (this.alert.status !== 'processing') {
            this.stepActive = 4
            this.stopPolling()
          }
        }
      }, 2000)
    },
    stopPolling() {
      if (this.pollTimer) {
        clearInterval(this.pollTimer)
        this.pollTimer = null
      }
    },
    caseSolutionSteps(solution) {
      // 后端 solution 是 "1. xxx\n2. xxx" 格式字符串，拆分为数组并去掉编号
      if (!solution) return []
      return solution
        .split('\n')
        .map(s => s.replace(/^\s*\d+[.、]?\s*/, '').trim())
        .filter(Boolean)
    },
    renderMarkdown(text) {
      if (!text) return ''
      // 简单 markdown 转 HTML（无额外依赖）
      let html = text
        // 代码块
        .replace(/```(\w*)\n([\s\S]*?)```/g, '<pre class="md-code-block"><code>$2</code></pre>')
        // 行内代码
        .replace(/`([^`]+)`/g, '<code class="md-code">$1</code>')
        // 标题 ###
        .replace(/^### (.+)$/gm, '<h4 class="md-h4">$1</h4>')
        // 标题 ##
        .replace(/^## (.+)$/gm, '<h3 class="md-h3">$1</h3>')
        // 标题 #
        .replace(/^# (.+)$/gm, '<h2 class="md-h2">$1</h2>')
        // 无序列表
        .replace(/^- (.+)$/gm, '<li class="md-li">$1</li>')
        .replace(/(<li class="md-li">.*<\/li>\n?)+/g, '<ul class="md-ul">$&</ul>')
        // 有序列表
        .replace(/^\d+\. (.+)$/gm, '<li class="md-li">$1</li>')
        // 加粗
        .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
        // 斜体
        .replace(/\*(.+?)\*/g, '<em>$1</em>')
        // 分割线
        .replace(/^---$/gm, '<hr class="md-hr" />')
        // 引用块 >
        .replace(/^&gt; (.+)$/gm, '<blockquote class="md-blockquote">$1</blockquote>')
        .replace(/^> (.+)$/gm, '<blockquote class="md-blockquote">$1</blockquote>')
        // 段落（连续两个换行）
        .replace(/\n\n/g, '</p><p class="md-p">')
        // 单换行
        .replace(/\n/g, '<br />')

      return '<p class="md-p">' + html + '</p>'
    },
    handleExport() {
      if (!this.alert) return
      const a = this.alert

      const overlayHtml = a.safety_overlays && a.safety_overlays.length > 0
        ? a.safety_overlays.map(o =>
          `<h3 style="color:#d48806;margin:16px 0 8px">${o.skill}</h3>
             <pre style="background:#fffbe6;border:1px solid #ffe58f;border-radius:4px;padding:10px;font-size:12px;line-height:1.6;white-space:pre-wrap">${typeof o.output === 'object' ? JSON.stringify(o.output, null, 2) : o.output
          }</pre>`
        ).join('')
        : ''

      const el = document.createElement('div')
      el.innerHTML = `
        <div id="pdf-report" style="font-family:-apple-system,'Segoe UI',sans-serif;max-width:700px;margin:0 auto;padding:40px;color:#1d2129;line-height:1.8;">
          <h1 style="font-size:22px;border-bottom:2px solid #409eff;padding-bottom:12px;margin:0 0 16px;">告警分析报告</h1>
          <table style="width:100%;border-collapse:collapse;margin:16px 0;font-size:13px;">
            <tr><td style="padding:6px 12px;border:1px solid #e0e0e0;background:#f5f7fa;font-weight:500;width:100px">告警 ID</td><td style="padding:6px 12px;border:1px solid #e0e0e0">${a.id}</td></tr>
            <tr><td style="padding:6px 12px;border:1px solid #e0e0e0;background:#f5f7fa;font-weight:500">严重级别</td><td style="padding:6px 12px;border:1px solid #e0e0e0"><span style="display:inline-block;padding:2px 12px;border-radius:4px;font-weight:600;font-size:12px;background:${a.severity === 'critical' ? '#f56c6c' : a.severity === 'warning' ? '#e6a23c' : '#909399'};color:#fff">${this.severityLabel(a.severity)}</span></td></tr>
            <tr><td style="padding:6px 12px;border:1px solid #e0e0e0;background:#f5f7fa;font-weight:500">来源</td><td style="padding:6px 12px;border:1px solid #e0e0e0">${a.source}</td></tr>
            <tr><td style="padding:6px 12px;border:1px solid #e0e0e0;background:#f5f7fa;font-weight:500">错误类型</td><td style="padding:6px 12px;border:1px solid #e0e0e0">${a.error_type || '-'}</td></tr>
            <tr><td style="padding:6px 12px;border:1px solid #e0e0e0;background:#f5f7fa;font-weight:500">处理耗时</td><td style="padding:6px 12px;border:1px solid #e0e0e0">${a.duration_ms}ms</td></tr>
            <tr><td style="padding:6px 12px;border:1px solid #e0e0e0;background:#f5f7fa;font-weight:500">Trace ID</td><td style="padding:6px 12px;border:1px solid #e0e0e0;font-family:monospace;font-size:12px">${a.trace_id}</td></tr>
          </table>

          <h2 style="font-size:18px;margin:24px 0 12px;">分析结论</h2>
          <div style="font-size:14px;line-height:1.9;">${this.renderMarkdown(a.conclusion)}</div>

          <h2 style="font-size:18px;margin:24px 0 12px;">处置计划</h2>
          <div style="font-size:14px;line-height:1.9;">${this.renderMarkdown(a.disposition_plan)}</div>

          <h2 style="font-size:18px;margin:24px 0 12px;">安全审查</h2>
          <p style="font-size:14px;">状态: <span style="display:inline-block;padding:2px 12px;border-radius:4px;font-weight:600;font-size:12px;background:${a.has_safety_intercept ? '#f56c6c' : '#67c23a'};color:#fff">${a.has_safety_intercept ? '已拦截' : '已通过'}</span></p>
          ${a.safety_reason ? '<pre style="background:#fff5f5;border:1px solid #fde2e2;border-radius:4px;padding:10px;font-size:13px;color:#c0392b;white-space:pre-wrap">' + a.safety_reason + '</pre>' : ''}

          ${overlayHtml ? '<h2 style="font-size:18px;margin:24px 0 12px;">安全技能叠加</h2>' + overlayHtml : ''}

          <div style="margin-top:40px;padding-top:16px;border-top:1px solid #e0e0e0;font-size:12px;color:#909399;">
            <p>生成时间: ${new Date().toLocaleString()}</p>
            <p>由 AIOps 智能运维系统自动生成</p>
          </div>
        </div>
      `
      el.style.position = 'fixed'
      el.style.left = '-9999px'
      el.style.top = '0'
      document.body.appendChild(el)

      const opt = {
        margin: [10, 10],
        filename: `alert-report-${a.id}.pdf`,
        image: { type: 'jpeg', quality: 0.98 },
        html2canvas: { scale: 2, useCORS: true },
        jsPDF: { unit: 'mm', format: 'a4', orientation: 'portrait' },
        pagebreak: { mode: ['avoid-all', 'css', 'legacy'] },
      }
      html2pdf().set(opt).from(el.querySelector('#pdf-report')).save().then(() => {
        document.body.removeChild(el)
        this.$message.success('PDF 报告已导出')
      }).catch(() => {
        document.body.removeChild(el)
        this.$message.error('导出失败')
      })
    },
    handleApply() {
      this.$message.success('建议已采纳，工单已自动创建')
    },
    handleReject() {
      this.$message.info('已标记为拒绝')
    },
  },
}
</script>

<style scoped>
.alert-detail {
  max-width: 920px;
  margin: 0 auto;
}

/* ── 顶部导航 ── */
.detail-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
  padding-bottom: 14px;
  border-bottom: 1px solid #e8eaed;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

.back-btn {
  font-size: 13px;
  color: #606266;
  padding: 0;
}

.back-btn:hover {
  color: #409eff;
}

.header-title {
  font-size: 18px;
  font-weight: 600;
  color: #1d2129;
}

/* ── 步骤条 ── */
.status-steps {
  margin-bottom: 24px;
}

.status-alert {
  margin-bottom: 16px;
}

/* ── 顶部指标卡 ── */
.metric-row {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
  margin-bottom: 20px;
}

.metric-card {
  background: #fff;
  border: 1px solid #e8eaed;
  border-radius: 8px;
  padding: 16px;
  display: flex;
  align-items: center;
  gap: 14px;
  transition: box-shadow 0.2s;
}

.metric-card:hover {
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.06);
}

.metric-icon {
  width: 40px;
  height: 40px;
  border-radius: 10px;
  background: #fef0f0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
  color: #f56c6c;
  flex-shrink: 0;
}

.metric-icon.icon-blue {
  background: #ecf5ff;
  color: #409eff;
}

.metric-icon.icon-green {
  background: #f0f9eb;
  color: #67c23a;
}

.metric-icon.icon-orange {
  background: #fdf6ec;
  color: #e6a23c;
}

.metric-body {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.metric-label {
  font-size: 12px;
  color: #909399;
}

.metric-value {
  font-size: 16px;
  font-weight: 600;
  color: #1d2129;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.metric-value small {
  font-size: 12px;
  font-weight: 400;
  color: #909399;
}

/* 严重级别卡颜色 */
.metric-severity.severity-critical {
  border-left: 3px solid #f56c6c;
}

.metric-severity.severity-warning {
  border-left: 3px solid #e6a23c;
}

.metric-severity.severity-info {
  border-left: 3px solid #909399;
}

/* ── 区块卡片 ── */
.section-card {
  margin-bottom: 16px;
  border-radius: 8px;
  border: 1px solid #e8eaed;
}

.section-card>>>.el-card__header {
  padding: 14px 20px;
  border-bottom: 1px solid #ebeef5;
  background: #fafbfc;
}

.section-card>>>.el-card__body {
  padding: 20px;
}

.section-header {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 15px;
  font-weight: 600;
  color: #1d2129;
}

.section-header i {
  font-size: 18px;
  color: #409eff;
}

/* ── 时间线 ── */
.tl-title {
  font-size: 14px;
  font-weight: 500;
  color: #1d2129;
}

.tl-desc {
  font-size: 12px;
  color: #909399;
  margin-top: 2px;
}

/* ── 结论区 ── */
.conclusion-body {
  padding: 0;
}

.section-content {
  padding: 4px 0;
}

.section-content:not(:last-child) {
  border-bottom: 1px solid #f0f0f0;
  margin-bottom: 12px;
  padding-bottom: 12px;
}

/* ── 元信息行 ── */
.meta-block {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  padding: 0 0 14px;
  border-bottom: 1px solid #f0f0f0;
  margin-bottom: 14px;
}

.meta-tag-row {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
}

.meta-tag-label {
  color: #909399;
  font-size: 12px;
}

.meta-tag-value {
  color: #1d2129;
  font-weight: 500;
}

/* ── 故障案例卡片 ── */
.cases-section {
  margin: 4px 0;
}

.cases-header {
  font-size: 15px;
  font-weight: 600;
  color: #1d2129;
  margin-bottom: 12px;
  padding: 0 2px;
}

.case-card {
  background: #f8faff;
  border: 1px solid #e8edf5;
  border-radius: 8px;
  margin-bottom: 12px;
  overflow: hidden;
  transition: box-shadow 0.2s;
}

.case-card:hover {
  box-shadow: 0 2px 8px rgba(64, 158, 255, 0.08);
}

.case-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 14px;
  font-weight: 600;
  color: #2c3e50;
  padding: 10px 14px;
  background: #f0f5ff;
  border-bottom: 1px solid #e8edf5;
}

.case-title i {
  color: #409eff;
  font-size: 15px;
}

.case-body {
  padding: 12px 14px;
}

.case-row {
  display: flex;
  gap: 6px;
  font-size: 13px;
  line-height: 1.7;
  margin-bottom: 6px;
  color: #606266;
}

.case-row:last-child {
  margin-bottom: 0;
}

.case-label {
  font-weight: 500;
  color: #909399;
  white-space: nowrap;
}

.case-solution {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.solution-step {
  display: flex;
  gap: 4px;
  font-size: 13px;
  color: #606266;
}

.step-num {
  color: #409eff;
  font-weight: 500;
}

/* ── Markdown ── */
.markdown-body {
  font-size: 14px;
  line-height: 1.9;
  color: #1d2129;
}

.markdown-body>>>.md-p {
  margin: 0 0 12px;
}

.markdown-body>>>.md-h2 {
  font-size: 17px;
  font-weight: 600;
  margin: 20px 0 10px;
  padding-bottom: 8px;
  border-bottom: 1px solid #ebeef5;
  color: #1d2129;
}

.markdown-body>>>.md-h3 {
  font-size: 15px;
  font-weight: 600;
  margin: 16px 0 8px;
  color: #2c3e50;
}

.markdown-body>>>.md-h4 {
  font-size: 14px;
  font-weight: 600;
  margin: 12px 0 6px;
  color: #2c3e50;
}

.markdown-body>>>.md-ul {
  padding-left: 20px;
  margin: 8px 0;
}

.markdown-body>>>.md-li {
  margin-bottom: 4px;
  line-height: 1.8;
}

.markdown-body>>>.md-code {
  background: #f5f7fa;
  border: 1px solid #e4e7ed;
  border-radius: 3px;
  padding: 1px 6px;
  font-size: 12px;
  color: #d5615e;
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
}

.markdown-body>>>.md-code-block {
  background: #1e1e1e;
  color: #d4d4d4;
  border-radius: 6px;
  padding: 14px 16px;
  overflow-x: auto;
  font-size: 13px;
  line-height: 1.6;
  margin: 12px 0;
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
}

.markdown-body>>>.md-hr {
  border: none;
  border-top: 1px solid #ebeef5;
  margin: 20px 0;
}

.markdown-body>>>.md-blockquote {
  border-left: 4px solid #e6a23c;
  background: #fffbe6;
  padding: 10px 14px;
  margin: 12px 0;
  border-radius: 4px;
  font-size: 13px;
  color: #856404;
  line-height: 1.7;
}

/* section-content 内也应用相同 markdown 样式 */
.section-content>>>.md-p {
  margin: 0 0 12px;
}

.section-content>>>.md-h2 {
  font-size: 17px;
  font-weight: 600;
  margin: 20px 0 10px;
  padding-bottom: 8px;
  border-bottom: 1px solid #ebeef5;
  color: #1d2129;
}

.section-content>>>.md-h3 {
  font-size: 15px;
  font-weight: 600;
  margin: 16px 0 8px;
  color: #2c3e50;
}

.section-content>>>.md-h4 {
  font-size: 14px;
  font-weight: 600;
  margin: 12px 0 6px;
  color: #2c3e50;
}

.section-content>>>.md-ul {
  padding-left: 20px;
  margin: 8px 0;
}

.section-content>>>.md-li {
  margin-bottom: 4px;
  line-height: 1.8;
}

.section-content>>>.md-code {
  background: #f5f7fa;
  border: 1px solid #e4e7ed;
  border-radius: 3px;
  padding: 1px 6px;
  font-size: 12px;
  color: #d5615e;
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
}

.section-content>>>.md-code-block {
  background: #1e1e1e;
  color: #d4d4d4;
  border-radius: 6px;
  padding: 14px 16px;
  overflow-x: auto;
  font-size: 13px;
  line-height: 1.6;
  margin: 12px 0;
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
}

.section-content>>>.md-blockquote {
  border-left: 4px solid #e6a23c;
  background: #fffbe6;
  padding: 10px 14px;
  margin: 12px 0;
  border-radius: 4px;
  font-size: 13px;
  color: #856404;
  line-height: 1.7;
}

/* ── 安全审查 ── */
.safety-status {
  display: flex;
  align-items: flex-start;
  gap: 16px;
  padding: 8px 0;
}

.safety-text {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.safety-title {
  font-size: 15px;
  font-weight: 500;
  color: #1d2129;
}

.safety-desc {
  font-size: 13px;
  color: #909399;
  line-height: 1.6;
}

/* ── 安全技能叠加 ── */
.overlay-item {
  margin-top: 12px;
  padding: 12px 16px;
  background: #fffbe6;
  border: 1px solid #ffe58f;
  border-radius: 6px;
}

.overlay-header {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
  font-size: 14px;
  color: #d48806;
}

.overlay-output {
  margin-top: 8px;
}

.overlay-output pre {
  background: rgba(0, 0, 0, 0.03);
  border-radius: 4px;
  padding: 10px;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
  color: #666;
  margin: 0;
}

/* ── 元信息 ── */
.meta-card>>>.el-descriptions__body {
  font-size: 13px;
}

.code-text {
  font-family: 'SFMono-Regular', Consolas, monospace;
  font-size: 12px;
  word-break: break-all;
}

/* ── 原始数据 ── */
.raw-data-section {
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px solid #ebeef5;
}
.raw-toggle {
  font-size: 13px;
  color: #606266;
}
.raw-toggle:hover {
  color: #409eff;
}
.raw-json {
  background: #1e1e1e;
  color: #d4d4d4;
  border-radius: 6px;
  padding: 14px 16px;
  font-size: 12px;
  line-height: 1.6;
  overflow-x: auto;
  margin: 10px 0 0;
  font-family: 'SFMono-Regular', Consolas, monospace;
  max-height: 400px;
  overflow-y: auto;
}

/* ── 操作栏 ── */
.action-bar {
  margin-top: 24px;
  text-align: center;
  padding: 8px 0 32px;
}

/* ── 实时处理日志 ── */
.event-log {
  max-height: 300px;
  overflow-y: auto;
  font-size: 13px;
  line-height: 1.6;
}
.event-item {
  display: flex;
  gap: 10px;
  align-items: baseline;
  padding: 4px 0;
  border-bottom: 1px solid #f5f5f5;
}
.event-item:last-child {
  border-bottom: none;
}
.event-time {
  font-family: monospace;
  font-size: 11px;
  color: #bbb;
  flex-shrink: 0;
  width: 60px;
}
.event-type {
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 3px;
  background: #f0f0f0;
  color: #666;
  flex-shrink: 0;
  min-width: 60px;
  text-align: center;
}
.event-type.evt-completed { background: #f0f9eb; color: #67c23a; }
.event-type.evt-agent_started { background: #ecf5ff; color: #409eff; }
.event-type.evt-skill_started { background: #fdf6ec; color: #e6a23c; }
.event-type.evt-safety_completed { background: #fef0f0; color: #f56c6c; }
.event-summary {
  color: #606266;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
