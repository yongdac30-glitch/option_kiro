/**
 * Backtest API service
 */
import apiClient from './api';

export const backtestService = {
  async run(params) {
    const response = await apiClient.post('/api/backtest/run', params);
    return response.data;
  },
};
