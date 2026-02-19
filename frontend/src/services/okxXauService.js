import api from './api';

const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';

export const okxXauService = {
  start: () => api.post('/api/okx-xau/start'),
  stop: () => api.post('/api/okx-xau/stop'),
  status: () => api.get('/api/okx-xau/status'),
  history: (limit = 100) => api.get('/api/okx-xau/history', { params: { limit } }),
  clear: () => api.post('/api/okx-xau/clear'),
  dbStats: () => api.get('/api/okx-xau/db-stats'),
  dbHistory: (params) => api.get('/api/okx-xau/db-history', { params }),
  arbBacktest: (params) => api.post('/api/okx-xau/arb-backtest', params),

  /** Returns an EventSource for SSE streaming */
  createStream: () => new EventSource(`${API_BASE}/api/okx-xau/stream`),
};
