/**
 * Market Price API service
 */
import apiClient from './api';

export const marketPriceService = {
  /**
   * Update market price for a symbol
   * @param {string} underlyingSymbol - Symbol
   * @param {number} currentPrice - Current price
   */
  async update(underlyingSymbol, currentPrice) {
    const response = await apiClient.post('/api/market-prices', {
      underlying_symbol: underlyingSymbol,
      current_price: currentPrice,
    });
    return response.data;
  },

  /**
   * Get market price for a symbol
   * @param {string} symbol - Symbol
   */
  async get(symbol) {
    const response = await apiClient.get(`/api/market-prices/${symbol}`);
    return response.data;
  },
};
