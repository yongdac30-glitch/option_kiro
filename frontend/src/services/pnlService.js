/**
 * P&L Calculation API service
 */
import apiClient from './api';

export const pnlService = {
  /**
   * Calculate portfolio P&L
   * @param {Object} request - Calculation request
   */
  async calculate(request) {
    const response = await apiClient.post('/api/calculate-pnl', request);
    return response.data;
  },
};
