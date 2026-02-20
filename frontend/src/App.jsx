/**
 * Main App Component
 */
import { ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { AppProvider } from './context/AppContext';
import MainLayout from './components/MainLayout';
import IVCalculator from './components/IVCalculator';
import Backtest from './components/Backtest';
import RealBacktest from './components/RealBacktest';
import DeribitDebug from './components/DeribitDebug';
import BacktestVerify from './components/BacktestVerify';
import LeapsStrategy from './components/LeapsStrategy';
import LeapsUltimate from './components/LeapsUltimate';
import LeapsUltimateV2 from './components/LeapsUltimateV2';
import USLeaps from './components/USLeaps';
import DataCenter from './components/DataCenter';
import './App.css';

function App() {
  return (
    <ConfigProvider locale={zhCN}>
      <AppProvider>
        <BrowserRouter>
          <Routes>
            <Route path="/" element={<MainLayout />} />
            <Route path="/iv-calculator" element={<IVCalculator />} />
            <Route path="/backtest" element={<Backtest />} />
            <Route path="/real-backtest" element={<RealBacktest />} />
            <Route path="/deribit-debug" element={<DeribitDebug />} />
            <Route path="/backtest-verify" element={<BacktestVerify />} />
            <Route path="/leaps" element={<LeapsStrategy />} />
            <Route path="/leaps-ultimate" element={<LeapsUltimate />} />
            <Route path="/leaps-ultimate-v2" element={<LeapsUltimateV2 />} />
            <Route path="/us-leaps" element={<USLeaps />} />
            <Route path="/data-center" element={<DataCenter />} />
          </Routes>
        </BrowserRouter>
      </AppProvider>
    </ConfigProvider>
  );
}

export default App;
