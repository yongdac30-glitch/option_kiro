/**
 * Implied Volatility calculation API service
 */
import apiClient from './api';

export const ivService = {
  async calculate(request) {
    const response = await apiClient.post('/api/calculate-iv', request);
    return response.data;
  },
};
