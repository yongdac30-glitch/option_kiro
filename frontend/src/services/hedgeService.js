/**
 * Hedge suggestion API service
 */
import apiClient from './api';

export const hedgeService = {
  async suggest(request) {
    const response = await apiClient.post('/api/suggest-hedge', request);
    return response.data;
  },
};
