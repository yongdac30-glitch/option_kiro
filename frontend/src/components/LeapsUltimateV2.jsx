/**
 * LEAPS终极2.0 策略回测页面
 *
 * 在LEAPS终极版基础上增加换仓逻辑：
 * 每个检查日，如果持仓不需要平仓，扫描更远期合约，
 * 判断 (远期TV - 当前剩余TV) 的年化值是否低于换仓阈值，满足则换仓。
 */
import { useState, useRef } from 'react';
import {
  Layout, Card, Form, Select, InputNumber, DatePicker, Button,
  Row, Col, Typography, Space, Table, Tag, Statistic, Divider,
  message, Progress, Switch, Alert, Collapse,
} from 'antd';
import {
  ArrowLeftOutlined, CrownOutlined, InfoCircleOutlined,
  LineChartOutlined, StopOutlined, DatabaseOutlined, SearchOutlined,
  SwapOutlined,
} from '@ant-design/icons';
import { Link } from 'react-router-dom';
import dayjs from 'dayjs';
import {
  XAxis, YAxis, CartesianGrid, Tooltip,
  Legend, ResponsiveContainer, Area, ComposedChart, Line, ReferenceLine,
} from 'recharts';
import { leapsUltimateV2Service } from '../services/leapsUltimateV2Service';

const { Header: AntHeader, Content, Footer } = Layout;
const { Title, Text } = Typography;
const { Option } = Select;

const ACTION_COLORS = { OPEN: '#1890ff', CLOSE: '#8c8c8c', ROLL: '#faad14' };
const ACTION_LABELS = { OPEN: '买入', CLOSE: '平仓', ROLL: '换仓' };

export default function LeapsUltimateV2() {
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [useRealData, setUseRealData] = useState(true);
  const [useHfData, setUseHfData] = useState(false);
  const [streamProgress, setStreamProgress] = useState(null);
  const abortRef = useRef(null);

  const handleStop = () => {
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    setLoading(false);
    setStreamProgress(null);
    message.info('已取消回测');
  };

  const handleRun = async (values) => {
    setLoading(true);
    setResult(null);
    setStreamProgress(null);

    const params = {
      underlying: values.underlying,
      start_date: values.start_date.format('YYYY-MM-DD'),
      end_date: values.end_date.format('YYYY-MM-DD'),
      initial_capital: values.initial_capital,
      contract_multiplier: values.contract_multiplier,
      max_annual_tv_pct: values.max_annual_tv_pct,
      roll_annual_tv_pct: values.roll_annual_tv_pct,
      min_expiry_months: values.min_expiry_months,
      close_days_before: values.close_days_before,
      quantity: values.quantity,
      num_strikes: values.num_strikes,
      open_interval_days: values.open_interval_days,
      use_real_data: useRealData,
      use_hf_data: useHfData,
    };

    try {
      abortRef.current = leapsUltimateV2Service.backtestStream(
        params,
        (progress) => setStreamProgress(progress),
        (data) => {
          setResult(data);
          setLoading(false);
          setStreamProgress(null);
          abortRef.current = null;
          message.success('LEAPS终极2.0回测完成');
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
      message.error('启动回测失败');
    }
  };

  const tradeColumns = [
    { title: '日期', dataIndex: 'date', key: 'date', width: 100 },
    {
      title: '操作', dataIndex: 'action', key: 'action', width: 80,
      render: (a) => <Tag color={ACTION_COLORS[a]}>{ACTION_LABELS[a] || a}</Tag>,
    },
    { title: '行权价', dataIndex: 'strike', key: 'strike', width: 100,
      render: (v) => '$' + v?.toLocaleString() },
    { title: '到期日', dataIndex: 'expiry', key: 'expiry', width: 100 },
    { title: '标的价', dataIndex: 'spot', key: 'spot', width: 100,
      render: (v) => '$' + v?.toLocaleString() },
    { title: '期权价', dataIndex: 'option_price', key: 'option_price', width: 100,
      render: (v) => '$' + v?.toFixed(2) },
    { title: '数量', dataIndex: 'quantity', key: 'quantity', width: 70 },
    { title: '内在价值', dataIndex: 'intrinsic', key: 'intrinsic', width: 100,
      render: (v) => '$' + v?.toFixed(2) },
    { title: '时间价值', dataIndex: 'time_value', key: 'time_value', width: 100,
      render: (v) => '$' + v?.toFixed(2) },
    {
      title: '年化TV%', dataIndex: 'annual_tv_pct', key: 'annual_tv_pct', width: 90,
      render: (v) => (
        <span style={{ color: v < 10 ? '#389e0d' : '#faad14', fontWeight: 600 }}>
          {v?.toFixed(2)}%
        </span>
      ),
    },
    {
      title: '现金流', dataIndex: 'cash_flow', key: 'cash_flow', width: 110,
      render: (v) => (
        <span style={{ color: v >= 0 ? '#389e0d' : '#cf1322', fontWeight: 600 }}>
          {v >= 0 ? '+' : ''}${v?.toFixed(2)}
        </span>
      ),
    },
    {
      title: '数据源', dataIndex: 'data_source', key: 'data_source', width: 90,
      render: (v) => v ? (
        <Tag color={v.includes('cache') || v.includes('chart') || v.includes('trade') ? 'green' : v === 'roll' ? 'gold' : 'orange'}>
          {v}
        </Tag>
      ) : <Tag>模拟</Tag>,
    },
    { title: '备注', dataIndex: 'note', key: 'note', ellipsis: true },
  ];

  const scanCols = [
    { title: '行权价', dataIndex: 'strike', key: 'strike', width: 90,
      render: (v) => '$' + v?.toLocaleString() },
    { title: '合约名', dataIndex: 'instrument', key: 'instrument', width: 220, ellipsis: true },
    { title: '期权价', dataIndex: 'price', key: 'price', width: 100,
      render: (v) => v > 0 ? '$' + v?.toFixed(2) : '-' },
    { title: 'IV', dataIndex: 'iv', key: 'iv', width: 70,
      render: (v) => v ? (v * 100).toFixed(1) + '%' : '-' },
    { title: '数据源', dataIndex: 'data_source', key: 'data_source', width: 100,
      render: (v) => v ? <Tag color={v.includes('cache') || v.includes('chart') || v.includes('trade') ? 'green' : 'orange'}>{v}</Tag> : '-' },
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

  const rollCols = [
    { title: '行权价', dataIndex: 'strike', key: 'strike', width: 90,
      render: (v) => '$' + v?.toLocaleString() },
    { title: '合约名', dataIndex: 'instrument', key: 'instrument', width: 220, ellipsis: true },
    { title: '到期日', dataIndex: 'expiry', key: 'expiry', width: 100 },
    { title: '远期价', dataIndex: 'price', key: 'price', width: 100,
      render: (v) => v > 0 ? '$' + v?.toFixed(2) : '-' },
    { title: '远期TV', dataIndex: 'far_tv', key: 'far_tv', width: 90,
      render: (v) => v > 0 ? '$' + v?.toFixed(2) : '-' },
    { title: '当前TV', dataIndex: 'cur_tv', key: 'cur_tv', width: 90,
      render: (v) => '$' + v?.toFixed(2) },
    { title: 'TV差值', dataIndex: 'tv_diff', key: 'tv_diff', width: 90,
      render: (v) => v != null ? '$' + v?.toFixed(2) : '-' },
    { title: '年化换仓成本', dataIndex: 'annual_roll_cost', key: 'annual_roll_cost', width: 120,
      render: (v) => v != null ? (
        <span style={{ color: v < 8 ? '#389e0d' : '#faad14', fontWeight: 600 }}>
          {v.toFixed(2)}%
        </span>
      ) : '-' },
    { title: '', dataIndex: 'selected', key: 'selected', width: 40,
      render: (v) => v ? <Tag color="gold">✓</Tag> : null },
    { title: '备注', dataIndex: 'note', key: 'note', ellipsis: true },
  ];

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <AntHeader style={{ background: '#001529', padding: '0 24px', display: 'flex', alignItems: 'center' }}>
        <Link to="/" style={{ display: 'flex', alignItems: 'center', marginRight: 16 }}>
          <ArrowLeftOutlined style={{ fontSize: 18, color: '#1890ff' }} />
        </Link>
        <SwapOutlined style={{ fontSize: 24, color: '#722ed1', marginRight: 12 }} />
        <Title level={3} style={{ color: 'white', margin: 0, fontSize: 20 }}>
          LEAPS终极2.0 策略回测（含换仓）
        </Title>
      </AntHeader>

      <Content style={{ padding: 24, background: '#f0f2f5' }}>
        <div style={{ maxWidth: 1400, margin: '0 auto' }}>
          <Alert
            type="info" showIcon icon={<InfoCircleOutlined />}
            style={{ marginBottom: 16 }}
            message="LEAPS终极2.0 策略说明"
            description={
              <div style={{ fontSize: 13 }}>
                <p style={{ margin: '4px 0' }}>
                  在LEAPS终极版基础上增加换仓逻辑。每个检查日，如果持仓不需要平仓，
                  则扫描更远期到期日的合约。
                </p>
                <p style={{ margin: '4px 0' }}>
                  换仓条件：(远期合约时间价值 - 当前合约剩余时间价值) 的年化值 &lt; 换仓阈值。
                  扫描顺序：从更远期的ATM开始往低价方向扫描，选首个满足条件的合约。
                </p>
                <p style={{ margin: '4px 0' }}>
                  换仓可以在到期前提前锁定更远期的低TV%合约，减少到期平仓再开仓的摩擦成本。
                </p>
              </div>
            }
          />

          <Card title={
            <Space>
              <span>策略参数</span>
              <Divider type="vertical" />
              <Space>
                <DatabaseOutlined style={{ color: useRealData ? '#52c41a' : '#999' }} />
                <Switch
                  checked={useRealData}
                  onChange={setUseRealData}
                  checkedChildren="Deribit真实数据"
                  unCheckedChildren="模拟IV"
                  disabled={loading}
                />
                {useRealData && <Tag color="green">真实IV</Tag>}
              </Space>
              <Divider type="vertical" />
              <Space>
                <Switch
                  checked={useHfData}
                  onChange={setUseHfData}
                  checkedChildren="高频数据优先"
                  unCheckedChildren="仅缓存"
                  disabled={loading}
                  size="small"
                />
                {useHfData && <Tag color="gold">HF</Tag>}
              </Space>
            </Space>
          }>
            <Form form={form} layout="vertical" onFinish={handleRun}
              initialValues={{
                underlying: 'BTC',
                start_date: dayjs('2023-01-01'),
                end_date: dayjs(),
                initial_capital: 100000,
                contract_multiplier: 0.01,
                max_annual_tv_pct: 10,
                roll_annual_tv_pct: 8,
                min_expiry_months: 12,
                close_days_before: 30,
                quantity: 1,
                num_strikes: 15,
                open_interval_days: 30,
              }}
            >
              <Divider orientation="left" plain>基础参数</Divider>
              <Row gutter={16}>
                <Col xs={24} sm={4}>
                  <Form.Item name="underlying" label="标的" rules={[{ required: true }]}>
                    <Select>
                      <Option value="BTC">BTC</Option>
                      <Option value="ETH">ETH</Option>
                    </Select>
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
                  <Form.Item label="快速选择">
                    <Space wrap size={[4, 4]}>
                      {[
                        { label: '近1年', start: dayjs().subtract(1, 'year'), end: dayjs() },
                        { label: '近2年', start: dayjs().subtract(2, 'year'), end: dayjs() },
                        { label: '近3年', start: dayjs().subtract(3, 'year'), end: dayjs() },
                        { label: '2021至今', start: dayjs('2021-01-01'), end: dayjs() },
                        { label: '2022至今', start: dayjs('2022-01-01'), end: dayjs() },
                        { label: '2023至今', start: dayjs('2023-01-01'), end: dayjs() },
                        { label: '2024至今', start: dayjs('2024-01-01'), end: dayjs() },
                      ].map((p) => (
                        <Button key={p.label} size="small" disabled={loading}
                          onClick={() => { form.setFieldsValue({ start_date: p.start, end_date: p.end }); }}>
                          {p.label}
                        </Button>
                      ))}
                    </Space>
                  </Form.Item>
                </Col>
              </Row>
              <Row gutter={16}>
                <Col xs={24} sm={6}>
                  <Form.Item name="initial_capital" label="初始资金 (USD)">
                    <InputNumber style={{ width: '100%' }} min={1000} step={10000} precision={0} />
                  </Form.Item>
                </Col>
                <Col xs={24} sm={6}>
                  <Form.Item name="contract_multiplier" label="合约乘数">
                    <InputNumber style={{ width: '100%' }} min={0.001} max={100} step={0.01} precision={3} />
                  </Form.Item>
                </Col>
              </Row>

              <Divider orientation="left" plain>LEAPS 筛选参数</Divider>
              <Row gutter={16}>
                <Col xs={24} sm={6}>
                  <Form.Item name="max_annual_tv_pct" label="开仓最大年化TV%">
                    <InputNumber style={{ width: '100%' }} min={1} max={50} step={1} precision={1}
                      formatter={(v) => v + '%'} parser={(v) => v.replace('%', '')} />
                  </Form.Item>
                </Col>
                <Col xs={24} sm={6}>
                  <Form.Item name="roll_annual_tv_pct" label="换仓年化TV差值阈值%"
                    tooltip="(远期TV - 当前TV) 年化值低于此阈值则换仓">
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
              </Row>
              <Row gutter={16}>
                <Col xs={24} sm={6}>
                  <Form.Item name="quantity" label="买入数量">
                    <InputNumber style={{ width: '100%' }} min={0.01} max={100} step={0.1} precision={2} />
                  </Form.Item>
                </Col>
                <Col xs={24} sm={6}>
                  <Form.Item name="num_strikes" label="扫描行权价数量">
                    <InputNumber style={{ width: '100%' }} min={5} max={30} step={5} />
                  </Form.Item>
                </Col>
                <Col xs={24} sm={6}>
                  <Form.Item name="open_interval_days" label="检查间隔(天)">
                    <InputNumber style={{ width: '100%' }} min={7} max={90} step={7} />
                  </Form.Item>
                </Col>
              </Row>

              <Form.Item>
                <Space>
                  <Button type="primary" htmlType="submit" disabled={loading}
                    icon={<SwapOutlined />} size="large"
                    style={{ background: '#722ed1', borderColor: '#722ed1', minWidth: 300 }}>
                    {loading ? '回测中...' : '开始LEAPS终极2.0回测'}
                  </Button>
                  {loading && (
                    <Button danger icon={<StopOutlined />} size="large" onClick={handleStop}>
                      取消
                    </Button>
                  )}
                </Space>
              </Form.Item>
            </Form>
          </Card>

          {loading && streamProgress && (
            <Card style={{ marginTop: 16 }}>
              <Space direction="vertical" style={{ width: '100%' }}>
                <Text>{streamProgress.status || '计算中'}...</Text>
                <Progress percent={streamProgress.pct || 0} status="active" />
                <Text type="secondary">
                  第 {streamProgress.day || 0} / {streamProgress.total || '?'} 天
                  {streamProgress.date && ` — ${streamProgress.date}`}
                </Text>
              </Space>
            </Card>
          )}

          {result && (
            <>
              <Card title={
                <Space>
                  回测摘要
                  <Tag color={result.summary?.use_real_data ? 'green' : 'orange'}>
                    {result.summary?.use_real_data ? 'Deribit真实数据' : '模拟IV'}
                  </Tag>
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
                  <Col xs={12} sm={4}>
                    <Statistic title="年化收益率" value={result.summary.annualized_return_pct} suffix="%" precision={2}
                      valueStyle={{ color: result.summary.annualized_return_pct >= 0 ? '#389e0d' : '#cf1322' }} />
                  </Col>
                  <Col xs={12} sm={4}>
                    <Statistic title="最大回撤" value={result.summary.max_drawdown_pct} suffix="%" precision={2}
                      valueStyle={{ color: '#cf1322' }} />
                  </Col>
                  <Col xs={8} sm={4}>
                    <Statistic title="买入次数" value={result.summary.open_count} />
                  </Col>
                  <Col xs={8} sm={4}>
                    <Statistic title="平仓次数" value={result.summary.close_count} />
                  </Col>
                  <Col xs={8} sm={4}>
                    <Statistic title="换仓次数" value={result.summary.roll_count || 0}
                      valueStyle={{ color: '#722ed1', fontWeight: 700 }} />
                  </Col>
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
                      stroke="#722ed1" fill="#722ed120" strokeWidth={2} />
                    <Line yAxisId="equity" type="monotone" dataKey="holdings" name="持仓市值"
                      stroke="#52c41a" dot={false} strokeWidth={1} strokeDasharray="3 3" />
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
                  scroll={{ x: 1500 }}
                />
              </Card>

              {result.scan_logs && result.scan_logs.length > 0 && (
                <Card title={
                  <Space><SearchOutlined /><span>扫描日志 ({result.scan_logs.length}次)</span></Space>
                } style={{ marginTop: 16 }}>
                  <Collapse accordion>
                    {result.scan_logs.map((log, idx) => {
                      const hasRoll = log.roll_candidates && log.roll_candidates.length > 0;
                      const label = (
                        <Space>
                          <Tag color="blue">{log.date}</Tag>
                          <Text>spot=${log.spot?.toLocaleString()}</Text>
                          {log.expiry && <Text type="secondary">expiry={log.expiry}</Text>}
                          {log.selected_strike && <Tag color="green">选中 K=${log.selected_strike?.toLocaleString()}</Tag>}
                          {hasRoll && <Tag color="purple">换仓扫描</Tag>}
                          {!log.selected_strike && !hasRoll && <Tag color="orange">{log.result}</Tag>}
                          {log.result && log.result.includes('换仓') && <Tag color="purple">{log.result}</Tag>}
                          {log.result && !log.result.includes('换仓') && !log.selected_strike && !hasRoll && null}
                        </Space>
                      );

                      return (
                        <Collapse.Panel header={label} key={idx}>
                          {log.candidates && log.candidates.length > 0 && (
                            <>
                              <Text strong>开仓扫描 — 到期日: {log.expiry} ({log.days_to_expiry}天)</Text>
                              <Table
                                columns={scanCols}
                                dataSource={log.candidates.map((c, j) => ({ ...c, key: j }))}
                                size="small" pagination={false}
                                scroll={{ x: 1100 }}
                                style={{ marginTop: 8, marginBottom: 16 }}
                                rowClassName={(r) => r.selected ? 'ant-table-row-selected' : ''}
                              />
                            </>
                          )}
                          {log.candidates2 && log.candidates2.length > 0 && (
                            <>
                              <Text strong>开仓扫描 — 到期日2: {log.expiry2} ({log.days_to_expiry2}天)</Text>
                              <Table
                                columns={scanCols}
                                dataSource={log.candidates2.map((c, j) => ({ ...c, key: 'b' + j }))}
                                size="small" pagination={false}
                                scroll={{ x: 1100 }}
                                style={{ marginTop: 8, marginBottom: 16 }}
                                rowClassName={(r) => r.selected ? 'ant-table-row-selected' : ''}
                              />
                            </>
                          )}
                          {hasRoll && (
                            <>
                              <Divider />
                              <Text strong style={{ color: '#722ed1' }}>
                                <SwapOutlined /> 换仓扫描（远期合约候选）
                              </Text>
                              <Table
                                columns={rollCols}
                                dataSource={log.roll_candidates.map((c, j) => ({ ...c, key: 'r' + j }))}
                                size="small" pagination={false}
                                scroll={{ x: 1200 }}
                                style={{ marginTop: 8 }}
                                rowClassName={(r) => r.selected ? 'ant-table-row-selected' : ''}
                              />
                            </>
                          )}
                          {log.result && (
                            <div style={{ marginTop: 8 }}>
                              <Tag color={log.result.includes('换仓') ? 'purple' : 'blue'}>{log.result}</Tag>
                            </div>
                          )}
                        </Collapse.Panel>
                      );
                    })}
                  </Collapse>
                </Card>
              )}
            </>
          )}
        </div>
      </Content>

      <Footer style={{ textAlign: 'center', background: '#001529', color: 'white' }}>
        期权风险监控系统 ©2024 | LEAPS终极2.0 策略（含换仓）
      </Footer>
    </Layout>
  );
}
