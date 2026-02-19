/**
 * Application global state context
 */
import { createContext, useContext, useReducer } from 'react';

// Action types
export const ActionTypes = {
  SET_PORTFOLIOS: 'SET_PORTFOLIOS',
  ADD_PORTFOLIO: 'ADD_PORTFOLIO',
  DELETE_PORTFOLIO: 'DELETE_PORTFOLIO',
  SET_ACTIVE_PORTFOLIO: 'SET_ACTIVE_PORTFOLIO',
  SET_POSITIONS: 'SET_POSITIONS',
  ADD_POSITION: 'ADD_POSITION',
  UPDATE_POSITION: 'UPDATE_POSITION',
  DELETE_POSITION: 'DELETE_POSITION',
  BATCH_DELETE_POSITIONS: 'BATCH_DELETE_POSITIONS',
  SET_MARKET_PRICE: 'SET_MARKET_PRICE',
  SET_VOLATILITY: 'SET_VOLATILITY',
  SET_PNL_DATA: 'SET_PNL_DATA',
  SET_LOADING: 'SET_LOADING',
  SET_ERROR: 'SET_ERROR',
};

// Initial state
const initialState = {
  portfolios: [],
  activePortfolioId: null,
  positions: [],
  marketPrices: {},
  volatilityScenarios: {},
  pnlData: null,
  loading: false,
  error: null,
};

// Reducer
function appReducer(state, action) {
  switch (action.type) {
    case ActionTypes.SET_PORTFOLIOS:
      return { ...state, portfolios: action.payload };

    case ActionTypes.ADD_PORTFOLIO:
      return { ...state, portfolios: [...state.portfolios, action.payload] };

    case ActionTypes.DELETE_PORTFOLIO: {
      const newPortfolios = state.portfolios.filter((p) => p.id !== action.payload);
      const newActiveId =
        state.activePortfolioId === action.payload
          ? (newPortfolios.length > 0 ? newPortfolios[0].id : null)
          : state.activePortfolioId;
      return {
        ...state,
        portfolios: newPortfolios,
        activePortfolioId: newActiveId,
        positions: newActiveId !== state.activePortfolioId ? [] : state.positions,
        pnlData: newActiveId !== state.activePortfolioId ? null : state.pnlData,
      };
    }

    case ActionTypes.SET_ACTIVE_PORTFOLIO:
      return { ...state, activePortfolioId: action.payload, positions: [], pnlData: null };

    case ActionTypes.SET_POSITIONS:
      return { ...state, positions: action.payload };

    case ActionTypes.ADD_POSITION:
      return { ...state, positions: [...state.positions, action.payload] };

    case ActionTypes.UPDATE_POSITION:
      return {
        ...state,
        positions: state.positions.map((pos) =>
          pos.id === action.payload.id ? action.payload : pos
        ),
      };

    case ActionTypes.DELETE_POSITION:
      return {
        ...state,
        positions: state.positions.filter((pos) => pos.id !== action.payload),
      };

    case ActionTypes.BATCH_DELETE_POSITIONS:
      return {
        ...state,
        positions: state.positions.filter((pos) => !action.payload.includes(pos.id)),
      };

    case ActionTypes.SET_MARKET_PRICE:
      return {
        ...state,
        marketPrices: {
          ...state.marketPrices,
          [action.payload.symbol]: action.payload.price,
        },
      };

    case ActionTypes.SET_VOLATILITY:
      return {
        ...state,
        volatilityScenarios: {
          ...state.volatilityScenarios,
          [action.payload.symbol]: action.payload.volatility,
        },
      };

    case ActionTypes.SET_PNL_DATA:
      return { ...state, pnlData: action.payload };

    case ActionTypes.SET_LOADING:
      return { ...state, loading: action.payload };

    case ActionTypes.SET_ERROR:
      return { ...state, error: action.payload };

    default:
      return state;
  }
}

// Create context
const AppContext = createContext();

// Provider component
export function AppProvider({ children }) {
  const [state, dispatch] = useReducer(appReducer, initialState);

  return (
    <AppContext.Provider value={{ state, dispatch }}>
      {children}
    </AppContext.Provider>
  );
}

// Custom hook to use context
export function useAppContext() {
  const context = useContext(AppContext);
  if (!context) {
    throw new Error('useAppContext must be used within AppProvider');
  }
  return context;
}
