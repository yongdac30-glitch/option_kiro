/**
 * Volatility Scenario API service
 */
import apiClient from './api';

export const volatilityService = {
  /**
   * Create volatility scenario
   * @param {Object} scenarioData - Scenario data
   */
  async create(scenarioData) {
    const response = await apiClient.post('/api/volatility-scenarios', scenarioData);
    return response.data;
  },

  /**
   * Get volatility scenarios
   * @param {string} underlyingSymbol - Optional filter by symbol
   */
  async getAll(underlyingSymbol = null) {
    const params = underlyingSymbol ? { underlying_symbol: underlyingSymbol } : {};
    const response = await apiClient.get('/api/volatility-scenarios', { params });
    return response.data;
  },
};
