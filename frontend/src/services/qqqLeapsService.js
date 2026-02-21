/**
 * QQQ LEAPS 策略 API service
 */
import api from './api';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:7152';

export const qqqLeapsService = {
  backtestStream(params, onProgress, onResult, onError) {
    const controller = new AbortController();
    fetch(`${API_BASE_URL}/api/qqq-leaps/backtest-stream`, {
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

  async etfCompare(tickers = 'QQQ,SPY,XLK,IWM,DIA', years = 5) {
    const resp = await api.get('/api/qqq-leaps/etf-compare', {
      params: { tickers, years },
    });
    return resp.data;
  },
};
