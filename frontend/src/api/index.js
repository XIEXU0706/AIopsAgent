import axios from 'axios'

const api = axios.create({
  baseURL: '/api/v1',
  timeout: 30000,
})

export function fetchAlerts(params) {
  return api.get('/alerts', { params })
}

export function createAlert(data) {
  return api.post('/alerts', data)
}

export function fetchAlertEvents(traceId) {
  return api.get(`/alerts/${traceId}/events`)
}

export function fetchSessions() {
  return api.get('/sessions')
}

export function createSession() {
  return api.post('/sessions')
}

export default api
