/**
 * 数据中心 API service
 */
import api from './api';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:7152';

export const dataCenterService = {
  /** 获取数据统计 */
  async getStats() {
    const resp = await api.get('/api/data-center/stats');
    return resp.data;
  },

  /** 获取缓存价格数据 */
  async getPrices(source, underlying, startDate, endDate, limit = 500) {
    const params = { source, underlying, limit };
    if (startDate) params.start_date = startDate;
    if (endDate) params.end_date = endDate;
    const resp = await api.get('/api/data-center/prices', { params });
    return resp.data;
  },

  /** 获取缓存IV数据 */
  async getIVData(underlying, expiryDate, targetDate, limit = 200) {
    const params = { underlying, limit };
    if (expiryDate) params.expiry_date = expiryDate;
    if (targetDate) params.target_date = targetDate;
    const resp = await api.get('/api/data-center/iv-data', { params });
    return resp.data;
  },

  /** 收取数据（SSE流式） */
  collectStream(params, onProgress, onResult, onError) {
    const controller = new AbortController();
    fetch(`${API_BASE_URL}/api/data-center/collect-stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params),
      signal: controller.signal,
    }).then(async (response) => {
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';
        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const msg = JSON.parse(line.slice(6));
              if (msg.type === 'progress') onProgress(msg);
              else if (msg.type === 'result') onResult(msg.data);
              else if (msg.type === 'error') onError(msg.message);
            } catch (e) { /* ignore */ }
          }
        }
      }
    }).catch((err) => {
      if (err.name !== 'AbortError') onError(err.message);
    });
    return controller;
  },

  /** 清除缓存 */
  async clearCache(source, underlying) {
    const params = {};
    if (source) params.source = source;
    if (underlying) params.underlying = underlying;
    const resp = await api.delete('/api/data-center/cache', { params });
    return resp.data;
  },

  /** 清除无数据标记 */
  async clearSentinels(underlying) {
    const params = {};
    if (underlying) params.underlying = underlying;
    const resp = await api.delete('/api/data-center/sentinels', { params });
    return resp.data;
  },

  /** 获取可编辑的IV数据 */
  async getIVDataEditable(underlying, expiryDate, targetDate, optionType, minStrike, maxStrike, startDate, endDate, limit = 500) {
    const params = { underlying, limit };
    if (expiryDate) params.expiry_date = expiryDate;
    if (targetDate) params.target_date = targetDate;
    if (optionType) params.option_type = optionType;
    if (minStrike != null) params.min_strike = minStrike;
    if (maxStrike != null) params.max_strike = maxStrike;
    if (startDate) params.start_date = startDate;
    if (endDate) params.end_date = endDate;
    const resp = await api.get('/api/data-center/iv-data-editable', { params });
    return resp.data;
  },

  /** 获取可编辑的价格数据 */
  async getPricesEditable(source, underlying, startDate, endDate, limit = 1000) {
    const params = { source, underlying, limit };
    if (startDate) params.start_date = startDate;
    if (endDate) params.end_date = endDate;
    const resp = await api.get('/api/data-center/prices-editable', { params });
    return resp.data;
  },

  /** 修改IV记录 */
  async updateIVRecord(recordId, data) {
    const resp = await api.put(`/api/data-center/iv-record/${recordId}`, data);
    return resp.data;
  },

  /** 删除IV记录 */
  async deleteIVRecord(recordId) {
    const resp = await api.delete(`/api/data-center/iv-record/${recordId}`);
    return resp.data;
  },

  /** 修改Deribit价格记录 */
  async updateDeribitPrice(recordId, data) {
    const resp = await api.put(`/api/data-center/price-record/deribit/${recordId}`, data);
    return resp.data;
  },

  /** 删除Deribit价格记录 */
  async deleteDeribitPrice(recordId) {
    const resp = await api.delete(`/api/data-center/price-record/deribit/${recordId}`);
    return resp.data;
  },

  /** 修改OKX价格记录 */
  async updateOkxPrice(recordId, data) {
    const resp = await api.put(`/api/data-center/price-record/okx/${recordId}`, data);
    return resp.data;
  },

  /** 删除OKX价格记录 */
  async deleteOkxPrice(recordId) {
    const resp = await api.delete(`/api/data-center/price-record/okx/${recordId}`);
    return resp.data;
  },

  /** 批量删除IV记录 */
  async batchDeleteIV(params) {
    const resp = await api.post('/api/data-center/iv-batch-delete', params);
    return resp.data;
  },

  /** 获取代理设置 */
  async getProxy() {
    const resp = await api.get('/api/data-center/proxy');
    return resp.data;
  },

  /** 更新代理设置 */
  async updateProxy(enabled, url) {
    const resp = await api.put('/api/data-center/proxy', { enabled, url });
    return resp.data;
  },

  /** 测试代理连接 */
  async testProxy() {
    const resp = await api.get('/api/data-center/proxy/test');
    return resp.data;
  },

  /** 获取数据可得性矩阵 */
  async getDataAvailability(underlying, targetDate, optionType) {
    const params = { underlying };
    if (targetDate) params.target_date = targetDate;
    if (optionType) params.option_type = optionType;
    const resp = await api.get('/api/data-center/data-availability', { params });
    return resp.data;
  },

  /** 获取有IV数据的target_date列表 */
  async getAvailabilityDates(underlying) {
    const resp = await api.get('/api/data-center/data-availability/dates', { params: { underlying } });
    return resp.data;
  },

  // ── 高频数据收集 ──

  /** 获取收集器状态 */
  async getHFStatus() {
    const resp = await api.get('/api/hf-collector/status');
    return resp.data;
  },

  /** 启动收集器 */
  async startHFCollector(underlying, intervalSec) {
    const resp = await api.post('/api/hf-collector/start', { underlying, interval_sec: intervalSec });
    return resp.data;
  },

  /** 停止收集器 */
  async stopHFCollector() {
    const resp = await api.post('/api/hf-collector/stop');
    return resp.data;
  },

  /** 手动快照 */
  async manualSnapshot(underlying) {
    const resp = await api.post('/api/hf-collector/snapshot', { underlying });
    return resp.data;
  },

  /** 获取高频数据可用日期 */
  async getHFDates(underlying) {
    const resp = await api.get('/api/hf-collector/available-dates', { params: { underlying } });
    return resp.data;
  },

  /** 获取某天可用的快照时间 */
  async getHFTimes(underlying, dateStr) {
    const params = { underlying };
    if (dateStr) params.date_str = dateStr;
    const resp = await api.get('/api/hf-collector/available-times', { params });
    return resp.data;
  },

  /** 获取某个快照的数据矩阵 */
  async getHFSnapshotData(underlying, snapshotTime, optionType) {
    const params = { underlying };
    if (snapshotTime) params.snapshot_time = snapshotTime;
    if (optionType) params.option_type = optionType;
    const resp = await api.get('/api/hf-collector/snapshot-data', { params });
    return resp.data;
  },

  /** 获取高频数据统计 */
  async getHFStats(underlying) {
    const resp = await api.get('/api/hf-collector/stats', { params: { underlying } });
    return resp.data;
  },

  /** 清除高频数据 */
  async clearHFData(underlying, beforeDate) {
    const params = {};
    if (underlying) params.underlying = underlying;
    if (beforeDate) params.before_date = beforeDate;
    const resp = await api.delete('/api/hf-collector/data', { params });
    return resp.data;
  },

  /** 获取sentinel详情 */
  async getSentinelDetails(underlying) {
    const params = {};
    if (underlying) params.underlying = underlying;
    const resp = await api.get('/api/data-center/sentinels/detail', { params });
    return resp.data;
  },

  /** 重试sentinel（SSE流式） */
  retrySentinelsStream(sentinelIds, onProgress, onResult, onError) {
    const controller = new AbortController();
    fetch(`${API_BASE_URL}/api/data-center/sentinels/retry-stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sentinel_ids: sentinelIds }),
      signal: controller.signal,
    }).then(async (response) => {
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';
        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const msg = JSON.parse(line.slice(6));
              if (msg.type === 'progress') onProgress(msg);
              else if (msg.type === 'result') onResult(msg.data);
              else if (msg.type === 'error') onError(msg.message);
            } catch (e) { /* ignore */ }
          }
        }
      }
    }).catch((err) => {
      if (err.name !== 'AbortError') onError(err.message);
    });
    return controller;
  },
};
