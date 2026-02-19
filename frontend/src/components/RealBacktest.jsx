/**
 * Real Data Backtest Page - Using Deribit historical option trades
 * With IV smile visualization per trade
 */
import { useState, useEffect } from 'react';
import {
  Layout, Card, Form, Select, InputNumber, DatePicker, Button,
  Row, Col, Typography, Space, Table, Tag, Statistic, Divider, message, Spin,
  Modal, Input, Popconfirm, Alert, Empty, Progress, Tabs, Collapse,
} from 'antd';
import {
  ArrowLeftOutlined, PlusOutlined, DeleteOutlined,
  LineChartOutlined, SaveOutlined, FolderOpenOutlined, DatabaseOutlined,
  BarChartOutlined, BookOutlined, QuestionCircleOutlined,
} from '@ant-design/icons';
import { Link } from 'react-router-dom';
import dayjs from 'dayjs';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ResponsiveContainer, ReferenceLine, BarChart, Bar, Cell,
} from 'recharts';
import { deribitService } from '../services/deribitService';

const { Header: AntHeader, Content, Footer } = Layout;
const { Title, Text } = Typography;
const { Option } = Select;
const { RangePicker } = DatePicker;

const DEFAULT_LEGS = [
  { key: 1, option_type: 'PUT', strike_offset_pct: -0.20, quantity: -1, expiry_months: 1 },
  { key: 2, option_type: 'PUT', strike_offset_pct: -0.25, quantity: 2, expiry_months: 1 },
];

const STORAGE_KEY = 'real_backtest_strategies';
const loadStrategies = () => {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]'); } catch { return []; }
};
const saveStrategies = (list) => localStorage.setItem(STORAGE_KEY, JSON.stringify(list));

// Colors for smile lines
const SMILE_COLORS = ['#1890ff', '#52c41a', '#faad14', '#f5222d', '#722ed1', '#13c2c2', '#eb2f96'];

// ── IV Smile Chart Component ───────────────────────────────────────────────

function IVSmileChart({ smileData }) {
  if (!smileData || smileData.length === 0) {
    return <Empty description="无IV微笑曲线数据" />;
  }

  // Build chart data: merge all smiles into one dataset keyed by strike
  // Each smile becomes a separate line
  const allStrikes = new Set();
  const smileLines = [];

  smileData.forEach((item, idx) => {
    const openSmile = item.open_smile || [];
    const closeSmile = item.close_smile || [];

    if (openSmile.length > 0) {
      smileLines.push({
        key: `open_${idx}`,
        label: `开仓 ${item.open_date} ${item.option_type} K=${item.strike}`,
        color: SMILE_COLORS[idx % SMILE_COLORS.length],
        dash: false,
        points: openSmile,
        targetStrike: item.strike,
        spot: item.open_spot,
      });
      openSmile.forEach(p => allStrikes.add(p.strike));
    }
    if (closeSmile.length > 0) {
      smileLines.push({
        key: `close_${idx}`,
        label: `平仓 ${item.close_date} ${item.option_type} K=${item.strike}`,
        color: SMILE_COLORS[idx % SMILE_COLORS.length],
        dash: true,
        points: closeSmile,
        targetStrike: item.strike,
        spot: item.close_spot,
      });
      closeSmile.forEach(p => allStrikes.add(p.strike));
    }
  });

  if (smileLines.length === 0) {
    return <Empty description="无IV微笑曲线数据" />;
  }

  // Build unified data array sorted by strike
  const sortedStrikes = Array.from(allStrikes).sort((a, b) => a - b);
  const chartData = sortedStrikes.map(strike => {
    const row = { strike };
    smileLines.forEach(line => {
      const pt = line.points.find(p => p.strike === strike);
      if (pt) row[line.key] = +(pt.iv * 100).toFixed(1);
    });
    return row;
  });

  return (
    <div>
      <ResponsiveContainer width="100%" height={350}>
        <LineChart data={chartData} margin={{ top: 5, right: 30, left: 10, bottom: 5 }}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis
            dataKey="strike"
            tick={{ fontSize: 11 }}
            tickFormatter={(v) => (v / 1000).toFixed(0) + 'K'}
            label={{ value: '行权价', position: 'insideBottom', offset: -5, fontSize: 12 }}
          />
          <YAxis
            tick={{ fontSize: 11 }}
            label={{ value: 'IV (%)', angle: -90, position: 'insideLeft', fontSize: 12 }}
            domain={['auto', 'auto']}
          />
          <Tooltip
            formatter={(value, name) => [value + '%', name]}
            labelFormatter={(v) => '行权价: $' + Number(v).toLocaleString()}
          />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          {smileLines.map(line => (
            <Line
              key={line.key}
              type="monotone"
              dataKey={line.key}
              name={line.label}
              stroke={line.color}
              strokeWidth={2}
              strokeDasharray={line.dash ? '5 5' : undefined}
              dot={{ r: 3 }}
              connectNulls
            />
          ))}
          {/* Reference lines for target strikes */}
          {smileData.map((item, idx) => (
            <ReferenceLine
              key={`ref_${idx}`}
              x={item.strike}
              stroke={SMILE_COLORS[idx % SMILE_COLORS.length]}
              strokeDasharray="3 3"
              strokeWidth={1}
              label={{ value: 'K', fontSize: 10 }}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

// ── Main Component ─────────────────────────────────────────────────────────

export default function RealBacktest() {
  const [form] = Form.useForm();
  const [legs, setLegs] = useState(DEFAULT_LEGS);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [nextKey, setNextKey] = useState(3);
  const [savedStrategies, setSavedStrategies] = useState([]);
  const [saveModalOpen, setSaveModalOpen] = useState(false);
  const [strategyName, setStrategyName] = useState('');
  const [selectedSmileIdx, setSelectedSmileIdx] = useState(null);
  const [progress, setProgress] = useState(null); // { pct, date }
  const [activeTab, setActiveTab] = useState('basic');
  const [docOpen, setDocOpen] = useState(false);
  const [martingaleMaxDoubles, setMartingaleMaxDoubles] = useState(3);
  const [enhancedRecoverPct, setEnhancedRecoverPct] = useState(110); // 110%
  const [enhancedMaxMultiplier, setEnhancedMaxMultiplier] = useState(10);
  const [volSellIv, setVolSellIv] = useState(80);
  const [volBuyIv, setVolBuyIv] = useState(40);
  const [volQuantity, setVolQuantity] = useState(1);
  const [volStrikeOffset, setVolStrikeOffset] = useState(0);
  const [rollPutOffset, setRollPutOffset] = useState(10);
  const [rollPutQuantity, setRollPutQuantity] = useState(1);
  const [hedgePutOffset, setHedgePutOffset] = useState(10);
  const [hedgePutQuantity, setHedgePutQuantity] = useState(1);
  const [hedgePutCrashPct, setHedgePutCrashPct] = useState(20);
  const [hedgePutHedgeQty, setHedgePutHedgeQty] = useState(1);
  const [channelLookback, setChannelLookback] = useState(90);
  const [channelQuantity, setChannelQuantity] = useState(1);
  const [wheelPutOffset, setWheelPutOffset] = useState(5);
  const [wheelCallOffset, setWheelCallOffset] = useState(5);
  const [wheelQuantity, setWheelQuantity] = useState(1);
  const [wheelReinvest, setWheelReinvest] = useState(true);
  const [gridStep, setGridStep] = useState(100);
  const [gridQuantity, setGridQuantity] = useState(1);
  const [gridMinYield, setGridMinYield] = useState(10);
  const [gridRangeUp, setGridRangeUp] = useState(5);
  const [gridRangeDown, setGridRangeDown] = useState(5);
  const [gridMaxPositions, setGridMaxPositions] = useState(10);
  const [leapsMaxAnnualTvPct, setLeapsMaxAnnualTvPct] = useState(10);
  const [leapsMinMonths, setLeapsMinMonths] = useState(12);
  const [leapsCloseDaysBefore, setLeapsCloseDaysBefore] = useState(30);
  const [leapsQuantity, setLeapsQuantity] = useState(1);
  const [leapsNumStrikes, setLeapsNumStrikes] = useState(15);

  useEffect(() => { setSavedStrategies(loadStrategies()); }, []);

  const handleSaveStrategy = () => {
    const name = strategyName.trim();
    if (!name) { message.warning('请输入策略名称'); return; }
    const values = form.getFieldsValue();
    const [start, end] = values.date_range || [];
    const preset = {
      name,
      underlying: values.underlying,
      start_date: start?.format('YYYY-MM-DD'),
      end_date: end?.format('YYYY-MM-DD'),
      roll_day: values.roll_day,
      close_days_before: values.close_days_before,
      initial_capital: values.initial_capital,
      contract_multiplier: values.contract_multiplier,
      legs: legs.map(({ key, ...rest }) => rest),
      saved_at: new Date().toISOString(),
    };
    const existing = loadStrategies();
    const idx = existing.findIndex((s) => s.name === name);
    if (idx >= 0) existing[idx] = preset; else existing.push(preset);
    saveStrategies(existing);
    setSavedStrategies(existing);
    setSaveModalOpen(false);
    setStrategyName('');
    message.success('策略已保存');
  };

  const handleLoadStrategy = (name) => {
    const preset = savedStrategies.find((s) => s.name === name);
    if (!preset) return;
    form.setFieldsValue({
      underlying: preset.underlying,
      date_range: preset.start_date && preset.end_date
        ? [dayjs(preset.start_date), dayjs(preset.end_date)] : undefined,
      roll_day: preset.roll_day,
      close_days_before: preset.close_days_before,
      initial_capital: preset.initial_capital,
      contract_multiplier: preset.contract_multiplier,
    });
    const restored = (preset.legs || []).map((l, i) => ({ ...l, key: i + 1 }));
    setLegs(restored);
    setNextKey(restored.length + 1);
    message.success(`已加载策略: ${name}`);
  };

  const handleDeleteStrategy = (name) => {
    const updated = savedStrategies.filter((s) => s.name !== name);
    saveStrategies(updated);
    setSavedStrategies(updated);
    message.success('策略已删除');
  };

  const addLeg = () => {
    setLegs([...legs, { key: nextKey, option_type: 'PUT', strike_offset_pct: -0.10, quantity: -1, expiry_months: 1 }]);
    setNextKey(nextKey + 1);
  };
  const removeLeg = (key) => setLegs(legs.filter((l) => l.key !== key));
  const updateLeg = (key, field, value) => setLegs(legs.map((l) => l.key === key ? { ...l, [field]: value } : l));

  const handleRun = async (values) => {
    if (activeTab !== 'vol' && activeTab !== 'rollput' && activeTab !== 'hedgeput' && activeTab !== 'channel' && activeTab !== 'wheel' && activeTab !== 'grid' && activeTab !== 'leaps' && legs.length === 0) { message.error('请至少添加一个策略腿'); return; }
    setLoading(true);
    setResult(null);
    setSelectedSmileIdx(null);
    setProgress({ pct: 0, date: '' });
    try {
      const [startDate, endDate] = values.date_range;
      const params = {
        underlying: values.underlying,
        start_date: startDate.format('YYYY-MM-DD'),
        end_date: endDate.format('YYYY-MM-DD'),
        roll_day: values.roll_day,
        close_days_before_expiry: values.close_days_before,
        initial_capital: values.initial_capital,
        contract_multiplier: values.contract_multiplier,
        legs: (activeTab === 'vol' || activeTab === 'rollput' || activeTab === 'hedgeput' || activeTab === 'channel' || activeTab === 'wheel' || activeTab === 'grid' || activeTab === 'leaps') ? [] : legs.map(({ key, ...rest }) => rest),
        martingale: activeTab === 'martingale',
        max_double_times: activeTab === 'martingale' ? martingaleMaxDoubles : 3,
        enhanced_martingale: activeTab === 'enhanced',
        enhanced_martingale_recover_pct: activeTab === 'enhanced' ? enhancedRecoverPct / 100 : 1.1,
        enhanced_max_multiplier: activeTab === 'enhanced' ? enhancedMaxMultiplier : 10,
        vol_strategy: activeTab === 'vol',
        vol_sell_iv: volSellIv,
        vol_buy_iv: volBuyIv,
        vol_quantity: volQuantity,
        vol_strike_offset_pct: volStrikeOffset,
        roll_put_strategy: activeTab === 'rollput',
        roll_put_offset_pct: rollPutOffset,
        roll_put_quantity: rollPutQuantity,
        hedge_put_strategy: activeTab === 'hedgeput',
        hedge_put_offset_pct: hedgePutOffset,
        hedge_put_quantity: hedgePutQuantity,
        hedge_put_crash_pct: hedgePutCrashPct,
        hedge_put_hedge_quantity: hedgePutHedgeQty,
        channel_strategy: activeTab === 'channel',
        channel_lookback_days: channelLookback,
        channel_quantity: channelQuantity,
        wheel_strategy: activeTab === 'wheel',
        wheel_put_offset_pct: wheelPutOffset,
        wheel_call_offset_pct: wheelCallOffset,
        wheel_quantity: wheelQuantity,
        wheel_reinvest: wheelReinvest,
        grid_strategy: activeTab === 'grid',
        grid_step: gridStep,
        grid_quantity: gridQuantity,
        grid_min_yield_pct: gridMinYield,
        grid_range_up: gridRangeUp,
        grid_range_down: gridRangeDown,
        grid_max_positions: gridMaxPositions,
        leaps_strategy: activeTab === 'leaps',
        leaps_max_annual_tv_pct: leapsMaxAnnualTvPct,
        leaps_min_months: leapsMinMonths,
        leaps_close_days_before: leapsCloseDaysBefore,
        leaps_quantity: leapsQuantity,
        leaps_num_strikes: leapsNumStrikes,
      };
      deribitService.runRealBacktestStream(
        params,
        (prog) => setProgress({ pct: prog.pct, date: prog.date }),
        (data) => {
          setResult(data);
          setLoading(false);
          setProgress(null);
          message.success('真实数据回测完成');
        },
        (errMsg) => {
          message.error(errMsg || '回测失败');
          setLoading(false);
          setProgress(null);
        },
      );
    } catch (error) {
      const detail = error.response?.data?.detail;
      message.error(detail || '回测失败');
      console.error(error);
      setLoading(false);
      setProgress(null);
    }
  };

  const tradeColumns = [
    { title: '开仓日', dataIndex: 'open_date', key: 'open_date', width: 100 },
    { title: '平仓日', dataIndex: 'close_date', key: 'close_date', width: 100 },
    {
      title: '类型', dataIndex: 'option_type', key: 'option_type', width: 70,
      render: (t) => <Tag color={t === 'PUT' ? 'red' : 'green'}>{t}</Tag>,
    },
    { title: '行权价', dataIndex: 'strike', key: 'strike', width: 100, render: (v) => '$' + v.toLocaleString() },
    { title: '开仓现货', dataIndex: 'open_spot', key: 'open_spot', width: 100, render: (v) => v ? '$' + v.toLocaleString() : '-' },
    { title: '平仓现货', dataIndex: 'close_spot', key: 'close_spot', width: 100, render: (v) => v ? '$' + v.toLocaleString() : '-' },
    {
      title: '距离%', dataIndex: 'strike_distance_pct', key: 'strike_distance_pct', width: 80,
      render: (v) => v != null ? <span style={{ color: v >= 0 ? '#389e0d' : '#cf1322' }}>{v > 0 ? '+' : ''}{v.toFixed(1)}%</span> : '-',
    },
    {
      title: '数量', dataIndex: 'quantity', key: 'quantity', width: 70,
      render: (v) => <span style={{ color: v > 0 ? 'green' : 'red' }}>{v > 0 ? '+' + v : v}</span>,
    },
    { title: '开仓价', dataIndex: 'open_price', key: 'open_price', width: 100, render: (v) => '$' + v.toFixed(2) },
    { title: '平仓价', dataIndex: 'close_price', key: 'close_price', width: 100, render: (v) => '$' + v.toFixed(2) },
    {
      title: '盈亏', dataIndex: 'pnl', key: 'pnl', width: 100,
      render: (v) => <span style={{ color: v >= 0 ? '#389e0d' : '#cf1322', fontWeight: 600 }}>{'$' + v.toFixed(2)}</span>,
    },
    {
      title: '净值', dataIndex: 'equity_after', key: 'equity_after', width: 100,
      render: (v) => v != null ? '$' + v.toFixed(2) : '-',
    },
    { title: '合约', dataIndex: 'instrument', key: 'instrument', width: 200, ellipsis: true },
    {
      title: '数据源', dataIndex: 'data_source', key: 'data_source', width: 80,
      render: (v) => {
        const colors = { real: 'green', iv_smile: 'green', mark: 'blue', model: 'orange', settlement: 'default' };
        const labels = { real: '真实', iv_smile: 'IV曲线', mark: '标记价', model: '模型', settlement: '结算' };
        return <Tag color={colors[v] || 'default'}>{labels[v] || v}</Tag>;
      },
    },
    {
      title: 'IV', dataIndex: 'iv_used', key: 'iv_used', width: 70,
      render: (v) => v != null ? (v * 100).toFixed(1) + '%' : '-',
    },
    { title: '平仓原因', dataIndex: 'close_reason', key: 'close_reason', width: 140 },
    {
      title: 'IV曲线', key: 'smile', width: 80, fixed: 'right',
      render: (_, record) => {
        // Use record.key as global trade index (set during dataSource mapping)
        const tradeIdx = record.key;
        const smile = result?.iv_smiles?.find(s => s.trade_idx === tradeIdx);
        const hasSmile = smile && ((smile.open_smile && smile.open_smile.length > 0) || (smile.close_smile && smile.close_smile.length > 0));
        return hasSmile ? (
          <Button type="link" size="small" onClick={() => setSelectedSmileIdx(tradeIdx)}>
            查看
          </Button>
        ) : <Text type="secondary" style={{ fontSize: 11 }}>-</Text>;
      },
    },
  ];

  // Get smile data for selected trade
  const selectedSmile = selectedSmileIdx !== null
    ? result?.iv_smiles?.find(s => s.trade_idx === selectedSmileIdx)
    : null;

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <AntHeader style={{ background: '#001529', padding: '0 24px', display: 'flex', alignItems: 'center' }}>
        <Link to="/backtest" style={{ display: 'flex', alignItems: 'center', marginRight: 16 }}>
          <ArrowLeftOutlined style={{ fontSize: 18, color: '#1890ff' }} />
        </Link>
        <DatabaseOutlined style={{ fontSize: 24, color: '#52c41a', marginRight: 12 }} />
        <Title level={3} style={{ color: 'white', margin: 0, fontSize: 20 }}>真实数据回测 (Deribit)</Title>
      </AntHeader>

      <Content style={{ padding: 24, background: '#f0f2f5' }}>
        <div style={{ maxWidth: 1400, margin: '0 auto' }}>
          <Card
            size="small"
            style={{ marginBottom: 16 }}
            title={<Space><BookOutlined style={{ color: '#1890ff' }} /><span>策略文档中心</span></Space>}
            extra={<Button type="link" onClick={() => setDocOpen(!docOpen)}>{docOpen ? '收起' : '展开详情'}</Button>}
          >
            <Text type="secondary">使用Deribit真实历史期权交易数据回测。IV数据从ATM附近多个行权价的真实成交反推，通过插值构建IV微笑曲线。所有历史数据缓存在本地数据库，重复回测无需重新调用API。</Text>
            {docOpen && (
              <div style={{ marginTop: 16 }}>
                <Collapse
                  accordion
                  items={[
                    {
                      key: 'common',
                      label: '📋 通用参数说明',
                      children: (
                        <div style={{ fontSize: 13, lineHeight: 1.8 }}>
                          <p><Tag color="blue">标的</Tag> 选择回测的底层资产（BTC / ETH），数据来源为Deribit交易所。</p>
                          <p><Tag color="blue">回测时间段</Tag> 起止日期，系统会获取该时间段内的每日指数价格。</p>
                          <p><Tag color="blue">初始资金</Tag> 回测起始的账户资金（USD），所有盈亏基于此计算。</p>
                          <p><Tag color="blue">每月开仓日</Tag> 每月第几天开仓（1-28），到达该日且无持仓时自动开仓。</p>
                          <p><Tag color="blue">到期前N天平仓</Tag> 在期权到期前N天主动平仓，避免到期日的不确定性。设为0则持有到到期。</p>
                          <p><Tag color="blue">合约乘数</Tag> 每张合约对应的标的数量。Deribit BTC期权默认1张=1BTC，如需缩小仓位可设为0.1等。</p>
                          <Divider style={{ margin: '8px 0' }} />
                          <p><strong>回测定价逻辑：</strong>开仓和平仓价格优先使用Deribit真实成交数据反推的IV微笑曲线插值定价（Black-Scholes），无真实数据时回退到默认IV=60%的模型定价。到期时使用内在价值结算。</p>
                          <p><strong>数据缓存：</strong>所有从Deribit API获取的价格和IV数据都缓存在本地SQLite数据库中，重复回测同一时间段不会重新调用API。</p>
                        </div>
                      ),
                    },
                    {
                      key: 'basic',
                      label: '🔧 基础策略',
                      children: (
                        <div style={{ fontSize: 13, lineHeight: 1.8 }}>
                          <p><strong>原理：</strong>自定义多腿期权组合策略。每个"策略腿"定义一个期权头寸，可以组合出各种经典策略（卖PUT、牛市价差、铁鹰等）。</p>
                          <p><strong>参数：</strong></p>
                          <ul>
                            <li><Tag>期权类型</Tag> PUT 或 CALL</li>
                            <li><Tag>行权价偏移%</Tag> 相对于当前现货价格的偏移。-20%表示现货下方20%处（OTM PUT），+10%表示现货上方10%处（OTM CALL）</li>
                            <li><Tag>数量</Tag> 正数=买入，负数=卖出。例如 -1 表示卖出1张</li>
                            <li><Tag>滚动周期(月)</Tag> 每个腿的到期月数，1=当月到期，2=两个月后到期</li>
                          </ul>
                          <p><strong>回测逻辑：</strong>每月开仓日，按各腿配置开仓。到期前平仓或到期结算。下一周期自动重新开仓。</p>
                          <p><strong>示例：</strong>卖出20% OTM PUT + 买入25% OTM PUT = 牛市PUT价差（Bull Put Spread）</p>
                        </div>
                      ),
                    },
                    {
                      key: 'martingale',
                      label: '🎰 马丁格尔策略',
                      children: (
                        <div style={{ fontSize: 13, lineHeight: 1.8 }}>
                          <p><strong>原理：</strong>在基础策略的基础上，当一个周期亏损时，下一周期将仓位翻倍，直到盈利回本。经典的"加倍下注"策略。</p>
                          <p><strong>参数：</strong></p>
                          <ul>
                            <li><Tag>最大翻倍次数</Tag> 连续亏损时最多翻倍几次。设为3表示最大倍数为2³=8倍。超过后恢复基础仓位。</li>
                          </ul>
                          <p><strong>回测逻辑：</strong>每个周期结束时统计盈亏。亏损→下周期仓位×2；盈利→恢复基础仓位。连续亏损达到上限后不再加倍。</p>
                          <p><strong>风险提示：</strong>极端行情下连续亏损会导致仓位指数级增长，可能造成巨大亏损。</p>
                        </div>
                      ),
                    },
                    {
                      key: 'enhanced',
                      label: '📈 增强型马丁格尔',
                      children: (
                        <div style={{ fontSize: 13, lineHeight: 1.8 }}>
                          <p><strong>原理：</strong>改进版马丁格尔。不是简单翻倍，而是根据累计亏损额动态计算下一周期仓位，使预期收益刚好覆盖累计亏损的指定比例。</p>
                          <p><strong>参数：</strong></p>
                          <ul>
                            <li><Tag>目标回收比例</Tag> 例如110%表示下一周期的预期收益要覆盖累计亏损的110%（含10%额外利润）</li>
                            <li><Tag>最大倍数限制</Tag> 仓位倍数的上限，防止仓位过大</li>
                          </ul>
                          <p><strong>回测逻辑：</strong>亏损时根据公式计算所需仓位倍数；盈利时用盈利抵扣累计亏损，直到完全回本后恢复基础仓位。</p>
                        </div>
                      ),
                    },
                    {
                      key: 'vol',
                      label: '📊 波动率策略',
                      children: (
                        <div style={{ fontSize: 13, lineHeight: 1.8 }}>
                          <p><strong>原理：</strong>基于隐含波动率（IV）水平决定操作方向。高IV时卖出期权（收取高权利金），低IV时买入期权（权利金便宜）。</p>
                          <p><strong>参数：</strong></p>
                          <ul>
                            <li><Tag>卖出IV阈值</Tag> 当ATM IV高于此值时，卖出PUT（默认80%）</li>
                            <li><Tag>买入IV阈值</Tag> 当ATM IV低于此值时，买入PUT（默认40%）</li>
                            <li><Tag>数量</Tag> 每次操作的合约数量</li>
                            <li><Tag>行权价偏移%</Tag> 0=ATM，负值=OTM PUT方向</li>
                          </ul>
                          <p><strong>回测逻辑：</strong>每月到期后检查当前ATM IV水平，高于卖出阈值→卖PUT，低于买入阈值→买PUT，中间区域→不操作。</p>
                        </div>
                      ),
                    },
                    {
                      key: 'rollput',
                      label: '🔄 Roll PUT策略',
                      children: (
                        <div style={{ fontSize: 13, lineHeight: 1.8 }}>
                          <p><strong>原理：</strong>持续卖出OTM PUT收取权利金。到期时如果期权有价值（价内），则Roll到下月同一行权价继续卖；如果到期归零（价外），则按新的现货价格重新选择行权价开仓。</p>
                          <p><strong>参数：</strong></p>
                          <ul>
                            <li><Tag>行权价偏移(OTM)</Tag> 在现货价格下方多少百分比处卖PUT（默认10%）</li>
                            <li><Tag>卖出数量</Tag> 每次卖出的合约数量</li>
                          </ul>
                          <p><strong>回测逻辑：</strong>开仓→到期前平仓→判断：平仓价&gt;0（有价值）→Roll同Strike到下月；平仓价=0（归零）→按新价格偏移重新开仓。</p>
                          <p><strong>优势：</strong>亏损时不换行权价，等待价格回归；盈利时跟随价格调整行权价。</p>
                        </div>
                      ),
                    },
                    {
                      key: 'hedgeput',
                      label: '🛡️ Hedge PUT策略',
                      children: (
                        <div style={{ fontSize: 13, lineHeight: 1.8 }}>
                          <p><strong>原理：</strong>在Roll PUT基础上增加暴跌保护。正常情况下卖PUT收权利金，当现货暴跌超过阈值时，切换为买入PUT进行对冲，直到价格恢复后再恢复卖PUT。</p>
                          <p><strong>参数：</strong></p>
                          <ul>
                            <li><Tag>行权价偏移(OTM)</Tag> 正常卖PUT的行权价偏移（默认10%）</li>
                            <li><Tag>卖出数量</Tag> 正常卖出的合约数量</li>
                            <li><Tag>暴跌阈值</Tag> 现货从开仓价下跌超过此百分比时触发对冲（默认20%）</li>
                            <li><Tag>对冲买入数量</Tag> 触发对冲后每月买入PUT的数量</li>
                          </ul>
                          <p><strong>回测逻辑：</strong>正常卖PUT→亏损时Roll同Strike→暴跌触发→切换为买PUT对冲→价格恢复到原始开仓水平→恢复卖PUT。</p>
                        </div>
                      ),
                    },
                    {
                      key: 'channel',
                      label: '📏 进阶通道策略',
                      children: (
                        <div style={{ fontSize: 13, lineHeight: 1.8 }}>
                          <p><strong>原理：</strong>结合价格通道和飞轮策略。基于滚动N天最高/最低价确定通道上下轨，初始在下轨卖PUT，若被行权则持有现货，转为在上轨卖CALL，若CALL被行权则回到卖PUT，形成通道飞轮循环。</p>
                          <p><strong>参数：</strong></p>
                          <ul>
                            <li><Tag>回看天数</Tag> 计算通道的滚动窗口（默认90天≈3个月）</li>
                            <li><Tag>每次卖出数量</Tag> 每次操作的合约数量</li>
                          </ul>
                          <p><strong>回测逻辑：</strong>计算通道上下轨→在下轨卖PUT→到期判断：OTM→继续卖PUT / ITM→记录"买入"价，转为在上轨卖CALL→到期判断：OTM→继续卖CALL / ITM→结算现货差价，回到卖PUT。</p>
                          <p><strong>与飞轮策略的区别：</strong>飞轮策略的行权价由固定偏移百分比决定，通道策略的行权价由滚动通道的上下轨决定，更贴合市场实际波动范围。</p>
                          <p><strong>适用场景：</strong>震荡市中通道稳定时效果好，趋势突破通道时可能被行权。</p>
                        </div>
                      ),
                    },
                    {
                      key: 'wheel',
                      label: '🎡 飞轮策略 (Wheel)',
                      children: (
                        <div style={{ fontSize: 13, lineHeight: 1.8 }}>
                          <p><strong>原理：</strong>经典期权飞轮策略，通过不断卖出期权收取权利金，形成"飞轮效应"。分两个阶段循环：</p>
                          <ol>
                            <li><strong>卖PUT阶段：</strong>卖出OTM PUT收取权利金。如果到期时PUT为价外（OTM），权利金全部收入，继续卖PUT。</li>
                            <li><strong>卖CALL阶段：</strong>如果PUT到期时为价内（ITM，即"被行权"），视为以行权价"买入"了标的资产。此时转为卖出OTM CALL（Covered Call）。如果CALL到期为价外，继续卖CALL；如果CALL被行权，视为以行权价"卖出"标的，实现标的差价盈亏，然后回到卖PUT阶段。</li>
                          </ol>
                          <p><strong>参数：</strong></p>
                          <ul>
                            <li><Tag>PUT行权价偏移(OTM)</Tag> 卖PUT时行权价在现货下方的百分比（默认5%）</li>
                            <li><Tag>CALL行权价偏移(OTM)</Tag> 卖CALL时行权价在现货上方的百分比（默认5%）</li>
                            <li><Tag>每次卖出数量</Tag> 每次操作的合约数量</li>
                          </ul>
                          <p><strong>回测逻辑：</strong>卖PUT→到期判断：OTM→继续卖PUT / ITM→记录"买入"价，转卖CALL→到期判断：OTM→继续卖CALL / ITM→结算标的差价，回到卖PUT。每个阶段都收取权利金。</p>
                          <p><strong>优势：</strong>持续产生现金流；在震荡市中效果显著。<strong>风险：</strong>标的大幅下跌时PUT被行权后持有标的会产生浮亏。</p>
                        </div>
                      ),
                    },
                    {
                      key: 'grid',
                      label: '📐 网格策略 (Grid)',
                      children: (
                        <div style={{ fontSize: 13, lineHeight: 1.8 }}>
                          <p><strong>原理：</strong>在当前现货价格上下按固定间距（网格步长）生成多个行权价格位，在每个满足最低年化收益率的网格位上卖出PUT。若PUT被行权（价内到期），则视为在该行权价"买入"了现货，随后在该行权价往上一格的位置卖出CALL（Covered Call）。若CALL也被行权，则完成一个网格周期，回到卖PUT状态。每个网格位独立运作，形成多层级的期权飞轮网格。</p>
                          <p><strong>参数：</strong></p>
                          <ul>
                            <li><Tag>网格步长(USD)</Tag> 相邻网格之间的价格间距。ETH默认100，即每隔$100一个网格位</li>
                            <li><Tag>每格数量</Tag> 在每个网格位上卖出期权的合约数量</li>
                            <li><Tag>最低年化收益率</Tag> 只有当某个网格位的PUT权利金折算年化收益率超过此阈值时才开仓。年化收益率 = (权利金 / 行权价) × (365 / 持有天数) × 100%</li>
                            <li><Tag>向上网格数</Tag> 在现货价格上方生成的网格层数（默认5层）</li>
                            <li><Tag>向下网格数</Tag> 在现货价格下方生成的网格层数（默认5层）</li>
                            <li><Tag>最大总仓位</Tag> 所有网格位的持仓总数上限（PUT + CALL合计），达到上限后不再开新仓</li>
                          </ul>
                          <p><strong>回测逻辑：</strong>每月1号，以当前现货价格为中心，向上和向下各生成指定层数的网格行权价（每100整数位一格）。对每个空闲网格位计算卖出PUT的年化收益率，≥阈值则开仓卖PUT，当月底到期。到期时：PUT价外→网格回到空闲状态；PUT价内（被行权）→视为买入现货，转为在该网格往上一格卖CALL。CALL到期时：CALL价外→继续卖CALL；CALL价内（被行权）→结算现货盈亏，网格回到空闲状态。</p>
                          <p><strong>独立运作：</strong>每个网格位独立运作。某一网PUT被行权后，该网转为卖CALL（持有现货），但不影响其他网格位继续卖PUT，只要总仓位数量不超过上限即可。</p>
                          <p><strong>适用场景：</strong>适合ETH等波动适中的标的在震荡市中运行。多个网格位并行运作，分散风险。大幅下跌时多个网格位可能同时被行权持有现货，需注意总仓位控制。</p>
                        </div>
                      ),
                    },
                    {
                      key: 'leaps',
                      label: '📅 LEAPS长期期权策略',
                      children: (
                        <div style={{ fontSize: 13, lineHeight: 1.8 }}>
                          <p><strong>原理：</strong>LEAPS（Long-Term Equity Anticipation Securities）策略通过买入到期日≥1年的深度实值或平值CALL期权，以较低的时间价值成本获取标的资产的长期上涨敞口。相比直接持有现货，LEAPS CALL的资金占用更低，同时下行风险有限（最大亏损为权利金）。策略核心在于筛选时间价值占比低的合约，降低持有成本。</p>
                          <p><strong>参数：</strong></p>
                          <ul>
                            <li><Tag>最大年化时间价值%</Tag> 筛选条件：只买入年化时间价值占行权价比例低于此阈值的CALL。年化时间价值% = (时间价值 / 行权价) × (365 / 持有天数) × 100%。默认10%，越低说明期权越"便宜"</li>
                            <li><Tag>最短到期月数</Tag> 期权到期日距今至少N个月（默认12个月=1年）。更长的到期日意味着更多的时间价值衰减缓冲</li>
                            <li><Tag>到期前N天平仓</Tag> 在期权到期前N天主动平仓（默认30天=1个月），避免最后阶段时间价值加速衰减（Theta加速）</li>
                            <li><Tag>买入数量</Tag> 每次买入的合约数量</li>
                            <li><Tag>扫描行权价数量</Tag> 在ATM附近扫描多少个行权价来寻找最优合约（默认15个）。扫描范围越大，越可能找到时间价值更低的合约</li>
                          </ul>
                          <p><strong>回测逻辑：</strong>每月开仓日，扫描所有到期日≥最短月数的CALL期权。对每个候选合约计算：时间价值 = 期权价格 - 内在价值（max(0, 现货-行权价)），年化时间价值% = (时间价值 / 行权价) × (365 / 剩余天数) × 100%。选择年化时间价值%最低且满足阈值的合约买入。持有至到期前N天平仓，然后滚动到下一个符合条件的长期合约。</p>
                          <p><strong>合约筛选优先级：</strong>1) 年化时间价值%最低优先；2) 深度实值（ITM）优先，因为内在价值占比高、时间价值占比低；3) 到期日更远优先，时间价值衰减更慢。</p>
                          <p><strong>适用场景：</strong>适合对标的资产长期看涨的投资者。在牛市中可以用较少资金获取接近现货的涨幅；在震荡市中时间价值损耗是主要成本；在熊市中最大亏损限于权利金。适合BTC/ETH等长期趋势向上的加密资产。</p>
                          <p><strong>风险提示：</strong>如果标的价格长期横盘或下跌，时间价值会持续损耗，导致亏损。深度虚值（OTM）CALL的时间价值占比高，不适合此策略。流动性较差的远期合约可能存在较大的买卖价差。</p>
                        </div>
                      ),
                    },
                  ]}
                />
              </div>
            )}
          </Card>

          <Card
            title={<Space><DatabaseOutlined style={{ color: '#52c41a' }} /><span>真实数据回测参数</span></Space>}
            extra={
              <Space>
                {savedStrategies.length > 0 && (
                  <Select
                    placeholder="加载已保存策略" style={{ width: 200 }} value={null}
                    onChange={handleLoadStrategy} suffixIcon={<FolderOpenOutlined />}
                    optionRender={(option) => (
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <span>{option.label}</span>
                        <Popconfirm title="确定删除？" onConfirm={(e) => { e.stopPropagation(); handleDeleteStrategy(option.value); }} onCancel={(e) => e.stopPropagation()}>
                          <DeleteOutlined style={{ color: '#ff4d4f', marginLeft: 8 }} onClick={(e) => e.stopPropagation()} />
                        </Popconfirm>
                      </div>
                    )}
                    options={savedStrategies.map((s) => ({ label: s.name, value: s.name }))}
                  />
                )}
                <Button icon={<SaveOutlined />} onClick={() => setSaveModalOpen(true)}>保存策略</Button>
              </Space>
            }
          >
            <Modal
              title="保存策略" open={saveModalOpen}
              onOk={handleSaveStrategy}
              onCancel={() => { setSaveModalOpen(false); setStrategyName(''); }}
              okText="保存" cancelText="取消"
            >
              <Form.Item label="策略名称" style={{ marginBottom: 0, marginTop: 16 }}>
                <Input value={strategyName} onChange={(e) => setStrategyName(e.target.value)} placeholder="输入策略名称" onPressEnter={handleSaveStrategy} />
              </Form.Item>
              {savedStrategies.some((s) => s.name === strategyName.trim()) && (
                <Text type="warning" style={{ fontSize: 12 }}>同名策略已存在，保存将覆盖</Text>
              )}
            </Modal>

            <Tabs
              activeKey={activeTab}
              onChange={(key) => { setActiveTab(key); setResult(null); }}
              items={[
                { key: 'basic', label: '基础策略' },
                { key: 'martingale', label: '马丁格尔策略' },
                { key: 'enhanced', label: '增强型马丁格尔' },
                { key: 'vol', label: '波动率策略' },
                { key: 'rollput', label: 'Roll PUT策略' },
                { key: 'hedgeput', label: 'Hedge PUT策略' },
                { key: 'channel', label: '进阶通道策略' },
                { key: 'wheel', label: '飞轮策略' },
                { key: 'grid', label: '网格策略' },
                { key: 'leaps', label: 'LEAPS长期期权' },
              ]}
              style={{ marginBottom: 16 }}
            />

            <Form
              form={form} layout="vertical" onFinish={handleRun}
              initialValues={{
                underlying: 'BTC', roll_day: 1, close_days_before: 1,
                initial_capital: 10000, contract_multiplier: 1,
                date_range: [dayjs('2024-01-01'), dayjs('2025-01-01')],
              }}
            >
              <Row gutter={16}>
                <Col xs={24} sm={8}>
                  <Form.Item name="underlying" label="标的" rules={[{ required: true }]}>
                    <Select>
                      <Option value="BTC">BTC</Option>
                      <Option value="ETH">ETH</Option>
                    </Select>
                  </Form.Item>
                </Col>
                <Col xs={24} sm={8}>
                  <Form.Item name="date_range" label="回测时间段" rules={[{ required: true, message: '请选择时间段' }]}>
                    <RangePicker style={{ width: '100%' }} format="YYYY-MM-DD" />
                  </Form.Item>
                </Col>
                <Col xs={24} sm={8}>
                  <Form.Item name="initial_capital" label="初始资金 (USD)">
                    <InputNumber style={{ width: '100%' }} min={100} precision={0} />
                  </Form.Item>
                </Col>
              </Row>
              <Row gutter={16}>
                <Col xs={24} sm={6}>
                  <Form.Item name="roll_day" label="每月开仓日 (1-28)">
                    <InputNumber style={{ width: '100%' }} min={1} max={28} precision={0} />
                  </Form.Item>
                </Col>
                <Col xs={24} sm={6}>
                  <Form.Item name="close_days_before" label="到期前N天平仓">
                    <InputNumber style={{ width: '100%' }} min={0} max={10} precision={0} />
                  </Form.Item>
                </Col>
                <Col xs={24} sm={6}>
                  <Form.Item name="contract_multiplier" label="合约乘数">
                    <InputNumber style={{ width: '100%' }} min={0.001} max={100} step={0.01} precision={3} />
                  </Form.Item>
                </Col>
              </Row>

              {activeTab !== 'vol' && activeTab !== 'rollput' && activeTab !== 'hedgeput' && activeTab !== 'channel' && activeTab !== 'wheel' && activeTab !== 'grid' && <Divider orientation="left">策略腿配置</Divider>}

              {activeTab === 'vol' && (
                <Alert
                  message="波动率策略说明"
                  description={`当ATM隐含波动率 > ${volSellIv}% 时，卖出ATM PUT；当ATM IV < ${volBuyIv}% 时，买入ATM PUT；其余时间不操作。每月到期后自动判断下一周期。`}
                  type="info" showIcon style={{ marginBottom: 16 }}
                />
              )}

              {activeTab === 'vol' && (
                <Row gutter={16} style={{ marginBottom: 16 }}>
                  <Col xs={12} sm={6}>
                    <Space direction="vertical" size={4}>
                      <Text style={{ fontSize: 13 }}>卖出IV阈值:</Text>
                      <InputNumber
                        value={volSellIv}
                        onChange={(v) => setVolSellIv(v || 80)}
                        min={1} max={300} step={5} precision={0} style={{ width: 100 }}
                        formatter={(v) => v + '%'} parser={(v) => v.replace('%', '')}
                      />
                    </Space>
                  </Col>
                  <Col xs={12} sm={6}>
                    <Space direction="vertical" size={4}>
                      <Text style={{ fontSize: 13 }}>买入IV阈值:</Text>
                      <InputNumber
                        value={volBuyIv}
                        onChange={(v) => setVolBuyIv(v || 40)}
                        min={1} max={300} step={5} precision={0} style={{ width: 100 }}
                        formatter={(v) => v + '%'} parser={(v) => v.replace('%', '')}
                      />
                    </Space>
                  </Col>
                  <Col xs={12} sm={6}>
                    <Space direction="vertical" size={4}>
                      <Text style={{ fontSize: 13 }}>数量:</Text>
                      <InputNumber
                        value={volQuantity}
                        onChange={(v) => setVolQuantity(v || 1)}
                        min={0.01} max={100} step={0.1} precision={2} style={{ width: 100 }}
                      />
                    </Space>
                  </Col>
                  <Col xs={12} sm={6}>
                    <Space direction="vertical" size={4}>
                      <Text style={{ fontSize: 13 }}>行权价偏移%:</Text>
                      <InputNumber
                        value={volStrikeOffset}
                        onChange={(v) => setVolStrikeOffset(v ?? 0)}
                        min={-50} max={50} step={1} precision={0} style={{ width: 100 }}
                        formatter={(v) => v + '%'} parser={(v) => v.replace('%', '')}
                      />
                    </Space>
                  </Col>
                </Row>
              )}

              {activeTab === 'rollput' && (
                <Alert
                  message="Roll PUT 策略说明"
                  description={`每月${1}日在现货价格向下${rollPutOffset}%处卖出当月到期PUT，到期前平仓。平仓价>0时Roll到下月同Strike继续卖；平仓价=0（到期归零）则下月按新价格偏移重新开仓。`}
                  type="info" showIcon style={{ marginBottom: 16 }}
                />
              )}

              {activeTab === 'rollput' && (
                <Row gutter={16} style={{ marginBottom: 16 }}>
                  <Col xs={12} sm={6}>
                    <Space direction="vertical" size={4}>
                      <Text style={{ fontSize: 13 }}>行权价偏移(OTM):</Text>
                      <InputNumber
                        value={rollPutOffset}
                        onChange={(v) => setRollPutOffset(v || 10)}
                        min={1} max={50} step={1} precision={0} style={{ width: 100 }}
                        formatter={(v) => v + '%'} parser={(v) => v.replace('%', '')}
                      />
                    </Space>
                  </Col>
                  <Col xs={12} sm={6}>
                    <Space direction="vertical" size={4}>
                      <Text style={{ fontSize: 13 }}>卖出数量:</Text>
                      <InputNumber
                        value={rollPutQuantity}
                        onChange={(v) => setRollPutQuantity(v || 1)}
                        min={0.01} max={100} step={0.1} precision={2} style={{ width: 100 }}
                      />
                    </Space>
                  </Col>
                </Row>
              )}

              {activeTab === 'hedgeput' && (
                <Alert
                  message="Hedge PUT 策略说明"
                  description={`每月${1}日在现货价格向下${hedgePutOffset}%处卖出当月到期PUT，到期前平仓。亏损时Roll到下月同Strike；若现货跌幅超${hedgePutCrashPct}%则切换为买入PUT对冲（数量${hedgePutHedgeQty}），持续买入直到现货价格恢复到原始开仓水平，然后恢复正常卖PUT。`}
                  type="info" showIcon style={{ marginBottom: 16 }}
                />
              )}

              {activeTab === 'hedgeput' && (
                <Row gutter={16} style={{ marginBottom: 16 }}>
                  <Col xs={12} sm={6}>
                    <Space direction="vertical" size={4}>
                      <Text style={{ fontSize: 13 }}>行权价偏移(OTM):</Text>
                      <InputNumber
                        value={hedgePutOffset}
                        onChange={(v) => setHedgePutOffset(v || 10)}
                        min={1} max={50} step={1} precision={0} style={{ width: 100 }}
                        formatter={(v) => v + '%'} parser={(v) => v.replace('%', '')}
                      />
                    </Space>
                  </Col>
                  <Col xs={12} sm={6}>
                    <Space direction="vertical" size={4}>
                      <Text style={{ fontSize: 13 }}>卖出数量:</Text>
                      <InputNumber
                        value={hedgePutQuantity}
                        onChange={(v) => setHedgePutQuantity(v || 1)}
                        min={0.01} max={100} step={0.1} precision={2} style={{ width: 100 }}
                      />
                    </Space>
                  </Col>
                  <Col xs={12} sm={6}>
                    <Space direction="vertical" size={4}>
                      <Text style={{ fontSize: 13 }}>暴跌阈值:</Text>
                      <InputNumber
                        value={hedgePutCrashPct}
                        onChange={(v) => setHedgePutCrashPct(v || 20)}
                        min={5} max={80} step={5} precision={0} style={{ width: 100 }}
                        formatter={(v) => v + '%'} parser={(v) => v.replace('%', '')}
                      />
                    </Space>
                  </Col>
                  <Col xs={12} sm={6}>
                    <Space direction="vertical" size={4}>
                      <Text style={{ fontSize: 13 }}>对冲买入数量:</Text>
                      <InputNumber
                        value={hedgePutHedgeQty}
                        onChange={(v) => setHedgePutHedgeQty(v || 1)}
                        min={0.01} max={100} step={0.1} precision={2} style={{ width: 100 }}
                      />
                    </Space>
                  </Col>
                </Row>
              )}

              {activeTab === 'channel' && (
                <Alert
                  message="进阶通道策略说明"
                  description={`结合通道和飞轮策略：滚动计算过去${channelLookback}天的最高/最低价作为上下轨。初始在下轨卖PUT（数量${channelQuantity}），若PUT被行权则持有现货，转为在上轨卖CALL；若CALL被行权则回到卖PUT，形成通道飞轮循环。`}
                  type="info" showIcon style={{ marginBottom: 16 }}
                />
              )}

              {activeTab === 'channel' && (
                <Row gutter={16} style={{ marginBottom: 16 }}>
                  <Col xs={12} sm={8}>
                    <Space direction="vertical" size={4}>
                      <Text style={{ fontSize: 13 }}>回看天数:</Text>
                      <InputNumber
                        value={channelLookback}
                        onChange={(v) => setChannelLookback(v || 90)}
                        min={30} max={365} step={1} precision={0} style={{ width: 100 }}
                        suffix="天"
                      />
                    </Space>
                  </Col>
                  <Col xs={12} sm={8}>
                    <Space direction="vertical" size={4}>
                      <Text style={{ fontSize: 13 }}>每次卖出数量:</Text>
                      <InputNumber
                        value={channelQuantity}
                        onChange={(v) => setChannelQuantity(v || 1)}
                        min={0.01} max={100} step={0.1} precision={2} style={{ width: 100 }}
                      />
                    </Space>
                  </Col>
                </Row>
              )}

              {activeTab === 'wheel' && (
                <Alert
                  message="飞轮策略 (Wheel Strategy) 说明"
                  description={`经典期权飞轮策略：1) 卖出OTM PUT（行权价=现货-${wheelPutOffset}%），收取权利金；2) 若PUT到期被行权（价内），则"买入"标的，转为卖出OTM CALL（行权价=现货+${wheelCallOffset}%）；3) 若CALL到期被行权，则"卖出"标的，回到步骤1。每个阶段都持续收取权利金，形成飞轮效应。`}
                  type="info" showIcon style={{ marginBottom: 16 }}
                />
              )}

              {activeTab === 'wheel' && (
                <Row gutter={16} style={{ marginBottom: 16 }}>
                  <Col xs={12} sm={6}>
                    <Space direction="vertical" size={4}>
                      <Text style={{ fontSize: 13 }}>PUT行权价偏移(OTM):</Text>
                      <InputNumber
                        value={wheelPutOffset}
                        onChange={(v) => setWheelPutOffset(v || 5)}
                        min={1} max={50} step={1} precision={0} style={{ width: 100 }}
                        formatter={(v) => v + '%'} parser={(v) => v.replace('%', '')}
                      />
                    </Space>
                  </Col>
                  <Col xs={12} sm={6}>
                    <Space direction="vertical" size={4}>
                      <Text style={{ fontSize: 13 }}>CALL行权价偏移(OTM):</Text>
                      <InputNumber
                        value={wheelCallOffset}
                        onChange={(v) => setWheelCallOffset(v || 5)}
                        min={1} max={50} step={1} precision={0} style={{ width: 100 }}
                        formatter={(v) => v + '%'} parser={(v) => v.replace('%', '')}
                      />
                    </Space>
                  </Col>
                  <Col xs={12} sm={6}>
                    <Space direction="vertical" size={4}>
                      <Text style={{ fontSize: 13 }}>每次卖出数量:</Text>
                      <InputNumber
                        value={wheelQuantity}
                        onChange={(v) => setWheelQuantity(v || 1)}
                        min={0.01} max={100} step={0.1} precision={2} style={{ width: 100 }}
                      />
                    </Space>
                  </Col>
                </Row>
              )}

              {activeTab === 'grid' && (
                <Alert
                  message="网格策略 (Grid Strategy) 说明"
                  description={`在当前现货价格上下按${gridStep}USD间距生成网格，向上${gridRangeUp}层、向下${gridRangeDown}层。在每个年化收益率≥${gridMinYield}%的网格位卖出PUT（数量${gridQuantity}），总仓位上限${gridMaxPositions}个。PUT被行权后持有现货并在上一格卖CALL，其他网格继续卖PUT。`}
                  type="info" showIcon style={{ marginBottom: 16 }}
                />
              )}

              {activeTab === 'grid' && (
                <Row gutter={16} style={{ marginBottom: 16 }}>
                  <Col xs={12} sm={4}>
                    <Space direction="vertical" size={4}>
                      <Text style={{ fontSize: 13 }}>网格步长(USD):</Text>
                      <InputNumber
                        value={gridStep}
                        onChange={(v) => setGridStep(v || 1000)}
                        min={10} max={10000} step={100} precision={0} style={{ width: 110 }}
                      />
                    </Space>
                  </Col>
                  <Col xs={12} sm={4}>
                    <Space direction="vertical" size={4}>
                      <Text style={{ fontSize: 13 }}>每格数量:</Text>
                      <InputNumber
                        value={gridQuantity}
                        onChange={(v) => setGridQuantity(v || 1)}
                        min={0.01} max={100} step={0.1} precision={2} style={{ width: 100 }}
                      />
                    </Space>
                  </Col>
                  <Col xs={12} sm={4}>
                    <Space direction="vertical" size={4}>
                      <Text style={{ fontSize: 13 }}>最低年化收益率:</Text>
                      <InputNumber
                        value={gridMinYield}
                        onChange={(v) => setGridMinYield(v || 10)}
                        min={1} max={200} step={5} precision={0} style={{ width: 100 }}
                        formatter={(v) => v + '%'} parser={(v) => v.replace('%', '')}
                      />
                    </Space>
                  </Col>
                  <Col xs={12} sm={4}>
                    <Space direction="vertical" size={4}>
                      <Text style={{ fontSize: 13 }}>向上网格数:</Text>
                      <InputNumber
                        value={gridRangeUp}
                        onChange={(v) => setGridRangeUp(v || 5)}
                        min={0} max={20} step={1} precision={0} style={{ width: 80 }}
                      />
                    </Space>
                  </Col>
                  <Col xs={12} sm={4}>
                    <Space direction="vertical" size={4}>
                      <Text style={{ fontSize: 13 }}>向下网格数:</Text>
                      <InputNumber
                        value={gridRangeDown}
                        onChange={(v) => setGridRangeDown(v || 5)}
                        min={0} max={20} step={1} precision={0} style={{ width: 80 }}
                      />
                    </Space>
                  </Col>
                  <Col xs={12} sm={4}>
                    <Space direction="vertical" size={4}>
                      <Text style={{ fontSize: 13 }}>最大总仓位:</Text>
                      <InputNumber
                        value={gridMaxPositions}
                        onChange={(v) => setGridMaxPositions(v || 10)}
                        min={1} max={50} step={1} precision={0} style={{ width: 80 }}
                      />
                    </Space>
                  </Col>
                </Row>
              )}

              {activeTab === 'martingale' && (
                <Row gutter={16} style={{ marginBottom: 16 }}>
                  <Col xs={24} sm={8}>
                    <Space size={8} align="center">
                      <Text style={{ fontSize: 13 }}>最大翻倍次数:</Text>
                      <InputNumber
                        value={martingaleMaxDoubles}
                        onChange={(v) => setMartingaleMaxDoubles(v || 3)}
                        min={1} max={10} precision={0} style={{ width: 80 }}
                      />
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        (最大倍数: {Math.pow(2, martingaleMaxDoubles)}x)
                      </Text>
                    </Space>
                  </Col>
                </Row>
              )}

              {activeTab === 'leaps' && (
                <Alert
                  message="LEAPS长期期权策略说明"
                  description={`买入到期日≥${leapsMinMonths}个月的深度实值CALL期权，筛选年化时间价值%<${leapsMaxAnnualTvPct}%的合约。到期前${leapsCloseDaysBefore}天平仓，然后滚动买入下一个符合条件的长期合约。`}
                  type="info" showIcon style={{ marginBottom: 16 }}
                />
              )}

              {activeTab === 'leaps' && (
                <Row gutter={16} style={{ marginBottom: 16 }}>
                  <Col xs={12} sm={5}>
                    <Space direction="vertical" size={4}>
                      <Text style={{ fontSize: 13 }}>最大年化时间价值%:</Text>
                      <InputNumber
                        value={leapsMaxAnnualTvPct}
                        onChange={(v) => setLeapsMaxAnnualTvPct(v || 10)}
                        min={1} max={50} step={1} precision={0} style={{ width: 100 }}
                        formatter={(v) => v + '%'} parser={(v) => v.replace('%', '')}
                      />
                    </Space>
                  </Col>
                  <Col xs={12} sm={5}>
                    <Space direction="vertical" size={4}>
                      <Text style={{ fontSize: 13 }}>最短到期月数:</Text>
                      <InputNumber
                        value={leapsMinMonths}
                        onChange={(v) => setLeapsMinMonths(v || 12)}
                        min={6} max={36} step={1} precision={0} style={{ width: 100 }}
                      />
                    </Space>
                  </Col>
                  <Col xs={12} sm={5}>
                    <Space direction="vertical" size={4}>
                      <Text style={{ fontSize: 13 }}>到期前N天平仓:</Text>
                      <InputNumber
                        value={leapsCloseDaysBefore}
                        onChange={(v) => setLeapsCloseDaysBefore(v || 30)}
                        min={7} max={90} step={1} precision={0} style={{ width: 100 }}
                      />
                    </Space>
                  </Col>
                  <Col xs={12} sm={5}>
                    <Space direction="vertical" size={4}>
                      <Text style={{ fontSize: 13 }}>买入数量:</Text>
                      <InputNumber
                        value={leapsQuantity}
                        onChange={(v) => setLeapsQuantity(v || 1)}
                        min={0.01} max={100} step={0.1} precision={2} style={{ width: 100 }}
                      />
                    </Space>
                  </Col>
                  <Col xs={12} sm={4}>
                    <Space direction="vertical" size={4}>
                      <Text style={{ fontSize: 13 }}>扫描行权价数:</Text>
                      <InputNumber
                        value={leapsNumStrikes}
                        onChange={(v) => setLeapsNumStrikes(v || 15)}
                        min={5} max={30} step={1} precision={0} style={{ width: 80 }}
                      />
                    </Space>
                  </Col>
                </Row>
              )}

              {activeTab === 'enhanced' && (
                <Alert
                  message="增强型马丁格尔策略说明"
                  description="当月度周期亏损时，根据累计亏损额动态计算下一周期的仓位大小，使预期收益刚好覆盖累计亏损的指定比例。盈利后用盈利抵扣累计亏损，直到完全回本后恢复基础仓位。"
                  type="warning" showIcon style={{ marginBottom: 16 }}
                />
              )}

              {activeTab === 'enhanced' && (
                <Row gutter={16} style={{ marginBottom: 16 }}>
                  <Col xs={24} sm={8}>
                    <Space size={8} align="center">
                      <Text style={{ fontSize: 13 }}>目标回收比例:</Text>
                      <InputNumber
                        value={enhancedRecoverPct}
                        onChange={(v) => setEnhancedRecoverPct(v || 110)}
                        min={100} max={300} step={5} precision={0} style={{ width: 80 }}
                        formatter={(v) => v + '%'} parser={(v) => v.replace('%', '')}
                      />
                    </Space>
                  </Col>
                  <Col xs={24} sm={8}>
                    <Space size={8} align="center">
                      <Text style={{ fontSize: 13 }}>最大倍数限制:</Text>
                      <InputNumber
                        value={enhancedMaxMultiplier}
                        onChange={(v) => setEnhancedMaxMultiplier(v || 10)}
                        min={1} max={100} step={1} precision={1} style={{ width: 80 }}
                      />
                      <Text type="secondary" style={{ fontSize: 12 }}>x</Text>
                    </Space>
                  </Col>
                </Row>
              )}

              {activeTab !== 'vol' && activeTab !== 'rollput' && activeTab !== 'hedgeput' && activeTab !== 'channel' && activeTab !== 'wheel' && activeTab !== 'grid' && activeTab !== 'leaps' && legs.map((leg, idx) => (
                <Row key={leg.key} gutter={12} align="middle" style={{ marginBottom: 8 }}>
                  <Col><Tag color="blue">腿 {idx + 1}</Tag></Col>
                  <Col>
                    <Select value={leg.option_type} onChange={(v) => updateLeg(leg.key, 'option_type', v)} style={{ width: 90 }} size="small">
                      <Option value="PUT">PUT</Option>
                      <Option value="CALL">CALL</Option>
                    </Select>
                  </Col>
                  <Col>
                    <Space size={4}>
                      <Text style={{ fontSize: 12, color: '#888' }}>行权价偏移%:</Text>
                      <InputNumber
                        value={leg.strike_offset_pct * 100}
                        onChange={(v) => updateLeg(leg.key, 'strike_offset_pct', (v || 0) / 100)}
                        size="small" style={{ width: 80 }} step={1} precision={0}
                        formatter={(v) => v + '%'} parser={(v) => v.replace('%', '')}
                      />
                    </Space>
                  </Col>
                  <Col>
                    <Space size={4}>
                      <Text style={{ fontSize: 12, color: '#888' }}>数量:</Text>
                      <InputNumber value={leg.quantity} onChange={(v) => updateLeg(leg.key, 'quantity', v)} size="small" style={{ width: 80 }} step={1} />
                    </Space>
                  </Col>
                  <Col>
                    <Space size={4}>
                      <Text style={{ fontSize: 12, color: '#888' }}>滚动周期(月):</Text>
                      <InputNumber value={leg.expiry_months} onChange={(v) => updateLeg(leg.key, 'expiry_months', v)} size="small" style={{ width: 60 }} min={1} max={12} />
                    </Space>
                  </Col>
                  <Col>
                    <Button type="link" danger size="small" icon={<DeleteOutlined />} onClick={() => removeLeg(leg.key)} disabled={legs.length <= 1} />
                  </Col>
                </Row>
              ))}

              {activeTab !== 'vol' && activeTab !== 'rollput' && activeTab !== 'hedgeput' && activeTab !== 'channel' && activeTab !== 'wheel' && activeTab !== 'grid' && activeTab !== 'leaps' && (
                <Button type="dashed" onClick={addLeg} icon={<PlusOutlined />} size="small" style={{ marginBottom: 16 }}>
                  添加策略腿
                </Button>
              )}

              <Form.Item>
                <Button type="primary" htmlType="submit" loading={loading} icon={<DatabaseOutlined />} size="large" block
                  style={{ background: activeTab === 'leaps' ? '#1890ff' : activeTab === 'grid' ? '#2f54eb' : activeTab === 'wheel' ? '#fa541c' : activeTab === 'vol' ? '#722ed1' : activeTab === 'rollput' ? '#13c2c2' : activeTab === 'hedgeput' ? '#eb2f96' : activeTab !== 'basic' ? '#faad14' : '#52c41a', borderColor: activeTab === 'leaps' ? '#1890ff' : activeTab === 'grid' ? '#2f54eb' : activeTab === 'wheel' ? '#fa541c' : activeTab === 'vol' ? '#722ed1' : activeTab === 'rollput' ? '#13c2c2' : activeTab === 'hedgeput' ? '#eb2f96' : activeTab !== 'basic' ? '#faad14' : '#52c41a' }}>
                  {activeTab === 'leaps' ? '开始LEAPS长期期权回测' : activeTab === 'grid' ? '开始网格策略回测' : activeTab === 'wheel' ? '开始飞轮策略回测' : activeTab === 'hedgeput' ? '开始Hedge PUT策略回测' : activeTab === 'rollput' ? '开始Roll PUT策略回测' : activeTab === 'vol' ? '开始波动率策略回测' : activeTab === 'enhanced' ? '开始增强型马丁格尔回测' : activeTab === 'martingale' ? '开始马丁格尔回测' : '开始真实数据回测'}
                </Button>
              </Form.Item>
            </Form>
          </Card>

          {loading && (
            <Card style={{ marginTop: 16, textAlign: 'center', padding: '24px 0' }}>
              <Spin tip="正在获取Deribit真实数据并回测..." size="large" />
              {progress && (
                <div style={{ marginTop: 16, maxWidth: 400, margin: '16px auto 0' }}>
                  <Progress percent={progress.pct} status="active" strokeColor="#52c41a" />
                  <Text type="secondary" style={{ fontSize: 13 }}>
                    当前回测到: {progress.date}
                  </Text>
                </div>
              )}
            </Card>
          )}

          {result && (
            <>
              <Card title="回测摘要" style={{ marginTop: 16 }}>
                <Row gutter={[24, 16]}>
                  <Col xs={12} sm={6}>
                    <Statistic title="初始资金" value={result.summary.initial_capital} prefix="$" precision={0} />
                  </Col>
                  <Col xs={12} sm={6}>
                    <Statistic title="最终权益" value={result.summary.final_equity} prefix="$" precision={2}
                      valueStyle={{ color: result.summary.total_pnl >= 0 ? '#389e0d' : '#cf1322' }} />
                  </Col>
                  <Col xs={12} sm={6}>
                    <Statistic title="总收益" value={result.summary.total_pnl} prefix="$" precision={2}
                      valueStyle={{ color: result.summary.total_pnl >= 0 ? '#389e0d' : '#cf1322' }} />
                  </Col>
                  <Col xs={12} sm={6}>
                    <Statistic title="总收益率" value={result.summary.total_return_pct} suffix="%" precision={2}
                      valueStyle={{ color: result.summary.total_return_pct >= 0 ? '#389e0d' : '#cf1322' }} />
                  </Col>
                  <Col xs={12} sm={6}>
                    <Statistic title="最大回撤" value={result.summary.max_drawdown_pct} suffix="%" precision={2} valueStyle={{ color: '#cf1322' }} />
                  </Col>
                  <Col xs={12} sm={6}>
                    <Statistic title="总交易次数" value={result.summary.total_trades} />
                  </Col>
                  <Col xs={12} sm={6}>
                    <Statistic title="胜率" value={result.summary.win_rate_pct} suffix="%" precision={1} />
                  </Col>
                  <Col xs={12} sm={6}>
                    <Statistic title="真实数据占比" value={result.summary.real_data_pct} suffix="%"
                      precision={1} valueStyle={{ color: '#52c41a' }} />
                  </Col>
                  {(result.summary.martingale_enabled || result.summary.enhanced_martingale_enabled) && (
                    <Col xs={12} sm={6}>
                      <Statistic title="最大使用倍数" value={result.summary.max_multiplier_used + 'x'}
                        valueStyle={{ color: '#faad14' }} />
                    </Col>
                  )}
                  {(result.summary.martingale_enabled || result.summary.enhanced_martingale_enabled) && (
                    <Col xs={12} sm={6}>
                      <Statistic title="加仓次数" value={result.summary.martingale_doublings}
                        valueStyle={{ color: result.summary.martingale_doublings > 0 ? '#faad14' : '#389e0d' }} />
                    </Col>
                  )}
                  {result.summary.enhanced_martingale_enabled && (
                    <Col xs={12} sm={6}>
                      <Statistic title="剩余未回收亏损" value={result.summary.accumulated_loss_remaining} prefix="$" precision={2}
                        valueStyle={{ color: result.summary.accumulated_loss_remaining > 0 ? '#cf1322' : '#389e0d' }} />
                    </Col>
                  )}
                  {result.summary.vol_strategy_enabled && (
                    <Col xs={12} sm={6}>
                      <Statistic title="策略模式" value="波动率策略"
                        valueStyle={{ color: '#722ed1' }} />
                    </Col>
                  )}
                  {result.summary.roll_put_strategy_enabled && (
                    <Col xs={12} sm={6}>
                      <Statistic title="策略模式" value="Roll PUT"
                        valueStyle={{ color: '#13c2c2' }} />
                    </Col>
                  )}
                  {result.summary.roll_put_strategy_enabled && (
                    <>
                      <Col xs={12} sm={6}>
                        <Statistic title="正常开仓" value={result.summary.roll_put_fresh_opens || 0} suffix="次" />
                      </Col>
                      <Col xs={12} sm={6}>
                        <Statistic title="Roll续期" value={result.summary.roll_put_loss_rolls || 0} suffix="次"
                          valueStyle={{ color: '#faad14' }} />
                      </Col>
                    </>
                  )}
                  {result.summary.hedge_put_strategy_enabled && (
                    <Col xs={12} sm={6}>
                      <Statistic title="策略模式" value="Hedge PUT"
                        valueStyle={{ color: '#eb2f96' }} />
                    </Col>
                  )}
                  {result.summary.hedge_put_strategy_enabled && (
                    <>
                      <Col xs={12} sm={6}>
                        <Statistic title="正常开仓" value={result.summary.hedge_put_fresh_opens || 0} suffix="次" />
                      </Col>
                      <Col xs={12} sm={6}>
                        <Statistic title="亏损Roll" value={result.summary.hedge_put_loss_rolls || 0} suffix="次"
                          valueStyle={{ color: '#faad14' }} />
                      </Col>
                      <Col xs={12} sm={6}>
                        <Statistic title="暴跌对冲" value={result.summary.hedge_put_crash_hedges || 0} suffix="次"
                          valueStyle={{ color: '#cf1322' }} />
                      </Col>
                    </>
                  )}
                  {result.summary.channel_strategy_enabled && (
                    <>
                      <Col xs={12} sm={6}>
                        <Statistic title="策略模式" value="进阶通道策略"
                          valueStyle={{ color: '#722ed1' }} />
                      </Col>
                      <Col xs={12} sm={6}>
                        <Statistic title="完整周期" value={result.summary.channel_cycles || 0} suffix="次" />
                      </Col>
                      <Col xs={12} sm={6}>
                        <Statistic title="卖PUT" value={result.summary.channel_put_sells || 0} suffix="次" />
                      </Col>
                      <Col xs={12} sm={6}>
                        <Statistic title="卖CALL" value={result.summary.channel_call_sells || 0} suffix="次" />
                      </Col>
                      <Col xs={12} sm={6}>
                        <Statistic title="被行权" value={result.summary.channel_assignments || 0} suffix="次"
                          valueStyle={{ color: '#faad14' }} />
                      </Col>
                      <Col xs={12} sm={6}>
                        <Statistic title="总权利金" value={result.summary.channel_total_premium || 0} prefix="$" precision={2}
                          valueStyle={{ color: '#52c41a' }} />
                      </Col>
                      <Col xs={12} sm={6}>
                        <Statistic title="当前阶段" value={result.summary.channel_phase === 'sell_call' ? '卖CALL(持有标的)' : '卖PUT'} />
                      </Col>
                      {result.summary.channel_phase === 'sell_call' && result.summary.channel_assigned_spot && (
                        <Col xs={12} sm={6}>
                          <Statistic title="行权日现货价" value={result.summary.channel_assigned_spot} prefix="$" precision={0} />
                        </Col>
                      )}
                      {result.summary.channel_end_underlying_pnl !== 0 && result.summary.channel_end_underlying_pnl != null && (
                        <Col xs={12} sm={6}>
                          <Statistic title="标的未实现盈亏" value={result.summary.channel_end_underlying_pnl} prefix="$" precision={2}
                            valueStyle={{ color: result.summary.channel_end_underlying_pnl >= 0 ? '#52c41a' : '#ff4d4f' }} />
                        </Col>
                      )}
                    </>
                  )}
                  {result.summary.wheel_strategy_enabled && (
                    <>
                      <Col xs={12} sm={6}>
                        <Statistic title="策略模式" value="飞轮策略"
                          valueStyle={{ color: '#fa541c' }} />
                      </Col>
                      <Col xs={12} sm={6}>
                        <Statistic title="完整周期" value={result.summary.wheel_cycles || 0} suffix="次" />
                      </Col>
                      <Col xs={12} sm={6}>
                        <Statistic title="卖PUT" value={result.summary.wheel_put_sells || 0} suffix="次" />
                      </Col>
                      <Col xs={12} sm={6}>
                        <Statistic title="卖CALL" value={result.summary.wheel_call_sells || 0} suffix="次" />
                      </Col>
                      <Col xs={12} sm={6}>
                        <Statistic title="被行权" value={result.summary.wheel_assignments || 0} suffix="次"
                          valueStyle={{ color: '#faad14' }} />
                      </Col>
                      <Col xs={12} sm={6}>
                        <Statistic title="总权利金" value={result.summary.wheel_total_premium || 0} prefix="$" precision={2}
                          valueStyle={{ color: '#52c41a' }} />
                      </Col>
                      <Col xs={12} sm={6}>
                        <Statistic title="当前阶段" value={result.summary.wheel_phase === 'sell_call' ? '卖CALL(持有标的)' : '卖PUT'} />
                      </Col>
                      {result.summary.wheel_phase === 'sell_call' && result.summary.wheel_assigned_strike && (
                        <Col xs={12} sm={6}>
                          <Statistic title="PUT行权价" value={result.summary.wheel_assigned_strike} prefix="$" precision={0} />
                        </Col>
                      )}
                      {result.summary.wheel_phase === 'sell_call' && result.summary.wheel_assigned_spot && (
                        <Col xs={12} sm={6}>
                          <Statistic title="行权日现货价" value={result.summary.wheel_assigned_spot} prefix="$" precision={0} />
                        </Col>
                      )}
                      {result.summary.wheel_end_underlying_pnl !== 0 && result.summary.wheel_end_underlying_pnl != null && (
                        <Col xs={12} sm={6}>
                          <Statistic title="标的未实现盈亏" value={result.summary.wheel_end_underlying_pnl} prefix="$" precision={2}
                            valueStyle={{ color: result.summary.wheel_end_underlying_pnl >= 0 ? '#52c41a' : '#ff4d4f' }} />
                        </Col>
                      )}
                    </>
                  )}
                  {result.summary.grid_strategy_enabled && (
                    <>
                      <Col xs={12} sm={6}>
                        <Statistic title="策略模式" value="网格策略"
                          valueStyle={{ color: '#2f54eb' }} />
                      </Col>
                      <Col xs={12} sm={6}>
                        <Statistic title="完整周期" value={result.summary.grid_cycles || 0} suffix="次" />
                      </Col>
                      <Col xs={12} sm={6}>
                        <Statistic title="卖PUT" value={result.summary.grid_put_sells || 0} suffix="次" />
                      </Col>
                      <Col xs={12} sm={6}>
                        <Statistic title="卖CALL" value={result.summary.grid_call_sells || 0} suffix="次" />
                      </Col>
                      <Col xs={12} sm={6}>
                        <Statistic title="被行权" value={result.summary.grid_assignments || 0} suffix="次"
                          valueStyle={{ color: '#faad14' }} />
                      </Col>
                      <Col xs={12} sm={6}>
                        <Statistic title="总权利金" value={result.summary.grid_total_premium || 0} prefix="$" precision={2}
                          valueStyle={{ color: '#52c41a' }} />
                      </Col>
                      <Col xs={12} sm={6}>
                        <Statistic title="跳过(低收益)" value={result.summary.grid_skipped_low_yield || 0} suffix="次"
                          valueStyle={{ color: '#888' }} />
                      </Col>
                      <Col xs={12} sm={6}>
                        <Statistic title="活跃网格数" value={result.summary.grid_active_levels || 0} suffix="个"
                          valueStyle={{ color: '#2f54eb' }} />
                      </Col>
                      {result.summary.grid_end_underlying_pnl !== 0 && result.summary.grid_end_underlying_pnl != null && (
                        <Col xs={12} sm={6}>
                          <Statistic title="标的未实现盈亏" value={result.summary.grid_end_underlying_pnl} prefix="$" precision={2}
                            valueStyle={{ color: result.summary.grid_end_underlying_pnl >= 0 ? '#52c41a' : '#ff4d4f' }} />
                        </Col>
                      )}
                    </>
                  )}
                </Row>
              </Card>

              <Card title={<Space><LineChartOutlined /><span>资金曲线</span></Space>} style={{ marginTop: 16 }}>
                <ResponsiveContainer width="100%" height={400}>
                  <LineChart data={result.equity_curve}>
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="date" tick={{ fontSize: 11 }} interval="preserveStartEnd" tickFormatter={(v) => v.substring(5)} />
                    <YAxis yAxisId="equity" tick={{ fontSize: 11 }} domain={['auto', 'auto']} />
                    <YAxis yAxisId="spot" orientation="right" tick={{ fontSize: 11 }} domain={['auto', 'auto']} />
                    <Tooltip formatter={(value, name) => {
                      if (name === '权益' || name === '标的价格') return ['$' + value.toLocaleString(), name];
                      return [value, name];
                    }} />
                    <Legend />
                    <ReferenceLine yAxisId="equity" y={result.summary.initial_capital} stroke="#999" strokeDasharray="5 5" label="初始资金" />
                    <Line yAxisId="equity" type="monotone" dataKey="equity" name="权益" stroke="#52c41a" dot={false} strokeWidth={2} />
                    <Line yAxisId="spot" type="monotone" dataKey="spot" name="标的价格" stroke="#faad14" dot={false} strokeWidth={1} opacity={0.6} />
                  </LineChart>
                </ResponsiveContainer>
              </Card>

              {/* Wheel Strategy: Split Option PnL vs Underlying PnL Charts (累计曲线) */}
              {result.summary?.wheel_strategy_enabled && result.equity_curve?.some(e => e.wheel_underlying_unrealized != null) && (() => {
                // 期权端累计盈亏 = (equity - wheel_underlying_unrealized - wheel_underlying_realized) - initial_capital
                // 现货端累计盈亏 = wheel_underlying_realized + wheel_underlying_unrealized
                const initCap = result.summary.initial_capital;
                const wheelData = result.equity_curve.map(e => {
                  const realized = e.wheel_underlying_realized || 0;
                  const unrealized = e.wheel_underlying_unrealized || 0;
                  const optionCumPnl = Math.round((e.equity - unrealized - realized - initCap) * 100) / 100;
                  const underlyingCumPnl = Math.round((realized + unrealized) * 100) / 100;
                  return {
                    date: e.date,
                    spot: e.spot,
                    option_cum_pnl: optionCumPnl,
                    underlying_cum_pnl: underlyingCumPnl,
                    phase: e.wheel_phase,
                  };
                });
                return (
                  <Row gutter={16} style={{ marginTop: 16 }}>
                    <Col xs={24} lg={12}>
                      <Card title={<Space><LineChartOutlined style={{ color: '#1890ff' }} /><span>飞轮策略 - 期权端累计盈亏</span></Space>} size="small">
                        <ResponsiveContainer width="100%" height={300}>
                          <LineChart data={wheelData}>
                            <CartesianGrid strokeDasharray="3 3" />
                            <XAxis dataKey="date" tick={{ fontSize: 10 }} interval="preserveStartEnd" tickFormatter={(v) => v.substring(5)} />
                            <YAxis tick={{ fontSize: 10 }} domain={['auto', 'auto']} />
                            <Tooltip
                              formatter={(value, name) => ['$' + Number(value).toLocaleString(), name]}
                              labelFormatter={(v) => '日期: ' + v}
                            />
                            <Legend wrapperStyle={{ fontSize: 11 }} />
                            <ReferenceLine y={0} stroke="#999" strokeDasharray="3 3" />
                            <Line type="monotone" dataKey="option_cum_pnl" name="期权端累计盈亏" stroke="#1890ff" dot={false} strokeWidth={2} />
                          </LineChart>
                        </ResponsiveContainer>
                        <div style={{ textAlign: 'center', fontSize: 11, color: '#888', marginTop: 4 }}>
                          卖出期权收取的权利金累计盈亏（不含现货端盈亏）
                        </div>
                      </Card>
                    </Col>
                    <Col xs={24} lg={12}>
                      <Card title={<Space><LineChartOutlined style={{ color: '#fa541c' }} /><span>飞轮策略 - 现货端累计盈亏</span></Space>} size="small">
                        <ResponsiveContainer width="100%" height={300}>
                          <LineChart data={wheelData}>
                            <CartesianGrid strokeDasharray="3 3" />
                            <XAxis dataKey="date" tick={{ fontSize: 10 }} interval="preserveStartEnd" tickFormatter={(v) => v.substring(5)} />
                            <YAxis yAxisId="pnl" tick={{ fontSize: 10 }} domain={['auto', 'auto']} />
                            <YAxis yAxisId="spot" orientation="right" tick={{ fontSize: 10 }} domain={['auto', 'auto']} />
                            <Tooltip
                              formatter={(value, name) => {
                                if (name === '标的价格') return ['$' + Number(value).toLocaleString(), name];
                                return ['$' + Number(value).toLocaleString(), name];
                              }}
                              labelFormatter={(v) => '日期: ' + v}
                            />
                            <Legend wrapperStyle={{ fontSize: 11 }} />
                            <ReferenceLine yAxisId="pnl" y={0} stroke="#999" strokeDasharray="3 3" />
                            <Line yAxisId="pnl" type="monotone" dataKey="underlying_cum_pnl" name="现货端累计盈亏" stroke="#fa541c" dot={false} strokeWidth={2} />
                            <Line yAxisId="spot" type="monotone" dataKey="spot" name="标的价格" stroke="#faad14" dot={false} strokeWidth={1} opacity={0.5} />
                          </LineChart>
                        </ResponsiveContainer>
                        <div style={{ textAlign: 'center', fontSize: 11, color: '#888', marginTop: 4 }}>
                          现货端累计盈亏（含已实现 + 当前持仓浮动盈亏）
                        </div>
                      </Card>
                    </Col>
                  </Row>
                );
              })()}

              {/* Channel Strategy: Split Option PnL vs Underlying PnL Charts (累计曲线) */}
              {result.summary?.channel_strategy_enabled && result.equity_curve?.some(e => e.channel_underlying_unrealized != null) && (() => {
                const initCap = result.summary.initial_capital;
                const channelData = result.equity_curve.map(e => {
                  const realized = e.channel_underlying_realized || 0;
                  const unrealized = e.channel_underlying_unrealized || 0;
                  const optionCumPnl = Math.round((e.equity - unrealized - realized - initCap) * 100) / 100;
                  const underlyingCumPnl = Math.round((realized + unrealized) * 100) / 100;
                  return {
                    date: e.date,
                    spot: e.spot,
                    option_cum_pnl: optionCumPnl,
                    underlying_cum_pnl: underlyingCumPnl,
                    phase: e.channel_phase,
                  };
                });
                return (
                  <Row gutter={16} style={{ marginTop: 16 }}>
                    <Col xs={24} lg={12}>
                      <Card title={<Space><LineChartOutlined style={{ color: '#722ed1' }} /><span>通道策略 - 期权端累计盈亏</span></Space>} size="small">
                        <ResponsiveContainer width="100%" height={300}>
                          <LineChart data={channelData}>
                            <CartesianGrid strokeDasharray="3 3" />
                            <XAxis dataKey="date" tick={{ fontSize: 10 }} interval="preserveStartEnd" tickFormatter={(v) => v.substring(5)} />
                            <YAxis tick={{ fontSize: 10 }} domain={['auto', 'auto']} />
                            <Tooltip
                              formatter={(value, name) => ['$' + Number(value).toLocaleString(), name]}
                              labelFormatter={(v) => '日期: ' + v}
                            />
                            <Legend wrapperStyle={{ fontSize: 11 }} />
                            <ReferenceLine y={0} stroke="#999" strokeDasharray="3 3" />
                            <Line type="monotone" dataKey="option_cum_pnl" name="期权端累计盈亏" stroke="#722ed1" dot={false} strokeWidth={2} />
                          </LineChart>
                        </ResponsiveContainer>
                        <div style={{ textAlign: 'center', fontSize: 11, color: '#888', marginTop: 4 }}>
                          卖出期权收取的权利金累计盈亏（不含现货端盈亏）
                        </div>
                      </Card>
                    </Col>
                    <Col xs={24} lg={12}>
                      <Card title={<Space><LineChartOutlined style={{ color: '#fa541c' }} /><span>通道策略 - 现货端累计盈亏</span></Space>} size="small">
                        <ResponsiveContainer width="100%" height={300}>
                          <LineChart data={channelData}>
                            <CartesianGrid strokeDasharray="3 3" />
                            <XAxis dataKey="date" tick={{ fontSize: 10 }} interval="preserveStartEnd" tickFormatter={(v) => v.substring(5)} />
                            <YAxis yAxisId="pnl" tick={{ fontSize: 10 }} domain={['auto', 'auto']} />
                            <YAxis yAxisId="spot" orientation="right" tick={{ fontSize: 10 }} domain={['auto', 'auto']} />
                            <Tooltip
                              formatter={(value, name) => {
                                if (name === '标的价格') return ['$' + Number(value).toLocaleString(), name];
                                return ['$' + Number(value).toLocaleString(), name];
                              }}
                              labelFormatter={(v) => '日期: ' + v}
                            />
                            <Legend wrapperStyle={{ fontSize: 11 }} />
                            <ReferenceLine yAxisId="pnl" y={0} stroke="#999" strokeDasharray="3 3" />
                            <Line yAxisId="pnl" type="monotone" dataKey="underlying_cum_pnl" name="现货端累计盈亏" stroke="#fa541c" dot={false} strokeWidth={2} />
                            <Line yAxisId="spot" type="monotone" dataKey="spot" name="标的价格" stroke="#faad14" dot={false} strokeWidth={1} opacity={0.5} />
                          </LineChart>
                        </ResponsiveContainer>
                        <div style={{ textAlign: 'center', fontSize: 11, color: '#888', marginTop: 4 }}>
                          现货端累计盈亏（含已实现 + 当前持仓浮动盈亏）
                        </div>
                      </Card>
                    </Col>
                  </Row>
                );
              })()}

              {/* Grid Strategy: Split Option PnL vs Underlying PnL Charts (累计曲线) */}
              {result.summary?.grid_strategy_enabled && result.equity_curve?.some(e => e.grid_underlying_unrealized != null) && (() => {
                const initCap = result.summary.initial_capital;
                const gridData = result.equity_curve.map(e => {
                  const realized = e.grid_underlying_realized || 0;
                  const unrealized = e.grid_underlying_unrealized || 0;
                  const optionCumPnl = Math.round((e.equity - unrealized - realized - initCap) * 100) / 100;
                  const underlyingCumPnl = Math.round((realized + unrealized) * 100) / 100;
                  return {
                    date: e.date,
                    spot: e.spot,
                    option_cum_pnl: optionCumPnl,
                    underlying_cum_pnl: underlyingCumPnl,
                  };
                });
                return (
                  <Row gutter={16} style={{ marginTop: 16 }}>
                    <Col xs={24} lg={12}>
                      <Card title={<Space><LineChartOutlined style={{ color: '#2f54eb' }} /><span>网格策略 - 期权端累计盈亏</span></Space>} size="small">
                        <ResponsiveContainer width="100%" height={300}>
                          <LineChart data={gridData}>
                            <CartesianGrid strokeDasharray="3 3" />
                            <XAxis dataKey="date" tick={{ fontSize: 10 }} interval="preserveStartEnd" tickFormatter={(v) => v.substring(5)} />
                            <YAxis tick={{ fontSize: 10 }} domain={['auto', 'auto']} />
                            <Tooltip
                              formatter={(value, name) => ['$' + Number(value).toLocaleString(), name]}
                              labelFormatter={(v) => '日期: ' + v}
                            />
                            <Legend wrapperStyle={{ fontSize: 11 }} />
                            <ReferenceLine y={0} stroke="#999" strokeDasharray="3 3" />
                            <Line type="monotone" dataKey="option_cum_pnl" name="期权端累计盈亏" stroke="#2f54eb" dot={false} strokeWidth={2} />
                          </LineChart>
                        </ResponsiveContainer>
                        <div style={{ textAlign: 'center', fontSize: 11, color: '#888', marginTop: 4 }}>
                          卖出期权收取的权利金累计盈亏（不含现货端盈亏）
                        </div>
                      </Card>
                    </Col>
                    <Col xs={24} lg={12}>
                      <Card title={<Space><LineChartOutlined style={{ color: '#fa541c' }} /><span>网格策略 - 现货端累计盈亏</span></Space>} size="small">
                        <ResponsiveContainer width="100%" height={300}>
                          <LineChart data={gridData}>
                            <CartesianGrid strokeDasharray="3 3" />
                            <XAxis dataKey="date" tick={{ fontSize: 10 }} interval="preserveStartEnd" tickFormatter={(v) => v.substring(5)} />
                            <YAxis yAxisId="pnl" tick={{ fontSize: 10 }} domain={['auto', 'auto']} />
                            <YAxis yAxisId="spot" orientation="right" tick={{ fontSize: 10 }} domain={['auto', 'auto']} />
                            <Tooltip
                              formatter={(value, name) => {
                                if (name === '标的价格') return ['$' + Number(value).toLocaleString(), name];
                                return ['$' + Number(value).toLocaleString(), name];
                              }}
                              labelFormatter={(v) => '日期: ' + v}
                            />
                            <Legend wrapperStyle={{ fontSize: 11 }} />
                            <ReferenceLine yAxisId="pnl" y={0} stroke="#999" strokeDasharray="3 3" />
                            <Line yAxisId="pnl" type="monotone" dataKey="underlying_cum_pnl" name="现货端累计盈亏" stroke="#fa541c" dot={false} strokeWidth={2} />
                            <Line yAxisId="spot" type="monotone" dataKey="spot" name="标的价格" stroke="#faad14" dot={false} strokeWidth={1} opacity={0.5} />
                          </LineChart>
                        </ResponsiveContainer>
                        <div style={{ textAlign: 'center', fontSize: 11, color: '#888', marginTop: 4 }}>
                          现货端累计盈亏（含已实现 + 当前持仓浮动盈亏）
                        </div>
                      </Card>
                    </Col>
                  </Row>
                );
              })()}

              {/* Monthly Combo PnL Bar Chart */}
              {result.trades && result.trades.length > 0 && (() => {
                // Group trades by open_date month, sum PnL per month
                const monthlyMap = {};
                result.trades.forEach(t => {
                  const m = t.close_date.substring(0, 7); // group by close month
                  if (!monthlyMap[m]) monthlyMap[m] = 0;
                  monthlyMap[m] += t.pnl;
                });
                const monthlyData = Object.entries(monthlyMap)
                  .sort(([a], [b]) => a.localeCompare(b))
                  .map(([month, pnl]) => ({ month, pnl: Math.round(pnl * 100) / 100 }));
                return (
                  <Card title={<Space><BarChartOutlined /><span>每月组合盈亏</span></Space>} style={{ marginTop: 16 }}>
                    <ResponsiveContainer width="100%" height={300}>
                      <BarChart data={monthlyData} margin={{ top: 5, right: 30, left: 10, bottom: 5 }}>
                        <CartesianGrid strokeDasharray="3 3" />
                        <XAxis dataKey="month" tick={{ fontSize: 11 }} tickFormatter={(v) => v.substring(2)} />
                        <YAxis tick={{ fontSize: 11 }} />
                        <Tooltip formatter={(value) => ['$' + value.toFixed(2), '盈亏']} />
                        <ReferenceLine y={0} stroke="#999" />
                        <Bar dataKey="pnl" name="月度盈亏">
                          {monthlyData.map((entry, idx) => (
                            <Cell key={idx} fill={entry.pnl >= 0 ? '#52c41a' : '#ff4d4f'} />
                          ))}
                        </Bar>
                      </BarChart>
                    </ResponsiveContainer>
                  </Card>
                );
              })()}

              {/* IV Smile section */}
              <Card
                title={<Space><LineChartOutlined style={{ color: '#722ed1' }} /><span>IV 微笑曲线</span></Space>}
                style={{ marginTop: 16 }}
                extra={
                  selectedSmile ? (
                    <Space>
                      <Tag color="purple">{selectedSmile.instrument}</Tag>
                      <Tag>{selectedSmile.open_date} → {selectedSmile.close_date}</Tag>
                      <Button size="small" onClick={() => setSelectedSmileIdx(null)}>关闭</Button>
                    </Space>
                  ) : (
                    <Text type="secondary" style={{ fontSize: 12 }}>在交易记录中点击「查看」查看单笔交易的IV曲线</Text>
                  )
                }
              >
                {selectedSmile ? (
                  <div>
                    <Row gutter={16} style={{ marginBottom: 12 }}>
                      <Col span={6}><Text type="secondary">合约:</Text> <Text strong>{selectedSmile.instrument}</Text></Col>
                      <Col span={4}><Text type="secondary">行权价:</Text> <Text strong>${selectedSmile.strike?.toLocaleString()}</Text></Col>
                      <Col span={4}><Text type="secondary">到期日:</Text> <Text strong>{selectedSmile.expiry}</Text></Col>
                      <Col span={5}><Text type="secondary">开仓Spot:</Text> <Text strong>${selectedSmile.open_spot?.toLocaleString()}</Text></Col>
                      <Col span={5}><Text type="secondary">平仓Spot:</Text> <Text strong>${selectedSmile.close_spot?.toLocaleString()}</Text></Col>
                    </Row>
                    <IVSmileChart smileData={[selectedSmile]} />
                  </div>
                ) : (
                  // Show all smiles overview
                  result.iv_smiles && result.iv_smiles.length > 0 ? (
                    <IVSmileChart smileData={result.iv_smiles.filter(s =>
                      (s.open_smile && s.open_smile.length > 0) || (s.close_smile && s.close_smile.length > 0)
                    ).slice(0, 5)} />
                  ) : (
                    <Empty description="无IV微笑曲线数据" />
                  )
                )}
              </Card>

              <Card title="交易记录" style={{ marginTop: 16 }}>
                <Table
                  columns={tradeColumns}
                  dataSource={result.trades.map((t, i) => ({ ...t, key: i }))}
                  size="small"
                  pagination={{ pageSize: 20, showSizeChanger: true, showTotal: (t) => '共 ' + t + ' 条' }}
                  scroll={{ x: 1500 }}
                />
              </Card>
            </>
          )}
        </div>
      </Content>

      <Footer style={{ textAlign: 'center', background: '#001529', color: 'white' }}>
        期权风险监控系统 ©2024 | 真实数据回测 (Deribit)
      </Footer>
    </Layout>
  );
}
