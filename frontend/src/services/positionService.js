/**
 * Position API service
 */
import apiClient from './api';

export const positionService = {
  /**
   * Get all positions
   * @param {string} underlyingSymbol - Optional filter by symbol
   * @param {number} portfolioId - Optional filter by portfolio
   */
  async getAll(underlyingSymbol = null, portfolioId = null) {
    const params = {};
    if (underlyingSymbol) params.underlying_symbol = underlyingSymbol;
    if (portfolioId) params.portfolio_id = portfolioId;
    const response = await apiClient.get('/api/positions', { params });
    return response.data;
  },

  async getById(id) {
    const response = await apiClient.get(`/api/positions/${id}`);
    return response.data;
  },

  async create(positionData) {
    const response = await apiClient.post('/api/positions', positionData);
    return response.data;
  },

  async update(id, positionData) {
    const response = await apiClient.put(`/api/positions/${id}`, positionData);
    return response.data;
  },

  async delete(id) {
    const response = await apiClient.delete(`/api/positions/${id}`);
    return response.data;
  },

  async batchDelete(ids) {
    const response = await apiClient.post('/api/positions/batch-delete', { ids });
    return response.data;
  },
};
