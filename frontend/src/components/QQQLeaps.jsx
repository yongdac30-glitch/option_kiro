/**
 * QQQ LEAPS 逢跌买入策略回测页面
 *
 * 策略：QQQ单日跌幅≥阈值时，买入Delta≈0.60的24个月CALL，阶梯止盈+超时强平
 */
import { useState, useRef, useCallback } from 'react';
import {
  Layout, Card, Row, Col, Button, Select, DatePicker, InputNumber,
  Table, Space, Tag, message, Progress, Typography, Statistic,
  Descriptions, Tabs, Divider, Slider, Switch, Tooltip as ATooltip,
} from 'antd';
import {
  RocketOutlined, HomeOutlined, PlayCircleOutlined, PauseCircleOutlined,
  ArrowUpOutlined, ArrowDownOutlined, LineChartOutlined,
  InfoCircleOutlined, BarChartOutlined,
} from '@ant-design/icons';
import { Link } from 'react-router-dom';
import dayjs from 'dayjs';
import {
  XAxis, YAxis, CartesianGrid, Tooltip as RTooltip,
  Legend, ResponsiveContainer, Area, ComposedChart, Line, ReferenceLine,
} from 'recharts';
import { qqqLeapsService } from '../services/qqqLeapsService';

const { Content } = Layout;
const { Title, Text, Paragraph } = Typography;
const { RangePicker } = DatePicker;

const TICKERS = [
  { value: 'QQQ', label: 'QQQ (纳斯达克100)' },
  { value: 'SPY', label: 'SPY (标普500)' },
  { value: 'XLK', label: 'XLK (科技板块)' },
  { value: 'IWM', label: 'IWM (罗素2000)' },
  { value: 'DIA', label: 'DIA (道琼斯)' },
  { value: 'BTC', label: 'BTC (比特币)' },
  { value: 'ETH', label: 'ETH (以太坊)' },
];

const CRYPTO_TICKERS = new Set(['BTC', 'ETH']);

export default function QQQLeaps() {
  const [ticker, setTicker] = useState('QQQ');
  const [dateRange, setDateRange] = useState([dayjs().subtract(3, 'year'), dayjs()]);
  const [initialCapital, setInitialCapital] = useState(50000);
  const [targetDelta, setTargetDelta] = useState(0.60);
  const [dipThreshold, setDipThreshold] = useState(1.0);
  const [expiryMonths, setExpiryMonths] = useState(24);
  const [numContracts, setNumContracts] = useState(1);
  const [tpPct1, setTpPct1] = useState(50);
  const [tpPct2, setTpPct2] = useState(30);
  const [tpPct3, setTpPct3] = useState(10);
  const [maxHoldMonths, setMaxHoldMonths] = useState(9);
  const [maxPositions, setMaxPositions] = useState(5);

  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState(null);
  const [result, setResult] = useState(null);
  const [showMtm, setShowMtm] = useState(true);
  const controllerRef = useRef(null);

  const handleRun = useCallback(() => {
    if (!dateRange || dateRange.length < 2) { message.warning('请选择时间范围'); return; }
    setRunning(true);
    setProgress({ pct: 0, status: '准备中...' });
    setResult(null);

    const isCrypto = CRYPTO_TICKERS.has(ticker);
    const params = {
      ticker,
      start_date: dateRange[0].format('YYYY-MM-DD'),
      end_date: dateRange[1].format('YYYY-MM-DD'),
      initial_capital: initialCapital,
      target_delta: targetDelta,
      dip_threshold: dipThreshold,
      expiry_months: expiryMonths,
      num_contracts: numContracts,
      tp_pct_1: tpPct1,
      tp_pct_2: tpPct2,
      tp_pct_3: tpPct3,
      max_hold_months: maxHoldMonths,
      max_positions: maxPositions,
      compare_tickers: isCrypto ? ['BTC', 'ETH'] : ['QQQ', 'SPY', 'XLK'],
    };

    const ctrl = qqqLeapsService.backtestStream(
      params,
      (prog) => setProgress(prog),
      (data) => { setResult(data); setRunning(false); setProgress(null); controllerRef.current = null; },
      (err) => { message.error(err); setRunning(false); setProgress(null); controllerRef.current = null; },
    );
    controllerRef.current = ctrl;
  }, [ticker, dateRange, initialCapital, targetDelta, dipThreshold,
      expiryMonths, numContracts, tpPct1, tpPct2, tpPct3, maxHoldMonths, maxPositions]);

  const handleStop = () => {
    if (controllerRef.current) { controllerRef.current.abort(); controllerRef.current = null; }
    setRunning(false); setProgress(null);
    message.info('已停止');
  };

  const summary = result?.summary;
  const trades = result?.trades || [];
  const curve = result?.equity_curve || [];
  const compare = result?.compare || {};

  const tradeColumns = [
    { title: '日期', dataIndex: 'date', width: 100, fixed: 'left' },
    { title: '操作', dataIndex: 'action', width: 100,
      render: (v) => {
        const colors = { OPEN: 'green', CLOSE: 'red', TAKE_PROFIT: 'gold', FORCE_CLOSE: 'orange', SKIP: 'default', MTM: 'blue' };
        const labels = { OPEN: '开仓', CLOSE: '平仓', TAKE_PROFIT: '止盈', FORCE_CLOSE: '强平', SKIP: '跳过', MTM: '盯市' };
        return <Tag color={colors[v]}>{labels[v] || v}</Tag>;
      },
    },
    { title: '行权价', dataIndex: 'strike', width: 100, render: (v) => `$${v?.toLocaleString()}` },
    { title: '到期日', dataIndex: 'expiry', width: 100 },
    { title: '现货', dataIndex: 'spot', width: 100, render: (v) => `$${v?.toLocaleString()}` },
    { title: '期权价', dataIndex: 'option_price', width: 100, render: (v) => `$${v?.toFixed(2)}` },
    { title: 'Delta', dataIndex: 'delta', width: 70, render: (v) => v?.toFixed(3) },
    { title: '数量', dataIndex: 'quantity', width: 60 },
    { title: '现金流', dataIndex: 'cash_flow', width: 110,
      render: (v) => <Text type={v >= 0 ? 'success' : 'danger'}>${v?.toLocaleString()}</Text> },
    { title: 'PnL', dataIndex: 'pnl', width: 100,
      render: (v) => v ? <Text type={v >= 0 ? 'success' : 'danger'}>${v?.toFixed(2)}</Text> : '-' },
    { title: 'PnL%', dataIndex: 'pnl_pct', width: 80,
      render: (v) => v ? <Text type={v >= 0 ? 'success' : 'danger'}>{v?.toFixed(1)}%</Text> : '-' },
    { title: '持仓月', dataIndex: 'months_held', width: 70, render: (v) => v ? `${v}月` : '-' },
    { title: '资金利用率', dataIndex: 'capital_usage_pct', width: 90,
      render: (v) => v != null ? <Text style={{ color: v > 80 ? '#cf1322' : v > 50 ? '#faad14' : '#3f8600' }}>{v}%</Text> : '-' },
    { title: '说明', dataIndex: 'note', width: 320,
      ellipsis: { showTitle: false },
      render: (v) => v ? <ATooltip title={v} placement="topLeft"><span>{v}</span></ATooltip> : '-' },
  ];

  return (
    <Layout style={{ minHeight: '100vh', background: '#f0f2f5' }}>
      <div style={{ background: '#001529', padding: '0 24px', display: 'flex', alignItems: 'center', height: 64 }}>
        <RocketOutlined style={{ fontSize: 24, color: '#eb2f96', marginRight: 12 }} />
        <Title level={3} style={{ color: 'white', margin: 0, fontSize: 20 }}>LEAPS 逢跌买入策略</Title>
        <div style={{ flex: 1 }} />
        <Link to="/" style={{ color: '#ffffffb3', display: 'flex', alignItems: 'center', gap: 6 }}>
          <HomeOutlined /> 返回主页
        </Link>
      </div>

      <Content style={{ padding: 24 }}>
        <Card size="small" style={{ marginBottom: 16, background: '#f6ffed', borderColor: '#b7eb8f' }}>
          <Text strong><InfoCircleOutlined /> 策略说明</Text>
          <Paragraph style={{ margin: '8px 0 0', fontSize: 13 }}>
            <ul style={{ margin: 0, paddingLeft: 20 }}>
              <li>开仓条件：标的单日跌幅 ≥ 阈值（默认1%）时触发</li>
              <li>开仓合约：买入到期约24个月的CALL，Delta最接近0.60</li>
              <li>阶梯止盈：0-4月 +50%，4-6月 +30%，6-9月 +10%</li>
              <li>超时强平：持仓超过9个月未止盈则强制平仓</li>
              <li>支持同时持有多笔仓位，每次跌幅触发独立开仓</li>
              <li>支持美股ETF（QQQ/SPY等，数据源Yahoo）和加密货币（BTC/ETH，数据源OKX）</li>
            </ul>
          </Paragraph>
        </Card>

        <Card title="回测参数" style={{ marginBottom: 16 }}>
          <Row gutter={[16, 12]}>
            <Col span={4}>
              <Text type="secondary" style={{ fontSize: 12 }}>标的</Text>
              <Select value={ticker} onChange={setTicker} style={{ width: '100%' }} options={TICKERS} />
            </Col>
            <Col span={6}>
              <Text type="secondary" style={{ fontSize: 12 }}>时间范围</Text>
              <RangePicker value={dateRange} onChange={setDateRange} style={{ width: '100%' }} />
            </Col>
            <Col span={3}>
              <Text type="secondary" style={{ fontSize: 12 }}>初始资金($)</Text>
              <InputNumber value={initialCapital} onChange={setInitialCapital}
                min={1000} step={10000} style={{ width: '100%' }}
                formatter={(v) => `${v}`.replace(/\B(?=(\d{3})+(?!\d))/g, ',')} />
            </Col>
            <Col span={3}>
              <Text type="secondary" style={{ fontSize: 12 }}>跌幅阈值(%)</Text>
              <InputNumber value={dipThreshold} onChange={setDipThreshold}
                min={0.5} max={10} step={0.5} style={{ width: '100%' }}
                formatter={(v) => `${v}%`} parser={(v) => v.replace('%', '')} />
            </Col>
            <Col span={3}>
              <Text type="secondary" style={{ fontSize: 12 }}>目标Delta</Text>
              <Slider value={targetDelta} onChange={setTargetDelta}
                min={0.30} max={0.90} step={0.05}
                marks={{ 0.50: '0.50', 0.60: '0.60', 0.70: '0.70' }} />
            </Col>
            <Col span={2}>
              <Text type="secondary" style={{ fontSize: 12 }}>IV模式</Text>
              <div style={{ marginTop: 4 }}>
                <Tag color="purple">动态30日</Tag>
              </div>
            </Col>
            <Col span={3}>
              <Space style={{ marginTop: 18 }}>
                <Button type="primary" icon={<PlayCircleOutlined />}
                  loading={running} onClick={handleRun}>回测</Button>
                {running && <Button danger icon={<PauseCircleOutlined />} onClick={handleStop}>停止</Button>}
              </Space>
            </Col>
          </Row>
          <Divider style={{ margin: '12px 0' }} />
          <Row gutter={[16, 12]}>
            <Col span={3}>
              <Text type="secondary" style={{ fontSize: 12 }}>到期月数</Text>
              <InputNumber value={expiryMonths} onChange={setExpiryMonths}
                min={6} max={36} step={3} style={{ width: '100%' }} />
            </Col>
            <Col span={2}>
              <Text type="secondary" style={{ fontSize: 12 }}>合约数</Text>
              <InputNumber value={numContracts} onChange={setNumContracts}
                min={1} max={100} style={{ width: '100%' }} />
            </Col>
            <Col span={3}>
              <Text type="secondary" style={{ fontSize: 12 }}>0-4月止盈%</Text>
              <InputNumber value={tpPct1} onChange={setTpPct1}
                min={5} max={200} step={5} style={{ width: '100%' }}
                formatter={(v) => `+${v}%`} parser={(v) => v.replace(/[+%]/g, '')} />
            </Col>
            <Col span={3}>
              <Text type="secondary" style={{ fontSize: 12 }}>4-6月止盈%</Text>
              <InputNumber value={tpPct2} onChange={setTpPct2}
                min={5} max={200} step={5} style={{ width: '100%' }}
                formatter={(v) => `+${v}%`} parser={(v) => v.replace(/[+%]/g, '')} />
            </Col>
            <Col span={3}>
              <Text type="secondary" style={{ fontSize: 12 }}>6-9月止盈%</Text>
              <InputNumber value={tpPct3} onChange={setTpPct3}
                min={1} max={100} step={5} style={{ width: '100%' }}
                formatter={(v) => `+${v}%`} parser={(v) => v.replace(/[+%]/g, '')} />
            </Col>
            <Col span={3}>
              <Text type="secondary" style={{ fontSize: 12 }}>最大持仓月数</Text>
              <InputNumber value={maxHoldMonths} onChange={setMaxHoldMonths}
                min={3} max={24} step={1} style={{ width: '100%' }} />
            </Col>
            <Col span={2}>
              <Text type="secondary" style={{ fontSize: 12 }}>最大持仓数</Text>
              <InputNumber value={maxPositions} onChange={setMaxPositions}
                min={1} max={20} step={1} style={{ width: '100%' }} />
            </Col>
            <Col span={7}>
              <Text type="secondary" style={{ fontSize: 12 }}>快速选择</Text>
              <div>
                <Space>
                  {[1, 2, 3, 5].map((y) => (
                    <Button key={y} size="small"
                      onClick={() => setDateRange([dayjs().subtract(y, 'year'), dayjs()])}>
                      近{y}年
                    </Button>
                  ))}
                </Space>
              </div>
            </Col>
          </Row>
          {progress && (
            <div style={{ marginTop: 12 }}>
              <Progress percent={progress.pct || 0} status="active" />
              <Text type="secondary">{progress.status}</Text>
            </div>
          )}
        </Card>

        {summary && (
          <>
            <Row gutter={16} style={{ marginBottom: 16 }}>
              <Col span={12}>
                <Card title={<><RocketOutlined /> 逢跌买入策略</>} size="small"
                  style={{ borderColor: '#1890ff' }}>
                  <Row gutter={16}>
                    <Col span={8}>
                      <Statistic title="总收益率" value={summary.return_pct}
                        suffix="%" precision={2}
                        valueStyle={{ color: summary.return_pct >= 0 ? '#3f8600' : '#cf1322' }}
                        prefix={summary.return_pct >= 0 ? <ArrowUpOutlined /> : <ArrowDownOutlined />} />
                    </Col>
                    <Col span={8}>
                      <Statistic title="年化收益" value={summary.annualized_return_pct}
                        suffix="%" precision={2}
                        valueStyle={{ color: summary.annualized_return_pct >= 0 ? '#3f8600' : '#cf1322' }} />
                    </Col>
                    <Col span={8}>
                      <Statistic title="最大回撤" value={summary.max_drawdown_pct}
                        suffix="%" precision={2} valueStyle={{ color: '#cf1322' }} />
                    </Col>
                  </Row>
                  <Divider style={{ margin: '12px 0' }} />
                  <Row gutter={16}>
                    <Col span={6}><Statistic title="Sharpe" value={summary.sharpe_ratio} precision={3} /></Col>
                    <Col span={6}><Statistic title="开仓次数" value={summary.open_count} /></Col>
                    <Col span={6}><Statistic title="止盈次数" value={summary.tp_count}
                      valueStyle={{ color: '#3f8600' }} /></Col>
                    <Col span={6}><Statistic title="胜率" value={summary.win_rate} suffix="%"
                      precision={1} valueStyle={{ color: summary.win_rate >= 50 ? '#3f8600' : '#cf1322' }} /></Col>
                  </Row>
                  <Divider style={{ margin: '12px 0' }} />
                  <Row gutter={16}>
                    <Col span={8}><Statistic title="强平次数" value={summary.force_close_count}
                      valueStyle={{ color: '#faad14' }} /></Col>
                    <Col span={8}><Statistic title="跳过(资金不足)" value={summary.skip_count} /></Col>
                    <Col span={8}><Statistic title="跌幅阈值" value={summary.dip_threshold} suffix="%" /></Col>
                  </Row>
                </Card>
              </Col>
              <Col span={12}>
                <Card title={<><LineChartOutlined /> Buy & Hold {summary.ticker}</>} size="small"
                  style={{ borderColor: '#52c41a' }}>
                  <Row gutter={16}>
                    <Col span={8}>
                      <Statistic title="总收益率" value={summary.bh_return_pct}
                        suffix="%" precision={2}
                        valueStyle={{ color: summary.bh_return_pct >= 0 ? '#3f8600' : '#cf1322' }}
                        prefix={summary.bh_return_pct >= 0 ? <ArrowUpOutlined /> : <ArrowDownOutlined />} />
                    </Col>
                    <Col span={8}>
                      <Statistic title="年化收益" value={summary.bh_annualized_pct}
                        suffix="%" precision={2}
                        valueStyle={{ color: summary.bh_annualized_pct >= 0 ? '#3f8600' : '#cf1322' }} />
                    </Col>
                    <Col span={8}>
                      <Statistic title="最大回撤" value={summary.bh_max_drawdown_pct}
                        suffix="%" precision={2} valueStyle={{ color: '#cf1322' }} />
                    </Col>
                  </Row>
                  <Divider style={{ margin: '12px 0' }} />
                  <Row gutter={16}>
                    <Col span={8}><Statistic title="初始资金" value={summary.initial_capital} prefix="$" precision={0} /></Col>
                    <Col span={8}><Statistic title="最终权益" value={summary.final_equity} prefix="$" precision={0}
                      valueStyle={{ color: summary.final_equity >= summary.initial_capital ? '#3f8600' : '#cf1322' }} /></Col>
                    <Col span={8}><Statistic title="总PnL" value={summary.total_pnl} prefix="$" precision={0}
                      valueStyle={{ color: summary.total_pnl >= 0 ? '#3f8600' : '#cf1322' }} /></Col>
                  </Row>
                </Card>
              </Col>
            </Row>

            {Object.keys(compare).length > 0 && (
              <Card title={<><BarChartOutlined /> ETF 收益对比</>} size="small" style={{ marginBottom: 16 }}>
                <Row gutter={16}>
                  <Col span={6}>
                    <Card size="small" style={{ background: '#e6f7ff' }}>
                      <Statistic title={`LEAPS ${summary.ticker}`}
                        value={summary.return_pct} suffix="%" precision={2}
                        valueStyle={{ color: '#1890ff', fontWeight: 'bold' }} />
                    </Card>
                  </Col>
                  {Object.entries(compare).map(([t, d]) => (
                    <Col span={6} key={t}>
                      <Card size="small">
                        <Statistic title={`B&H ${t}`}
                          value={d.total_return_pct} suffix="%" precision={2}
                          valueStyle={{ color: d.total_return_pct >= 0 ? '#3f8600' : '#cf1322' }} />
                        <Text type="secondary" style={{ fontSize: 11 }}>
                          ${d.start_price} → ${d.end_price}
                        </Text>
                      </Card>
                    </Col>
                  ))}
                </Row>
              </Card>
            )}

            {curve.length > 0 && (
              <Card title={<Space><LineChartOutlined /><span>资金曲线</span></Space>}
                style={{ marginBottom: 16 }}>
                <ResponsiveContainer width="100%" height={420}>
                  <ComposedChart data={curve}>
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="date" tick={{ fontSize: 11 }} interval="preserveStartEnd"
                      tickFormatter={(v) => v.substring(5)} />
                    <YAxis yAxisId="equity" tick={{ fontSize: 11 }} domain={['auto', 'auto']} />
                    <YAxis yAxisId="spot" orientation="right" tick={{ fontSize: 11 }} domain={['auto', 'auto']} />
                    <RTooltip formatter={(value, name) => {
                      if (name === '持仓数') return [value, name];
                      if (typeof value === 'number') return ['$' + value.toLocaleString(), name];
                      return [value, name];
                    }} />
                    <Legend />
                    <ReferenceLine yAxisId="equity" y={summary.initial_capital}
                      stroke="#999" strokeDasharray="5 5" label="初始资金" />
                    <Area yAxisId="equity" type="monotone" dataKey="equity" name="权益"
                      stroke="#1890ff" fill="#1890ff20" strokeWidth={2} />
                    <Line yAxisId="spot" type="monotone" dataKey="spot" name="标的价格"
                      stroke="#faad14" dot={false} strokeWidth={1} opacity={0.5} />
                    <Line yAxisId="equity" type="stepAfter" dataKey="num_positions" name="持仓数"
                      stroke="#722ed1" dot={false} strokeWidth={1} strokeDasharray="4 2" />
                  </ComposedChart>
                </ResponsiveContainer>
              </Card>
            )}

            {curve.length > 0 && (
              <Card title={<Space><BarChartOutlined /><span>资金利用率走势</span></Space>}
                style={{ marginBottom: 16 }}>
                <ResponsiveContainer width="100%" height={260}>
                  <ComposedChart data={curve}>
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="date" tick={{ fontSize: 11 }} interval="preserveStartEnd"
                      tickFormatter={(v) => v.substring(5)} />
                    <YAxis yAxisId="usage" tick={{ fontSize: 11 }} domain={[0, 100]}
                      tickFormatter={(v) => `${v}%`} />
                    <YAxis yAxisId="pos" orientation="right" tick={{ fontSize: 11 }}
                      domain={[0, 'auto']} allowDecimals={false} />
                    <RTooltip formatter={(value, name) => {
                      if (name === '资金利用率') return [`${value}%`, name];
                      return [value, name];
                    }} />
                    <Legend />
                    <ReferenceLine yAxisId="usage" y={80} stroke="#cf1322" strokeDasharray="4 2" label="80%" />
                    <ReferenceLine yAxisId="usage" y={50} stroke="#faad14" strokeDasharray="4 2" label="50%" />
                    <Area yAxisId="usage" type="monotone" dataKey="capital_usage_pct" name="资金利用率"
                      stroke="#722ed1" fill="#722ed120" strokeWidth={2} />
                    <Line yAxisId="pos" type="stepAfter" dataKey="num_positions" name="持仓数"
                      stroke="#1890ff" dot={false} strokeWidth={1.5} />
                  </ComposedChart>
                </ResponsiveContainer>
              </Card>
            )}

            <Card>
              <Tabs items={[
                {
                  key: 'trades',
                  label: `交易记录 (${trades.filter(t => t.action !== 'MTM').length}笔, 含${trades.filter(t => t.action === 'MTM').length}条盯市)`,
                  children: (
                    <>
                      <div style={{ marginBottom: 8 }}>
                        <Switch checked={showMtm} onChange={setShowMtm} size="small" />
                        <Text style={{ marginLeft: 6, fontSize: 12 }}>显示逐日盯市</Text>
                      </div>
                      <Table
                        dataSource={showMtm ? trades : trades.filter((t) => t.action !== 'MTM')}
                        rowKey={(r, i) => `${r.date}-${r.action}-${i}`}
                        size="small"
                        pagination={{ pageSize: 30, showSizeChanger: true, pageSizeOptions: [30, 50, 100, 200] }}
                        scroll={{ x: 1700, y: 500 }}
                        columns={tradeColumns}
                      />
                    </>
                  ),
                },
                {
                  key: 'equity',
                  label: `权益曲线 (${curve.length}天)`,
                  children: (
                    <Table
                      dataSource={curve}
                      rowKey="date"
                      size="small"
                      pagination={{ pageSize: 30, showSizeChanger: true }}
                      scroll={{ y: 500 }}
                      columns={[
                        { title: '日期', dataIndex: 'date', width: 100 },
                        { title: '权益', dataIndex: 'equity', width: 120,
                          render: (v) => `$${v?.toLocaleString()}`,
                          sorter: (a, b) => a.equity - b.equity },
                        { title: '现货', dataIndex: 'spot', width: 100,
                          render: (v) => `$${v?.toLocaleString()}` },
                        { title: '持仓市值', dataIndex: 'holdings', width: 110,
                          render: (v) => `$${v?.toLocaleString()}` },
                        { title: '未实现PnL', dataIndex: 'unrealized_pnl', width: 110,
                          render: (v) => v != null ? `$${v?.toLocaleString()}` : '-' },
                        { title: '已实现PnL', dataIndex: 'realized_pnl', width: 110,
                          render: (v) => v != null ? `$${v?.toLocaleString()}` : '-' },
                        { title: '持仓数', dataIndex: 'num_positions', width: 70 },
                        { title: 'IV', dataIndex: 'iv', width: 70,
                          render: (v) => v ? `${(v * 100).toFixed(1)}%` : '-' },
                      ]}
                    />
                  ),
                },
                {
                  key: 'params',
                  label: '回测参数',
                  children: (
                    <Descriptions bordered size="small" column={3}>
                      <Descriptions.Item label="标的">{summary.ticker}</Descriptions.Item>
                      <Descriptions.Item label="回测天数">{summary.backtest_days}天</Descriptions.Item>
                      <Descriptions.Item label="目标Delta">{summary.target_delta}</Descriptions.Item>
                      <Descriptions.Item label="跌幅阈值">{summary.dip_threshold}%</Descriptions.Item>
                      <Descriptions.Item label="IV模式">{summary.iv_mode || '动态(30日滚动)'}</Descriptions.Item>
                      <Descriptions.Item label="数据源">{CRYPTO_TICKERS.has(summary.ticker) ? 'OKX' : 'Yahoo Finance'}</Descriptions.Item>
                      <Descriptions.Item label="合约乘数">{CRYPTO_TICKERS.has(summary.ticker) ? 1 : 100}</Descriptions.Item>
                      <Descriptions.Item label="初始资金">${summary.initial_capital?.toLocaleString()}</Descriptions.Item>
                      <Descriptions.Item label="止盈规则">0-4月+{tpPct1}%, 4-6月+{tpPct2}%, 6-9月+{tpPct3}%</Descriptions.Item>
                      <Descriptions.Item label="最大持仓">{maxHoldMonths}个月, 最多{maxPositions}笔</Descriptions.Item>
                    </Descriptions>
                  ),
                },
              ]} />
            </Card>
          </>
        )}
      </Content>
    </Layout>
  );
}
