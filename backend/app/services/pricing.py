"""Black-Scholes option pricing engine."""
import numpy as np
from scipy.stats import norm
from datetime import date, datetime
from typing import List, Dict, Optional


def calculate_time_to_expiration(expiration_date: date, target_date: date = None) -> float:
    """
    Calculate time to expiration in years.
    
    Args:
        expiration_date: Option expiration date
        target_date: Target date for calculation (default: today)
    
    Returns:
        Time to expiration in years
    """
    if target_date is None:
        target_date = date.today()
    
    days_to_expiration = (expiration_date - target_date).days
    
    # Ensure minimum time value to avoid division by zero
    if days_to_expiration <= 0:
        return 0.0001  # Very small positive number
    
    return days_to_expiration / 365.0


def black_scholes_price(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    option_type: str
) -> float:
    """
    Calculate Black-Scholes option price.
    
    Args:
        S: Current stock price
        K: Strike price
        T: Time to expiration (years)
        r: Risk-free rate
        sigma: Implied volatility
        option_type: 'CALL' or 'PUT'
    
    Returns:
        Option theoretical price
    """
    # Handle edge case: at expiration
    if T <= 0.0001:
        if option_type.upper() == 'CALL':
            return max(0, S - K)
        else:  # PUT
            return max(0, K - S)
    
    # Calculate d1 and d2
    d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    
    # Calculate option price
    if option_type.upper() == 'CALL':
        price = S * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)
    else:  # PUT
        price = K * np.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)
    
    return max(0, price)  # Ensure non-negative price


def black_scholes_vega(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Calculate Black-Scholes vega (derivative w.r.t. sigma)."""
    if T <= 0.0001 or sigma <= 0:
        return 0.0
    d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
    return S * np.sqrt(T) * norm.pdf(d1)


def implied_volatility(
    market_price: float,
    S: float,
    K: float,
    T: float,
    r: float,
    option_type: str,
    max_iterations: int = 100,
    tolerance: float = 1e-8
) -> Optional[float]:
    """
    Calculate implied volatility using Newton-Raphson method.

    Args:
        market_price: Observed option market price
        S: Current underlying price
        K: Strike price
        T: Time to expiration in years
        r: Risk-free rate
        option_type: 'CALL' or 'PUT'
        max_iterations: Max Newton iterations
        tolerance: Convergence tolerance

    Returns:
        Implied volatility as decimal, or None if not converged
    """
    # Initial guess
    sigma = 0.5

    for _ in range(max_iterations):
        price = black_scholes_price(S, K, T, r, sigma, option_type)
        vega = black_scholes_vega(S, K, T, r, sigma)

        diff = price - market_price

        if abs(diff) < tolerance:
            return sigma

        if vega < 1e-12:
            # Vega too small, try bisection fallback
            break

        sigma -= diff / vega

        # Keep sigma in reasonable bounds
        if sigma <= 0.001:
            sigma = 0.001
        if sigma > 20.0:
            sigma = 20.0

    # Bisection fallback
    lo, hi = 0.001, 20.0
    for _ in range(200):
        mid = (lo + hi) / 2
        price = black_scholes_price(S, K, T, r, mid, option_type)
        if abs(price - market_price) < tolerance:
            return mid
        if price < market_price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def calculate_position_value(
    position: Dict,
    current_price: float,
    implied_volatility: float,
    risk_free_rate: float = 0.05,
    target_date: date = None,
    contract_multiplier: float = 1.0
) -> Dict[str, float]:
    """
    Calculate current value and P&L for a single position.
    
    Args:
        position: Position data dict
        current_price: Current underlying price
        implied_volatility: Implied volatility
        risk_free_rate: Risk-free rate
        target_date: Target date for calculation
        contract_multiplier: Contract multiplier (e.g., 1.0 for crypto, 100 for stock options)
    
    Returns:
        Dict with current_value, pnl, and entry_cost
    """
    # Calculate time to expiration
    T = calculate_time_to_expiration(position['expiration_date'], target_date)
    
    # Calculate option theoretical price
    option_price = black_scholes_price(
        S=current_price,
        K=position['strike_price'],
        T=T,
        r=risk_free_rate,
        sigma=implied_volatility,
        option_type=position['option_type']
    )
    
    # Calculate position value with contract multiplier
    # quantity directly represents the number of units (e.g., -0.1 BTC)
    effective_quantity = position['quantity'] * contract_multiplier
    current_value = effective_quantity * option_price
    
    # Calculate entry cost (negative for sold positions)
    entry_cost = effective_quantity * position['entry_price']
    
    # Calculate P&L
    pnl = current_value - entry_cost
    
    return {
        'current_value': current_value,
        'pnl': pnl,
        'entry_cost': entry_cost
    }


def generate_price_points(
    current_price: float,
    range_percent: float = 0.5,
    num_points: int = 100,
    positions: List[Dict] = None
) -> List[float]:
    """
    Generate price points for P&L curve.
    Includes price=0 for sell put max loss calculation,
    and ensures all strike prices are covered.
    
    Args:
        current_price: Current underlying price
        range_percent: Range as percentage (0.5 = ±50%)
        num_points: Number of price points
        positions: Optional list of positions to include strike prices
    
    Returns:
        List of price points
    """
    min_price = max(0.01, current_price * (1 - range_percent))
    max_price = current_price * (1 + range_percent)
    
    # If there are sell put positions, extend range down to near 0
    if positions:
        has_sell_put = any(
            p.get('option_type', '').upper() == 'PUT' and p.get('quantity', 0) < 0
            for p in positions
        )
        has_sell_call = any(
            p.get('option_type', '').upper() == 'CALL' and p.get('quantity', 0) < 0
            for p in positions
        )
        if has_sell_put:
            min_price = 0.01  # Extend to near zero for sell put
        if has_sell_call:
            max_price = max(max_price, current_price * 3)  # Extend upward for sell call
    
    return np.linspace(min_price, max_price, num_points).tolist()


def calculate_portfolio_pnl(
    positions: List[Dict],
    price_points: List[float],
    implied_volatility: float,
    risk_free_rate: float = 0.05,
    target_date: date = None,
    contract_multiplier: float = 1.0
) -> List[Dict]:
    """
    Calculate portfolio P&L across multiple price points.
    
    Args:
        positions: List of position dicts
        price_points: List of underlying prices to evaluate
        implied_volatility: Implied volatility
        risk_free_rate: Risk-free rate
        target_date: Target date for calculation
        contract_multiplier: Contract multiplier
    
    Returns:
        List of dicts with price, total_pnl, and position_values
    """
    results = []
    
    for price in price_points:
        total_pnl = 0
        position_values = []
        
        for position in positions:
            value_data = calculate_position_value(
                position=position,
                current_price=price,
                implied_volatility=implied_volatility,
                risk_free_rate=risk_free_rate,
                target_date=target_date,
                contract_multiplier=contract_multiplier,
            )
            
            total_pnl += value_data['pnl']
            
            position_values.append({
                'position_id': position['id'],
                'current_value': value_data['current_value'],
                'pnl': value_data['pnl']
            })
        
        results.append({
            'price': price,
            'total_pnl': total_pnl,
            'position_values': position_values
        })
    
    return results


def find_max_loss(pnl_data: List[Dict]) -> Dict[str, float]:
    """
    Find maximum loss from P&L data.
    
    Args:
        pnl_data: List of P&L data points
    
    Returns:
        Dict with amount and at_price
    """
    if not pnl_data:
        return {'amount': 0.0, 'at_price': 0.0}
    
    min_pnl = min(pnl_data, key=lambda x: x['total_pnl'])
    return {
        'amount': round(min_pnl['total_pnl'], 2),
        'at_price': round(min_pnl['price'], 2)
    }


def find_max_profit(pnl_data: List[Dict]) -> Dict[str, float]:
    """
    Find maximum profit from P&L data.
    
    Args:
        pnl_data: List of P&L data points
    
    Returns:
        Dict with amount and at_price
    """
    if not pnl_data:
        return {'amount': 0.0, 'at_price': 0.0}
    
    max_pnl = max(pnl_data, key=lambda x: x['total_pnl'])
    amount = round(max_pnl['total_pnl'], 2)
    return {
        'amount': 0.0 if amount == 0 else amount,
        'at_price': round(max_pnl['price'], 2)
    }
