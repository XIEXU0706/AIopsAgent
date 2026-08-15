<template>
  <div class="dashboard">
    <!-- 顶部：标题 + 刷新 -->
    <div class="page-header">
      <div>
        <h2>数据中心</h2>
        <p class="page-desc">AIOps 智能告警处理概览与实时监控</p>
      </div>
      <el-button type="primary" icon="el-icon-refresh" :loading="loading" @click="loadData" size="small">
        刷新数据
      </el-button>
    </div>

    <!-- 统计卡片 -->
    <el-row :gutter="20" class="stat-row">
      <el-col :span="6" v-for="card in statCards" :key="card.label">
        <el-card shadow="hover" class="stat-card" :body-style="{ padding: '20px' }">
          <div class="stat-inner">
            <div :class="['stat-icon', card.color]">
              <i :class="card.icon"></i>
            </div>
            <div class="stat-info">
              <p class="stat-value">{{ card.value }}</p>
              <p class="stat-label">{{ card.label }}</p>
            </div>
          </div>
          <div class="stat-footer">
            <span :style="{ color: card.trend > 0 ? '#52c41a' : '#f56c6c' }">
              {{ card.trend > 0 ? '↑' : '↓' }} {{ Math.abs(card.trend) }}%
            </span>
            <span style="color: #999; font-size: 12px;">较昨日</span>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 中间行：风险分布 + 工具状态 -->
    <el-row :gutter="20" class="chart-row">
      <el-col :span="12">
        <el-card shadow="hover">
          <div slot="header"><strong>风险等级分布</strong></div>
          <div class="chart-content">
            <div class="risk-item" v-for="item in riskDistribution" :key="item.label">
              <span class="risk-label">
                <el-tag :type="item.type" size="mini" effect="dark" style="min-width: 108px; text-align: center;">
                  {{ item.label }}
                </el-tag>
              </span>
              <el-progress
                :percentage="item.percent"
                :color="item.color"
                :stroke-width="16"
                :text-inside="true"
              >
                {{ item.count }}条
              </el-progress>
            </div>
          </div>
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card shadow="hover">
          <div slot="header"><strong>告警趋势</strong></div>
          <div ref="trendChart" class="echart-container"></div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 最近活动 -->
    <el-card shadow="hover" class="activity-card">
      <div slot="header">
        <strong>最近活动</strong>
      </div>
      <template v-if="activities.length > 0">
        <el-timeline>
          <el-timeline-item
            v-for="act in (showAllActivities ? activities : activities.slice(0, 3))"
            :key="act.id"
            :timestamp="act.time"
            :color="act.color"
          >
            <span :style="{ fontWeight: 600 }">{{ act.title }}</span>
            <p style="margin: 4px 0 0; color: #666; font-size: 13px;">{{ act.detail }}</p>
          </el-timeline-item>
        </el-timeline>
        <div v-if="activities.length > 3" class="activity-footer">
          <el-button type="text" @click="showAllActivities = !showAllActivities">
            {{ showAllActivities ? '收起' : `查看全部（共 ${activities.length} 条）` }}
            <i :class="showAllActivities ? 'el-icon-arrow-up' : 'el-icon-arrow-down'"></i>
          </el-button>
        </div>
      </template>
      <el-empty v-else description="暂无活动记录" />
    </el-card>
  </div>
</template>

<script>
import * as echarts from 'echarts'
import { severityLabel } from '../utils/severity'

export default {
  data() {
    return {
      loading: false,
      alerts: [],
      chart: null,
      statCards: [
        { label: '今日告警', value: '--', icon: 'el-icon-bell', color: 'blue', trend: 0 },
        { label: '严重告警', value: '--', icon: 'el-icon-warning-outline', color: 'red', trend: 0 },
        { label: 'Agent 健康率', value: '--', icon: 'el-icon-check', color: 'green', trend: 0 },
        { label: '平均处置耗时', value: '--', icon: 'el-icon-time', color: 'orange', trend: 0 },
      ],
      riskDistribution: [],
      activities: [],
      showAllActivities: false,
    }
  },
  mounted() {
    this.loadData()
    window.addEventListener('resize', this.handleResize)
  },
  beforeDestroy() {
    window.removeEventListener('resize', this.handleResize)
    if (this.chart) this.chart.dispose()
  },
  methods: {
    async loadData() {
      this.loading = true
      try {
        const res = await fetch('/api/v1/alerts')
        if (res.ok) {
          this.alerts = await res.json()
          this.computeStats()
          this.computeRiskDistribution()
          this.computeActivities()
          this.$nextTick(() => this.renderTrendChart())
        }
      } catch (e) {
        this.alerts = []
      }
      this.loading = false
    },
    computeStats() {
      const today = new Date()
      const todayStr = today.toISOString().slice(0, 10)
      const yesterdayStr = new Date(today.getTime() - 86400000).toISOString().slice(0, 10)

      const todayAlerts = this.alerts.filter(a => a.create_time && a.create_time.slice(0, 10) === todayStr)
      const yesterdayAlerts = this.alerts.filter(a => a.create_time && a.create_time.slice(0, 10) === yesterdayStr)

      const tTotal = todayAlerts.length
      const tCritical = todayAlerts.filter(a => a.severity === 'critical').length
      const tCompleted = todayAlerts.filter(a => a.status === 'completed')
      const tAvgDur = tCompleted.length > 0
        ? Math.round(tCompleted.reduce((s, a) => s + (a.duration_ms || 0), 0) / tCompleted.length)
        : 0

      const yTotal = yesterdayAlerts.length
      const yCritical = yesterdayAlerts.filter(a => a.severity === 'critical').length
      const yCompleted = yesterdayAlerts.filter(a => a.status === 'completed')
      const yAvgDur = yCompleted.length > 0
        ? Math.round(yCompleted.reduce((s, a) => s + (a.duration_ms || 0), 0) / yCompleted.length)
        : 0

      const calcTrend = (curr, prev) => prev > 0 ? Math.round((curr - prev) / prev * 100) : (curr > 0 ? 100 : 0)

      this.statCards = [
        { label: '今日告警', value: tTotal, icon: 'el-icon-bell', color: 'blue', trend: calcTrend(tTotal, yTotal) },
        { label: '严重告警', value: tCritical, icon: 'el-icon-warning-outline', color: 'red', trend: calcTrend(tCritical, yCritical) },
        { label: 'Agent 健康率', value: tTotal > 0 ? `${Math.round(tCompleted.length / tTotal * 100)}%` : '--', icon: 'el-icon-check', color: 'green', trend: calcTrend(tCompleted.length / (tTotal || 1) * 100, yCompleted.length / (yTotal || 1) * 100) },
        { label: '平均处置耗时', value: tAvgDur > 0 ? `${tAvgDur}ms` : '--', icon: 'el-icon-time', color: 'orange', trend: calcTrend(tAvgDur, yAvgDur) },
      ]
    },
    computeRiskDistribution() {
      const alerts = this.alerts
      const total = alerts.length || 1
      const critical = alerts.filter(a => a.severity === 'critical').length
      const warning = alerts.filter(a => a.severity === 'warning').length
      const info = alerts.filter(a => a.severity === 'info' || (!a.severity)).length

      this.riskDistribution = [
        { label: severityLabel('critical'), type: 'danger', count: critical, percent: Math.round(critical / total * 100), color: '#f56c6c' },
        { label: severityLabel('warning'), type: 'warning', count: warning, percent: Math.round(warning / total * 100), color: '#e6a23c' },
        { label: severityLabel('info'), type: 'info', count: info, percent: Math.round(info / total * 100), color: '#909399' },
      ]
    },
    computeActivities() {
      this.activities = this.alerts.slice(0, 10).map(a => ({
        id: a.id,
        title: a.title || '未知告警',
        detail: `${a.status === 'completed' ? '分析完成' : a.status === 'processing' ? '分析中' : '失败'} | ${a.error_type || '未知类型'}${a.duration_ms ? ` | ${a.duration_ms}ms` : ''}`,
        time: a.create_time ? new Date(a.create_time).toLocaleString('zh-CN') : '',
        color: a.severity === 'critical' ? '#f56c6c' : a.severity === 'warning' ? '#e6a23c' : '#67c23a',
      }))
    },

    // ── 趋势图 ──
    computeTrendData() {
      const dayMap = {}
      this.alerts.forEach(a => {
        if (!a.create_time) return
        const day = a.create_time.slice(0, 10)
        if (!dayMap[day]) dayMap[day] = { total: 0, critical: 0 }
        dayMap[day].total++
        if (a.severity === 'critical') dayMap[day].critical++
      })
      const sorted = Object.keys(dayMap).sort()
      return {
        dates: sorted,
        total: sorted.map(d => dayMap[d].total),
        critical: sorted.map(d => dayMap[d].critical),
      }
    },
    renderTrendChart() {
      if (!this.$refs.trendChart) return
      if (this.chart) this.chart.dispose()

      this.chart = echarts.init(this.$refs.trendChart)
      const data = this.computeTrendData()

      this.chart.setOption({
        tooltip: {
          trigger: 'axis',
          backgroundColor: 'rgba(255,255,255,0.95)',
          borderColor: '#e8eaed',
          borderWidth: 1,
          textStyle: { color: '#1d2129', fontSize: 12 },
        },
        legend: {
          data: ['总告警', '严重告警'],
          bottom: 0,
          icon: 'circle',
          itemWidth: 8,
          itemHeight: 8,
          textStyle: { fontSize: 12 },
        },
        grid: { left: 40, right: 16, top: 10, bottom: 36 },
        xAxis: {
          type: 'category',
          data: data.dates,
          axisLine: { lineStyle: { color: '#e8eaed' } },
          axisLabel: { fontSize: 11, color: '#909399' },
          axisTick: { alignWithLabel: true },
        },
        yAxis: {
          type: 'value',
          minInterval: 1,
          splitLine: { lineStyle: { color: '#f0f0f0', type: 'dashed' } },
          axisLabel: { fontSize: 11, color: '#909399' },
        },
        series: [
          {
            name: '总告警',
            type: 'line',
            smooth: true,
            symbol: 'circle',
            symbolSize: 6,
            lineStyle: { width: 2, color: '#409eff' },
            itemStyle: { color: '#409eff' },
            areaStyle: {
              color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                { offset: 0, color: 'rgba(64,158,255,0.25)' },
                { offset: 1, color: 'rgba(64,158,255,0.02)' },
              ]),
            },
            data: data.total,
          },
          {
            name: '严重告警',
            type: 'line',
            smooth: true,
            symbol: 'diamond',
            symbolSize: 6,
            lineStyle: { width: 2, color: '#f56c6c' },
            itemStyle: { color: '#f56c6c' },
            areaStyle: {
              color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                { offset: 0, color: 'rgba(245,108,108,0.2)' },
                { offset: 1, color: 'rgba(245,108,108,0.02)' },
              ]),
            },
            data: data.critical,
          },
        ],
      })
    },
    handleResize() {
      if (this.chart) this.chart.resize()
    },
  },
}
</script>

<style scoped>
.dashboard { max-width: 1400px; margin: 0 auto; }

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 20px;
}
.page-header h2 { margin: 0; font-size: 22px; }
.page-desc { margin: 4px 0 0; color: #999; font-size: 13px; }

/* 统计卡片 */
.stat-row { margin-bottom: 20px; }
.stat-card { cursor: default; }
.stat-inner { display: flex; align-items: center; gap: 16px; }
.stat-icon {
  width: 48px; height: 48px; border-radius: 12px;
  display: flex; align-items: center; justify-content: center;
  font-size: 24px;
}
.stat-icon.blue { background: #e6f7ff; color: #1890ff; }
.stat-icon.red { background: #fff1f0; color: #f5222d; }
.stat-icon.green { background: #f6ffed; color: #52c41a; }
.stat-icon.orange { background: #fff7e6; color: #fa8c16; }
.stat-value { font-size: 28px; font-weight: 700; margin: 0; line-height: 1.2; }
.stat-label { font-size: 13px; color: #999; margin: 2px 0 0; }
.stat-footer {
  margin-top: 12px; padding-top: 12px; border-top: 1px solid #f0f0f0;
  display: flex; gap: 8px; align-items: center; font-size: 13px;
}

/* 图表行 */
.chart-row { margin-bottom: 20px; }
.risk-item { margin-bottom: 16px; }
.risk-item:last-child { margin-bottom: 0; }
.risk-label { display: inline-block; margin-bottom: 6px; }

.echart-container {
  width: 100%;
  height: 260px;
}
.chart-content {
  height: 260px;
  display: flex;
  flex-direction: column;
  justify-content: center;
}

/* 活动 */
.activity-card { margin-bottom: 20px; }
.activity-footer {
  text-align: center;
  padding: 8px 0 0;
  border-top: 1px solid #f0f0f0;
  margin-top: 8px;
}
</style>
