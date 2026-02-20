/**
 * 美股 LEAPS 策略回测页面
 *
 * 使用 yfinance 获取美股期权数据，支持实时扫描和 BS 模型回测。
 */
import { useState, useRef } from 'react';
import {
  Layout, Card, Form, Input, InputNumber, DatePicker, Button,
  Row, Col, Typography, Space, Table, Tag, Statistic, Divider,
  message, Progress, Alert, Collapse, Steps, Spin, Switch,
} from 'antd';
import {
  ArrowLeftOutlined, DollarOutlined, InfoCircleOutlined,
  LineChartOutlined, StopOutlined, SearchOutlined,
  LoadingOutlined, CheckCircleOutlined,
} from '@ant-design/icons';
import { Link } from 'react-router-dom';
import dayjs from 'dayjs';
import {
  XAxis, YAxis, CartesianGrid, Tooltip,
  Legend, ResponsiveContainer, Area, ComposedChart, Line, ReferenceLine,
} from 'recharts';
import { usLeapsService } from '../services/usLeapsService';
import LeapsRollDoc from './LeapsRollDoc';

const { Header: AntHeader, Content, Footer } = Layout;
const { Title, Text } = Typography;

const ACTION_COLORS = { OPEN: '#1890ff', CLOSE: '#8c8c8c', ROLL: '#faad14' };
const ACTION_LABELS = { OPEN: '买入', CLOSE: '平仓', ROLL: '换仓' };

const POPULAR_TICKERS = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'SPY', 'QQQ', 'IWM'];

export default function USLeaps() {
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [streamProgress, setStreamProgress] = useState(null);
  const abortRef = useRef(null);
  const [liveScan, setLiveScan] = useState(null);
  const [liveLoading, setLiveLoading] = useState(false);
  const [liveStep, setLiveStep] = useState(null);
  const liveAbortRef = useRef(null);

  const handleStop = () => {
    if (abortRef.current) { abortRef.current.abort(); abortRef.current = null; }
    setLoading(false);
    setStreamProgress(null);
    message.info('已取消回测');
  };

  const handleLiveScan = () => {
    const values = form.getFieldsValue();
    setLiveLoading(true);
    setLiveScan(null);
    setLiveStep(null);
    const params = {
      ticker: (values.ticker || 'AAPL').toUpperCase(),
      max_annual_tv_pct: values.max_annual_tv_pct || 10,
      min_expiry_months: values.min_expiry_months || 12,
      num_strikes: values.num_strikes || 15,
    };
    liveAbortRef.current = usLeapsService.liveScanStream(
      params,
      (stepMsg) => setLiveStep(stepMsg),
      (data) => {
        setLiveScan(data);
        setLiveLoading(false);
        setLiveStep(null);
        liveAbortRef.current = null;
        if (data.error) {
          message.warning(data.error);
        } else {
          message.success('实时扫描完成');
        }
      },
      (errMsg) => {
        setLiveLoading(false);
        setLiveStep(null);
        liveAbortRef.current = null;
        message.error('实时扫描失败: ' + errMsg);
      },
    );
  };

  const handleStopLive = () => {
    if (liveAbortRef.current) { liveAbortRef.current.abort(); liveAbortRef.current = null; }
    setLiveLoading(false);
    setLiveStep(null);
    message.info('已取消扫描');
  };

  const handleRun = async (values) => {
    setLoading(true);
    setResult(null);
    setStreamProgress({ status: '正在连接服务器...', pct: 0 });
    const params = {
      ticker: values.ticker.toUpperCase(),
      start_date: values.start_date.format('YYYY-MM-DD'),
      end_date: values.end_date.format('YYYY-MM-DD'),
      initial_capital: values.initial_capital,
      max_annual_tv_pct: values.max_annual_tv_pct,
      min_expiry_months: values.min_expiry_months,
      close_days_before: values.close_days_before,
      num_contracts: values.num_contracts,
      num_strikes: values.num_strikes,
      open_interval_days: values.open_interval_days,
      default_iv: values.default_iv,
      enable_roll: values.enable_roll || false,
      roll_annual_tv_pct: values.roll_annual_tv_pct || 8,
    };
    try {
      abortRef.current = usLeapsService.backtestStream(
        params,
        (progress) => setStreamProgress(progress),
        (data) => {
          setResult(data);
          setLoading(false);
          setStreamProgress(null);
          abortRef.current = null;
          message.success(`${params.ticker} LEAPS回测完成`);
        },
        (errMsg) => {
          setLoading(false);
          setStreamProgress(null);
          abortRef.current = null;
          message.error(errMsg || '回测失败');
        },
      );
    } catch (error) {
      setLoading(false);
      setStreamProgress(null);
      message.error('启动回测失败');
    }
  };

  const tradeColumns = [
    { title: '日期', dataIndex: 'date', key: 'date', width: 100 },
    { title: '操作', dataIndex: 'action', key: 'action', width: 80,
      render: (a) => <Tag color={ACTION_COLORS[a]}>{ACTION_LABELS[a] || a}</Tag> },
    { title: '行权价', dataIndex: 'strike', key: 'strike', width: 100,
      render: (v) => '$' + v?.toLocaleString() },
    { title: '到期日', dataIndex: 'expiry', key: 'expiry', width: 100 },
    { title: '标的价', dataIndex: 'spot', key: 'spot', width: 100,
      render: (v) => '$' + v?.toLocaleString() },
    { title: '期权价', dataIndex: 'option_price', key: 'option_price', width: 100,
      render: (v) => '$' + v?.toFixed(2) },
    { title: '数量', dataIndex: 'quantity', key: 'quantity', width: 70 },
    { title: '现金流', dataIndex: 'cash_flow', key: 'cash_flow', width: 110,
      render: (v) => (
        <span style={{ color: v >= 0 ? '#389e0d' : '#cf1322', fontWeight: 600 }}>
          {v >= 0 ? '+' : ''}${v?.toFixed(2)}
        </span>
      ) },
    { title: '数据源', dataIndex: 'data_source', key: 'data_source', width: 90,
      render: (v) => <Tag color="orange">{v}</Tag> },
    { title: '备注', dataIndex: 'note', key: 'note', ellipsis: true },
  ];

  const scanCols = [
    { title: '行权价', dataIndex: 'strike', key: 'strike', width: 90,
      render: (v) => '$' + v?.toLocaleString() },
    { title: '期权价', dataIndex: 'price', key: 'price', width: 100,
      render: (v) => v > 0 ? '$' + v?.toFixed(2) : '-' },
    { title: 'Bid', dataIndex: 'bid', key: 'bid', width: 80,
      render: (v) => v > 0 ? '$' + v?.toFixed(2) : '-' },
    { title: 'Ask', dataIndex: 'ask', key: 'ask', width: 80,
      render: (v) => v > 0 ? '$' + v?.toFixed(2) : '-' },
    { title: 'IV', dataIndex: 'iv', key: 'iv', width: 70,
      render: (v) => v ? (v * 100).toFixed(1) + '%' : '-' },
    { title: '成交量', dataIndex: 'volume', key: 'volume', width: 80 },
    { title: '持仓量', dataIndex: 'open_interest', key: 'open_interest', width: 80 },
    { title: '内在价值', dataIndex: 'intrinsic', key: 'intrinsic', width: 90,
      render: (v) => v > 0 ? '$' + v?.toFixed(2) : '-' },
    { title: '时间价值', dataIndex: 'time_value', key: 'time_value', width: 90,
      render: (v) => v > 0 ? '$' + v?.toFixed(2) : '-' },
    { title: '年化TV%', dataIndex: 'annual_tv_pct', key: 'annual_tv_pct', width: 90,
      render: (v) => v != null ? (
        <span style={{ color: v < 10 ? '#389e0d' : '#faad14', fontWeight: 600 }}>
          {v.toFixed(2)}%
        </span>
      ) : '-' },
    { title: '', dataIndex: 'selected', key: 'selected', width: 40,
      render: (v) => v ? <Tag color="green">✓</Tag> : null },
    { title: '备注', dataIndex: 'note', key: 'note', ellipsis: true },
  ];

  const btScanCols = scanCols.filter(c => !['bid', 'ask', 'volume', 'open_interest'].includes(c.dataIndex));

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <AntHeader style={{ background: '#001529', padding: '0 24px', display: 'flex', alignItems: 'center' }}>
        <Link to="/" style={{ display: 'flex', alignItems: 'center', marginRight: 16 }}>
          <ArrowLeftOutlined style={{ fontSize: 18, color: '#1890ff' }} />
        </Link>
        <DollarOutlined style={{ fontSize: 24, color: '#52c41a', marginRight: 12 }} />
        <Title level={3} style={{ color: 'white', margin: 0, fontSize: 20 }}>
          美股 LEAPS 策略
        </Title>
      </AntHeader>

      <Content style={{ padding: 24, background: '#f0f2f5' }}>
        <div style={{ maxWidth: 1400, margin: '0 auto' }}>
          <Alert
            type="info" showIcon icon={<InfoCircleOutlined />}
            style={{ marginBottom: 16 }}
            message="美股 LEAPS 策略说明"
            description={
              <div style={{ fontSize: 13 }}>
                <p style={{ margin: '4px 0' }}>
                  使用 yfinance 获取美股期权数据。实时扫描使用真实市场报价（bid/ask/IV），
                  回测使用 Black-Scholes 模型估算历史期权价格。
                </p>
                <p style={{ margin: '4px 0' }}>
                  美股期权合约乘数固定为 100（1张合约 = 100股）。
                  LEAPS 通常为1月到期的长期期权。资金不足时自动减少合约数。
                </p>
              </div>
            }
          />

          <LeapsRollDoc />

          <Card title="策略参数">
            <Form form={form} layout="vertical" onFinish={handleRun}
              initialValues={{
                ticker: 'AAPL',
                start_date: dayjs('2023-01-01'),
                end_date: dayjs(),
                initial_capital: 100000,
                max_annual_tv_pct: 10,
                min_expiry_months: 12,
                close_days_before: 30,
                num_contracts: 1,
                num_strikes: 15,
                open_interval_days: 30,
                default_iv: 0.3,
                enable_roll: false,
                roll_annual_tv_pct: 8,
              }}
            >
              <Divider orientation="left" plain>基础参数</Divider>
              <Row gutter={16}>
                <Col xs={24} sm={4}>
                  <Form.Item name="ticker" label="标的代码" rules={[{ required: true }]}>
                    <Input placeholder="AAPL" style={{ textTransform: 'uppercase' }} />
                  </Form.Item>
                </Col>
                <Col xs={12} sm={4}>
                  <Form.Item name="start_date" label="开始日期" rules={[{ required: true }]}>
                    <DatePicker style={{ width: '100%' }} format="YYYY-MM-DD" />
                  </Form.Item>
                </Col>
                <Col xs={12} sm={4}>
                  <Form.Item name="end_date" label="结束日期" rules={[{ required: true }]}>
                    <DatePicker style={{ width: '100%' }} format="YYYY-MM-DD" />
                  </Form.Item>
                </Col>
                <Col xs={24} sm={12}>
                  <Form.Item label="热门标的">
                    <Space wrap size={[4, 4]}>
                      {POPULAR_TICKERS.map((t) => (
                        <Button key={t} size="small" disabled={loading}
                          onClick={() => form.setFieldsValue({ ticker: t })}>
                          {t}
                        </Button>
                      ))}
                    </Space>
                  </Form.Item>
                </Col>
              </Row>
              <Row gutter={16}>
                <Col xs={24} sm={6}>
                  <Form.Item label="快速选择">
                    <Space wrap size={[4, 4]}>
                      {[
                        { label: '近1年', start: dayjs().subtract(1, 'year'), end: dayjs() },
                        { label: '近2年', start: dayjs().subtract(2, 'year'), end: dayjs() },
                        { label: '近3年', start: dayjs().subtract(3, 'year'), end: dayjs() },
                        { label: '近5年', start: dayjs().subtract(5, 'year'), end: dayjs() },
                      ].map((p) => (
                        <Button key={p.label} size="small" disabled={loading}
                          onClick={() => form.setFieldsValue({ start_date: p.start, end_date: p.end })}>
                          {p.label}
                        </Button>
                      ))}
                    </Space>
                  </Form.Item>
                </Col>
                <Col xs={24} sm={6}>
                  <Form.Item name="initial_capital" label="初始资金 (USD)">
                    <InputNumber style={{ width: '100%' }} min={1000} step={10000} precision={0} />
                  </Form.Item>
                </Col>
                <Col xs={24} sm={6}>
                  <Form.Item name="num_contracts" label="每次买入合约数">
                    <InputNumber style={{ width: '100%' }} min={1} max={100} step={1} />
                  </Form.Item>
                </Col>
                <Col xs={24} sm={6}>
                  <Form.Item name="default_iv" label="回测默认IV"
                    tooltip="BS模型使用的隐含波动率，建议AAPL用0.25-0.35，TSLA用0.5-0.7">
                    <InputNumber style={{ width: '100%' }} min={0.05} max={2.0} step={0.05} precision={2} />
                  </Form.Item>
                </Col>
              </Row>

              <Divider orientation="left" plain>LEAPS 筛选参数</Divider>
              <Row gutter={16}>
                <Col xs={24} sm={6}>
                  <Form.Item name="max_annual_tv_pct" label="最大年化TV%">
                    <InputNumber style={{ width: '100%' }} min={1} max={50} step={1} precision={1}
                      formatter={(v) => v + '%'} parser={(v) => v.replace('%', '')} />
                  </Form.Item>
                </Col>
                <Col xs={24} sm={6}>
                  <Form.Item name="min_expiry_months" label="最短到期月数">
                    <InputNumber style={{ width: '100%' }} min={6} max={36} step={3} />
                  </Form.Item>
                </Col>
                <Col xs={24} sm={6}>
                  <Form.Item name="close_days_before" label="到期前N天平仓">
                    <InputNumber style={{ width: '100%' }} min={7} max={90} step={7} />
                  </Form.Item>
                </Col>
                <Col xs={24} sm={6}>
                  <Form.Item name="open_interval_days" label="检查间隔(天)">
                    <InputNumber style={{ width: '100%' }} min={7} max={90} step={7} />
                  </Form.Item>
                </Col>
              </Row>

              <Divider orientation="left" plain>换仓参数</Divider>
              <Row gutter={16}>
                <Col xs={24} sm={6}>
                  <Form.Item name="enable_roll" label="启用换仓" valuePropName="checked"
                    tooltip="持仓期间检查是否有更优的远期合约可换仓">
                    <Switch checkedChildren="开" unCheckedChildren="关" />
                  </Form.Item>
                </Col>
                <Col xs={24} sm={6}>
                  <Form.Item name="roll_annual_tv_pct" label="换仓年化TV阈值%"
                    tooltip="换仓年化成本低于此阈值时执行换仓">
                    <InputNumber style={{ width: '100%' }} min={1} max={30} step={0.5} precision={1}
                      formatter={(v) => v + '%'} parser={(v) => v.replace('%', '')} />
                  </Form.Item>
                </Col>
              </Row>

              <Form.Item>
                <Space>
                  <Button type="primary" htmlType="submit" disabled={loading}
                    icon={<DollarOutlined />} size="large"
                    style={{ background: '#52c41a', borderColor: '#52c41a', minWidth: 300 }}>
                    {loading ? '回测中...' : '开始美股LEAPS回测 (BS模型)'}
                  </Button>
                  {loading && (
                    <Button danger icon={<StopOutlined />} size="large" onClick={handleStop}>取消</Button>
                  )}
                </Space>
              </Form.Item>
            </Form>
          </Card>

          {/* 回测进度 */}
          {loading && streamProgress && (
            <Card style={{ marginTop: 16, borderColor: '#52c41a' }}>
              <Space direction="vertical" style={{ width: '100%' }}>
                <Space>
                  <Spin indicator={<LoadingOutlined style={{ fontSize: 18, color: '#52c41a' }} spin />} />
                  <Text strong style={{ color: '#52c41a', fontSize: 15 }}>
                    {streamProgress.status || '计算中...'}
                  </Text>
                </Space>
                <Progress
                  percent={streamProgress.pct || 0}
                  status="active"
                  strokeColor={{ '0%': '#52c41a', '100%': '#389e0d' }}
                  format={(pct) => `${pct}%`}
                />
                <Row gutter={16}>
                  <Col span={8}>
                    <Text type="secondary">
                      观察日: {streamProgress.day || 0} / {streamProgress.total || '?'}
                    </Text>
                  </Col>
                  <Col span={8}>
                    <Text type="secondary">
                      {streamProgress.date && `当前日期: ${streamProgress.date}`}
                    </Text>
                  </Col>
                  <Col span={8} style={{ textAlign: 'right' }}>
                    <Text type="secondary">
                      {streamProgress.pct > 0 && streamProgress.pct < 100 && '预计即将完成'}
                    </Text>
                  </Col>
                </Row>
              </Space>
            </Card>
          )}
          {loading && !streamProgress && (
            <Card style={{ marginTop: 16 }}>
              <Space>
                <Spin indicator={<LoadingOutlined style={{ fontSize: 18 }} spin />} />
                <Text>正在连接服务器...</Text>
              </Space>
            </Card>
          )}

          {result && (
            <>
              <Card title={
                <Space>
                  <CheckCircleOutlined style={{ color: '#52c41a' }} />
                  回测摘要 — {result.summary?.ticker}
                  <Tag color="orange">BS模型 (IV={result.summary?.default_iv})</Tag>
                  <Tag color="blue">{result.summary?.backtest_days}天</Tag>
                </Space>
              } style={{ marginTop: 16 }}>
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
                </Row>
                <Divider />
                <Row gutter={[24, 16]}>
                  <Col xs={12} sm={6}>
                    <Statistic title="年化收益率" value={result.summary.annualized_return_pct} suffix="%" precision={2}
                      valueStyle={{ color: result.summary.annualized_return_pct >= 0 ? '#389e0d' : '#cf1322' }} />
                  </Col>
                  <Col xs={12} sm={6}>
                    <Statistic title="最大回撤" value={result.summary.max_drawdown_pct} suffix="%" precision={2}
                      valueStyle={{ color: '#cf1322' }} />
                  </Col>
                  <Col xs={12} sm={6}>
                    <Statistic title="买入次数" value={result.summary.open_count} />
                  </Col>
                  <Col xs={12} sm={6}>
                    <Statistic title="平仓次数" value={result.summary.close_count} />
                  </Col>
                  {result.summary.roll_count > 0 && (
                    <Col xs={12} sm={6}>
                      <Statistic title="换仓次数" value={result.summary.roll_count}
                        valueStyle={{ color: '#faad14', fontWeight: 700 }} />
                    </Col>
                  )}
                </Row>
                <Divider />
                <Row gutter={[24, 16]}>
                  <Col xs={12} sm={6}>
                    <Statistic title="策略夏普比率" value={result.summary.sharpe_ratio} precision={3}
                      valueStyle={{ color: result.summary.sharpe_ratio >= 0 ? '#389e0d' : '#cf1322', fontWeight: 700 }} />
                  </Col>
                  <Col xs={12} sm={6}>
                    <Statistic title="现货夏普比率" value={result.summary.spot_sharpe_ratio} precision={3}
                      valueStyle={{ color: result.summary.spot_sharpe_ratio >= 0 ? '#389e0d' : '#cf1322', fontWeight: 700 }} />
                  </Col>
                  <Col xs={12} sm={6}>
                    <Statistic title="现货总收益率" value={result.summary.spot_return_pct} suffix="%" precision={2}
                      valueStyle={{ color: result.summary.spot_return_pct >= 0 ? '#389e0d' : '#cf1322' }} />
                  </Col>
                </Row>
              </Card>

              <Card title={<Space><LineChartOutlined /><span>资金曲线</span></Space>}
                style={{ marginTop: 16 }}>
                <ResponsiveContainer width="100%" height={420}>
                  <ComposedChart data={result.equity_curve}>
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="date" tick={{ fontSize: 11 }} interval="preserveStartEnd"
                      tickFormatter={(v) => v.substring(5)} />
                    <YAxis yAxisId="equity" tick={{ fontSize: 11 }} domain={['auto', 'auto']} />
                    <YAxis yAxisId="spot" orientation="right" tick={{ fontSize: 11 }} domain={['auto', 'auto']} />
                    <Tooltip formatter={(value, name) => {
                      if (typeof value === 'number') return ['$' + value.toLocaleString(), name];
                      return [value, name];
                    }} />
                    <Legend />
                    <ReferenceLine yAxisId="equity" y={result.summary.initial_capital}
                      stroke="#999" strokeDasharray="5 5" label="初始资金" />
                    <Area yAxisId="equity" type="monotone" dataKey="equity" name="权益"
                      stroke="#52c41a" fill="#52c41a20" strokeWidth={2} />
                    <Line yAxisId="spot" type="monotone" dataKey="spot" name="标的价格"
                      stroke="#faad14" dot={false} strokeWidth={1} opacity={0.5} />
                  </ComposedChart>
                </ResponsiveContainer>
              </Card>

              <Card title="交易记录" style={{ marginTop: 16 }}>
                <Table
                  columns={tradeColumns}
                  dataSource={result.trades.map((t, i) => ({ ...t, key: i }))}
                  size="small"
                  pagination={{ pageSize: 20, showSizeChanger: true, showTotal: (t) => `共 ${t} 条` }}
                  scroll={{ x: 1200 }}
                />
              </Card>

              {result.scan_logs && result.scan_logs.length > 0 && (
                <Card title={
                  <Space><SearchOutlined /><span>扫描日志 ({result.scan_logs.length}次)</span></Space>
                } style={{ marginTop: 16 }}>
                  <Collapse accordion>
                    {result.scan_logs.map((log, idx) => (
                      <Collapse.Panel key={idx} header={
                        <Space>
                          <Tag color="blue">{log.date}</Tag>
                          <Text>spot=${log.spot?.toLocaleString()}</Text>
                          <Text type="secondary">expiry={log.expiry}</Text>
                          {log.selected_strike && <Tag color="green">K=${log.selected_strike}</Tag>}
                          {log.selected_expiry && <Tag color="gold">→{log.selected_expiry}</Tag>}
                          <Tag color={
                            log.result?.includes('开仓') ? 'green' :
                            log.result?.includes('换仓(') ? 'gold' :
                            log.result?.includes('持仓中') ? 'blue' : 'orange'
                          }>{log.result}</Tag>
                        </Space>
                      }>
                        {log.candidates && log.candidates.length > 0 && (
                          <>
                            <Text strong style={{ display: 'block', marginBottom: 4 }}>开仓扫描:</Text>
                            <Table
                              columns={btScanCols}
                              dataSource={log.candidates.map((c, j) => ({ ...c, key: j }))}
                              size="small" pagination={false} scroll={{ x: 900 }}
                              rowClassName={(r) => r.selected ? 'ant-table-row-selected' : ''}
                            />
                          </>
                        )}
                        {log.roll_candidates && log.roll_candidates.length > 0 && (
                          <>
                            <Text strong style={{ display: 'block', margin: '12px 0 4px' }}>换仓扫描:</Text>
                            <Table
                              columns={[
                                { title: '行权价', dataIndex: 'strike', key: 'strike', width: 90,
                                  render: (v) => '$' + v?.toLocaleString() },
                                { title: '到期日', dataIndex: 'expiry', key: 'expiry', width: 100 },
                                { title: '期权价', dataIndex: 'price', key: 'price', width: 90,
                                  render: (v) => v > 0 ? '$' + v?.toFixed(2) : '-' },
                                { title: '远期TV', dataIndex: 'far_tv', key: 'far_tv', width: 80,
                                  render: (v) => v > 0 ? '$' + v?.toFixed(2) : '-' },
                                { title: '当前TV', dataIndex: 'cur_tv', key: 'cur_tv', width: 80,
                                  render: (v) => '$' + v?.toFixed(2) },
                                { title: 'TV差值', dataIndex: 'tv_diff', key: 'tv_diff', width: 80,
                                  render: (v) => v != null ? '$' + v?.toFixed(2) : '-' },
                                { title: '年化成本%', dataIndex: 'annual_roll_cost', key: 'annual_roll_cost', width: 100,
                                  render: (v) => v != null ? (
                                    <span style={{ color: v < 8 ? '#389e0d' : '#faad14', fontWeight: 600 }}>
                                      {v.toFixed(2)}%
                                    </span>
                                  ) : '-' },
                                { title: '', dataIndex: 'selected', key: 'selected', width: 40,
                                  render: (v) => v ? <Tag color="gold">✓</Tag> : null },
                                { title: '备注', dataIndex: 'note', key: 'note', ellipsis: true },
                              ]}
                              dataSource={log.roll_candidates.map((c, j) => ({ ...c, key: 'r' + j }))}
                              size="small" pagination={false} scroll={{ x: 900 }}
                              rowClassName={(r) => r.selected ? 'ant-table-row-selected' : ''}
                            />
                          </>
                        )}
                      </Collapse.Panel>
                    ))}
                  </Collapse>
                </Card>
              )}
            </>
          )}

          {/* ── 实时扫描 ── */}
          <Card title={
            <Space>
              <DollarOutlined style={{ color: '#52c41a' }} />
              <span>实时市场扫描 (yfinance)</span>
              {!liveLoading ? (
                <Button type="primary" size="small"
                  onClick={handleLiveScan}
                  style={{ background: '#52c41a', borderColor: '#52c41a' }}>
                  扫描当前市场
                </Button>
              ) : (
                <Button danger size="small" onClick={handleStopLive}>
                  取消扫描
                </Button>
              )}
            </Space>
          } style={{ marginTop: 16 }}>
            {!liveScan && !liveLoading && (
              <Text type="secondary">点击"扫描当前市场"获取实时期权数据和推荐合约</Text>
            )}
            {liveLoading && (
              <div style={{ padding: '16px 0' }}>
                <Steps
                  current={liveStep ? liveStep.step - 1 : 0}
                  size="small"
                  items={[
                    { title: '获取价格', description: liveStep?.step === 1 ? liveStep.message : (liveStep?.step > 1 ? '完成' : '等待中') },
                    { title: '查询到期日', description: liveStep?.step === 2 ? liveStep.message : (liveStep?.step > 2 ? '完成' : '等待中') },
                    { title: '获取期权链', description: liveStep?.step === 3 ? liveStep.message : (liveStep?.step > 3 ? '完成' : '等待中') },
                    { title: '扫描合约', description: liveStep?.step === 4 ? liveStep.message : (liveStep?.step > 4 ? '完成' : '等待中') },
                    { title: '分析完成', description: liveStep?.step === 5 ? liveStep.message : '等待中' },
                  ]}
                />
                <div style={{ textAlign: 'center', marginTop: 12 }}>
                  <Spin indicator={<LoadingOutlined style={{ fontSize: 20, color: '#52c41a' }} spin />} />
                  <Text style={{ marginLeft: 8, color: '#52c41a' }}>
                    {liveStep?.message || '正在连接...'}
                  </Text>
                </div>
              </div>
            )}
            {liveScan && liveScan.error && <Alert type="warning" message={liveScan.error} />}
            {liveScan && !liveScan.error && (
              <div>
                <Row gutter={[16, 8]} style={{ marginBottom: 16 }}>
                  <Col xs={8} sm={3}>
                    <Statistic title="标的" value={liveScan.ticker} valueStyle={{ fontSize: 16, fontWeight: 700 }} />
                  </Col>
                  <Col xs={8} sm={3}>
                    <Statistic title="现价" value={liveScan.spot} prefix="$" precision={2} />
                  </Col>
                  <Col xs={8} sm={4}>
                    <Statistic title="到期日" value={liveScan.expiry1}
                      suffix={` (${liveScan.days_to_expiry1}天)`} valueStyle={{ fontSize: 14 }} />
                  </Col>
                  {liveScan.recommended && (
                    <>
                      <Col xs={8} sm={3}>
                        <Statistic title="推荐行权价" value={liveScan.recommended.strike}
                          prefix="$" precision={0}
                          valueStyle={{ color: '#389e0d', fontWeight: 700 }} />
                      </Col>
                      <Col xs={8} sm={3}>
                        <Statistic title="期权价(mid)" value={liveScan.recommended.price}
                          prefix="$" precision={2} />
                      </Col>
                      <Col xs={8} sm={3}>
                        <Statistic title="年化TV%" value={liveScan.recommended.annual_tv_pct}
                          suffix="%" precision={2}
                          valueStyle={{ color: liveScan.recommended.annual_tv_pct < 10 ? '#389e0d' : '#faad14' }} />
                      </Col>
                      <Col xs={24} sm={5}>
                        <Statistic title="IV" value={liveScan.recommended.iv ? (liveScan.recommended.iv * 100).toFixed(1) + '%' : '-'}
                          valueStyle={{ fontSize: 14 }} />
                      </Col>
                    </>
                  )}
                </Row>
                {liveScan.fallback_used && (
                  <Alert type="warning" style={{ marginBottom: 8 }}
                    message="未找到满足年化TV%阈值的合约，已回退选择最低TV%的ITM合约" />
                )}
                {liveScan.leaps_expiries && (
                  <div style={{ marginBottom: 8 }}>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      LEAPS到期日: {liveScan.leaps_expiries.join(', ')}
                    </Text>
                  </div>
                )}
                <Text strong>到期日: {liveScan.expiry1} ({liveScan.days_to_expiry1}天)</Text>
                <Table
                  columns={scanCols}
                  dataSource={liveScan.candidates1?.map((c, j) => ({ ...c, key: j })) || []}
                  size="small" pagination={false} scroll={{ x: 1200 }}
                  style={{ marginTop: 8, marginBottom: 16 }}
                  rowClassName={(r) => r.selected ? 'ant-table-row-selected' : ''}
                />
                {liveScan.candidates2 && liveScan.candidates2.length > 0 && (
                  <>
                    <Text strong>到期日2: {liveScan.expiry2} ({liveScan.days_to_expiry2}天)</Text>
                    <Table
                      columns={scanCols}
                      dataSource={liveScan.candidates2.map((c, j) => ({ ...c, key: 'b' + j }))}
                      size="small" pagination={false} scroll={{ x: 1200 }}
                      style={{ marginTop: 8 }}
                    />
                  </>
                )}
              </div>
            )}
          </Card>
        </div>
      </Content>

      <Footer style={{ textAlign: 'center', background: '#001529', color: 'white' }}>
        期权风险监控系统 ©2024 | 美股 LEAPS 策略 (yfinance)
      </Footer>
    </Layout>
  );
}
