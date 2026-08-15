// 告警级别：统一为 critical / warning / info 三值，展示为 英文(中文)
export const SEVERITY_LABELS = {
  critical: 'Critical(严重)',
  warning: 'Warning(警告)',
  info: 'Info(提示)',
}

const SEVERITY_MAP = {
  critical: 'critical', fatal: 'critical', emergency: 'critical',
  severe: 'critical', error: 'critical', alert: 'critical', high: 'critical',
  warning: 'warning', warn: 'warning', medium: 'warning', moderate: 'warning',
  info: 'info', information: 'info', notice: 'info',
  low: 'info', ok: 'info', okay: 'info', normal: 'info', debug: 'info',
}

// 把任意来源的级别值归一为 critical/warning/info
export function normalizeSeverity(value) {
  if (!value) return 'warning'
  const key = String(value).trim().toLowerCase()
  if (SEVERITY_MAP[key]) return SEVERITY_MAP[key]
  if (/(critical|fatal|emergency|severe|error)/.test(key)) return 'critical'
  if (/(warn|medium|moderate)/.test(key)) return 'warning'
  if (/(info|notice|low|ok|normal|debug)/.test(key)) return 'info'
  return 'warning'
}

// 展示文本：Critical(严重) / Warning(警告) / Info(提示)
export function severityLabel(value) {
  return SEVERITY_LABELS[normalizeSeverity(value)]
}
