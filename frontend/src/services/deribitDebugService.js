import api from './api';

export const deribitDebugService = {
  listInstruments: async (underlying = 'BTC', expired = true) => {
    const resp = await api.get('/api/deribit-debug/instruments', {
      params: { underlying, expired },
    });
    return resp.data;
  },

  testTrades: async (params) => {
    const resp = await api.post('/api/deribit-debug/test-trades', params);
    return resp.data;
  },

  testSmile: async (params) => {
    const resp = await api.post('/api/deribit-debug/test-smile', params);
    return resp.data;
  },

  saveSmile: async (params) => {
    const resp = await api.post('/api/deribit-debug/save-smile', params);
    return resp.data;
  },

  getIVCacheData: async (underlying = 'BTC', limit = 200) => {
    const resp = await api.get('/api/deribit-debug/cache/iv-data', {
      params: { underlying, limit },
    });
    return resp.data;
  },

  batchSmile: async (params) => {
    const resp = await api.post('/api/deribit-debug/batch-smile', params);
    return resp.data;
  },

  getATMIVHistory: async (params) => {
    const resp = await api.post('/api/deribit-debug/atm-iv-history', params);
    return resp.data;
  },

  getCacheStats: async () => {
    const resp = await api.get('/api/deribit/cache/stats');
    return resp.data;
  },

  clearIVCache: async () => {
    const resp = await api.delete('/api/deribit/cache/iv');
    return resp.data;
  },

  clearAllCache: async () => {
    const resp = await api.delete('/api/deribit/cache');
    return resp.data;
  },
};
