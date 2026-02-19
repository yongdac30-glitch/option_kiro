/**
 * Portfolio API service
 */
import apiClient from './api';

export const portfolioService = {
  async getAll() {
    const response = await apiClient.get('/api/portfolios');
    return response.data;
  },

  async create(name) {
    const response = await apiClient.post('/api/portfolios', { name });
    return response.data;
  },

  async delete(id) {
    const response = await apiClient.delete(`/api/portfolios/${id}`);
    return response.data;
  },
};
