/**
 * OKX market data API service
 */
import apiClient from './api';

export const okxService = {
  async getSpotPrice(instId = 'BTC-USDC') {
    const response = await apiClient.get('/api/okx/spot-price', { params: { instId } });
    return response.data;
  },

  async getExpiryDates(uly = 'BTC-USD') {
    const response = await apiClient.get('/api/okx/expiry-dates', { params: { uly } });
    return response.data;
  },

  async getOptionChain(uly = 'BTC-USD', expiry = null) {
    const params = { uly };
    if (expiry) params.expiry = expiry;
    const response = await apiClient.get('/api/okx/option-chain', { params });
    return response.data;
  },
};
