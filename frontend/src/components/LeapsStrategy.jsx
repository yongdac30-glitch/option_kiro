/**
 * LEAPS Rolling Strategy ("无限续杯") Backtest Page
 *
 * Strategy: Buy deep ITM LEAPS calls, then mechanically:
 * - Roll Out when approaching expiry (续命)
 * - Roll Up when underlying rallies (提款)
 * - Add on Dip when underlying drops (逢跌加仓)
 * Goal: reduce cost basis to zero or negative.
 *
 * Supports two modes:
 * - Simulated: fixed IV + OKX prices (fast)
 * - Real Data: Deribit IV smile interpolation (slow, SSE streaming)
 */
import { useState, useRef } from 'react';
import {
  Layout, Card, Form, Select, InputNumber, DatePicker, Button,
  Row, Col, Typography, Space, Table, Tag, Statistic, Divider,
  message, Spin, Tooltip as AntTooltip, Alert, Switch, Progress,
} from 'antd';
import {
  ArrowLeftOutlined, RocketOutlined, InfoCircleOutlined,
  LineChartOutlined, RiseOutlined, FallOutlined, SwapOutlined,
  DatabaseOutlined, ExperimentOutlined, StopOutlined,
} from '@ant-design/icons';
import { Link } from 'react-router-dom';
import dayjs from 'dayjs';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  Legend, ResponsiveContainer, ReferenceLine, Area, ComposedChart,
} from 'recharts';
import { leapsService } from '../services/leapsService';

const { Header: AntHeader, Content, Footer } = Layout;
const { Title, Text } = Typography;
const { Option } = Select;
const { RangePicker } = DatePicker;

const ACTION_COLORS = {
  OPEN: '#1890ff',
  ROLL_OUT: '#faad14',
  ROLL_UP: '#52c41a',
  ADD_DIP: '#722ed1',
  CLOSE: '#8c8c8c',
};

const ACTION_LABELS = {
  OPEN: '建仓',
  ROLL_OUT: '续命(Roll Out)',
  ROLL_UP: '提款(Roll Up)',
  ADD_DIP: '逢跌加仓',
  CLOSE: '平仓',
};

export default function LeapsStrategy() {
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [useRealData, setUseRealData] = useState(false);
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

    const [startDate, endDate] = values.date_range;
    const params = {
      underlying: values.underlying,
      start_date: startDate.format('YYYY-MM-DD'),
      end_date: endDate.format('YYYY-MM-DD'),
      initial_capital: values.initial_capital,
      contract_multiplier: values.contract_multiplier,
      risk_free_rate: values.risk_free_rate,
      leaps_delta_target: values.leaps_delta_target,
      leaps_expiry_months: values.leaps_expiry_months,
      iv: values.iv,
      roll_out_dte: values.roll_out_dte,
      roll_up_pct: values.roll_up_pct / 100,
      add_on_dip_pct: -Math.abs(values.add_on_dip_pct) / 100,
      max_positions: values.max_positions,
      position_size_pct: values.position_size_pct / 100,
      cooldown_days: values.cooldown_days,
      use_real_data: useRealData,
    };

    if (useRealData) {
      // SSE streaming mode
      try {
        abortRef.current = leapsService.realBacktestStream(
          params,
          (progress) => setStreamProgress(progress),
          (data) => {
            setResult(data);
            setLoading(false);
            setStreamProgress(null);
            abortRef.current = null;
            message.success('LEAPS真实数据回测完成');
          },
          (errMsg) => {
            setLoading(false);
            setStreamProgress(null);
            abortRef.current = null;
            message.error(errMsg || 'LEAPS真实数据回测失败');
          },
        );
      } catch (error) {
        setLoading(false);
        message.error('启动流式回测失败');
      }
    } else {
      // Normal mode
      try {
        const data = await leapsService.backtest(params);
        setResult(data);
        message.success('LEAPS回测完成');
      } catch (error) {
        const detail = error.response?.data?.detail;
        message.error(detail || 'LEAPS回测失败');
      } finally {
        setLoading(false);
      }
    }
  };

  const tradeColumns = [
    { title: '日期', dataIndex: 'date', key: 'date', width: 100 },
    {
      title: '操作', dataIndex: 'action', key: 'action', width: 130,
      render: (a) => <Tag color={ACTION_COLORS[a]}>{ACTION_LABELS[a] || a}</Tag>,
    },
    { title: '行权价', dataIndex: 'strike', key: 'strike', width: 100, render: (v) => '$' + v?.toLocaleString() },
    { title: '到期日', dataIndex: 'expiry', key: 'expiry', width: 100 },
    { title: '标的价', dataIndex: 'spot', key: 'spot', width: 100, render: (v) => '$' + v?.toLocaleString() },
    { title: '期权价', dataIndex: 'option_price', key: 'option_price', width: 100, render: (v) => '$' + v?.toFixed(2) },
    { title: '数量', dataIndex: 'quantity', key: 'quantity', width: 80 },
    { title: '总数量', dataIndex: 'total_quantity', key: 'total_quantity', width: 90,
      render: (v) => v != null ? v.toFixed(4) : '-',
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
      title: '成本基础', dataIndex: 'cost_basis', key: 'cost_basis', width: 110,
      render: (v) => (
        <span style={{ color: v <= 0 ? '#389e0d' : '#595959', fontWeight: v <= 0 ? 700 : 400 }}>
          ${v?.toFixed(2)}
        </span>
      ),
    },
    {
      title: '数据源', dataIndex: 'data_source', key: 'data_source', width: 90,
      render: (v) => v ? (
        <Tag color={v === 'iv_smile' ? 'green' : 'orange'}>{v === 'iv_smile' ? 'IV曲线' : '模型'}</Tag>
      ) : <Tag>模拟</Tag>,
    },
    {
      title: 'IV', dataIndex: 'iv_used', key: 'iv_used', width: 70,
      render: (v) => v ? `${(v * 100).toFixed(1)}%` : '-',
    },
    { title: '备注', dataIndex: 'note', key: 'note', ellipsis: true },
  ];

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <AntHeader style={{ background: '#001529', padding: '0 24px', display: 'flex', alignItems: 'center' }}>
        <Link to="/" style={{ display: 'flex', alignItems: 'center', marginRight: 16 }}>
          <ArrowLeftOutlined style={{ fontSize: 18, color: '#1890ff' }} />
        </Link>
        <RocketOutlined style={{ fontSize: 24, color: '#52c41a', marginRight: 12 }} />
        <Title level={3} style={{ color: 'white', margin: 0, fontSize: 20 }}>
          LEAPS "无限续杯" 策略回测
        </Title>
      </AntHeader>

      <Content style={{ padding: 24, background: '#f0f2f5' }}>
        <div style={{ maxWidth: 1400, margin: '0 auto' }}>
          {/* Strategy explanation */}
          <Alert
            type="info"
            showIcon
            icon={<InfoCircleOutlined />}
            style={{ marginBottom: 16 }}
            message="LEAPS Rolling 策略说明"
            description={
              <div style={{ fontSize: 13 }}>
                <p style={{ margin: '4px 0' }}>买入深度实值(高Delta)的LEAPS长期看涨期权，通过机械化操作逐步将持仓成本降至零或负数：</p>
                <Space direction="vertical" size={2}>
                  <Text><SwapOutlined style={{ color: '#faad14' }} /> Roll Out(续命)：到期前滚动到更远到期日</Text>
                  <Text><RiseOutlined style={{ color: '#52c41a' }} /> Roll Up(提款)：标的上涨时提高行权价，提取利润</Text>
                  <Text><FallOutlined style={{ color: '#722ed1' }} /> 逢跌加仓：标的下跌时在低位加仓摊低成本</Text>
                </Space>
              </div>
            }
          />

          {/* Configuration */}
          <Card title={
            <Space>
              <span>策略参数配置</span>
              <Divider type="vertical" />
              <AntTooltip title="开启后使用Deribit真实IV微笑曲线定价，数据会缓存到本地数据库。首次运行较慢。">
                <Space>
                  <DatabaseOutlined style={{ color: useRealData ? '#52c41a' : '#999' }} />
                  <Switch
                    checked={useRealData}
                    onChange={setUseRealData}
                    checkedChildren="真实数据"
                    unCheckedChildren="模拟IV"
                    disabled={loading}
                  />
                  {useRealData && <Tag color="green">Deribit IV Smile</Tag>}
                </Space>
              </AntTooltip>
            </Space>
          }>
            <Form
              form={form}
              layout="vertical"
              onFinish={handleRun}
              initialValues={{
                underlying: 'BTC-USD',
                date_range: [dayjs('2021-01-01'), dayjs('2025-12-31')],
                initial_capital: 100000,
                contract_multiplier: 0.01,
                risk_free_rate: 0.05,
                leaps_delta_target: 0.80,
                leaps_expiry_months: 12,
                iv: 0.6,
                roll_out_dte: 60,
                roll_up_pct: 20,
                add_on_dip_pct: 15,
                max_positions: 5,
                position_size_pct: 20,
                cooldown_days: 5,
              }}
            >
              <Divider orientation="left" plain>基础参数</Divider>
              <Row gutter={16}>
                <Col xs={24} sm={6}>
                  <Form.Item name="underlying" label="标的" rules={[{ required: true }]}>
                    <Select>
                      <Option value="BTC-USD">BTC</Option>
                      <Option value="ETH-USD">ETH</Option>
                    </Select>
                  </Form.Item>
                </Col>
                <Col xs={24} sm={6}>
                  <Form.Item name="date_range" label="回测时间段" rules={[{ required: true }]}>
                    <RangePicker style={{ width: '100%' }} format="YYYY-MM-DD" />
                  </Form.Item>
                </Col>
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

              <Divider orientation="left" plain>LEAPS 参数</Divider>
              <Row gutter={16}>
                <Col xs={24} sm={6}>
                  <Form.Item name="leaps_delta_target" label={
                    <AntTooltip title="深度实值Call的Delta目标，0.8表示约80 Delta">
                      <Space>目标 Delta <InfoCircleOutlined /></Space>
                    </AntTooltip>
                  }>
                    <InputNumber style={{ width: '100%' }} min={0.5} max={0.95} step={0.05} precision={2} />
                  </Form.Item>
                </Col>
                <Col xs={24} sm={6}>
                  <Form.Item name="leaps_expiry_months" label="LEAPS到期月数">
                    <InputNumber style={{ width: '100%' }} min={6} max={36} step={3} />
                  </Form.Item>
                </Col>
                <Col xs={24} sm={6}>
                  <Form.Item name="iv" label={
                    <AntTooltip title={useRealData ? "真实数据模式下仅用于Delta估算和MTM回退" : "模拟模式下用于所有BS定价"}>
                      <Space>{useRealData ? '回退IV' : '假设IV'} <InfoCircleOutlined /></Space>
                    </AntTooltip>
                  }>
                    <InputNumber style={{ width: '100%' }} min={0.1} max={3} step={0.05} precision={2} />
                  </Form.Item>
                </Col>
                <Col xs={24} sm={6}>
                  <Form.Item name="risk_free_rate" label="无风险利率">
                    <InputNumber style={{ width: '100%' }} min={0} max={0.2} step={0.01} precision={2} />
                  </Form.Item>
                </Col>
              </Row>

              <Divider orientation="left" plain>触发条件</Divider>
              <Row gutter={16}>
                <Col xs={24} sm={6}>
                  <Form.Item name="roll_out_dte" label={
                    <AntTooltip title="当剩余天数≤此值时，自动Roll Out续命">
                      <Space>Roll Out触发(DTE) <InfoCircleOutlined /></Space>
                    </AntTooltip>
                  }>
                    <InputNumber style={{ width: '100%' }} min={14} max={180} step={7} />
                  </Form.Item>
                </Col>
                <Col xs={24} sm={6}>
                  <Form.Item name="roll_up_pct" label={
                    <AntTooltip title="标的从建仓价上涨超过此比例时Roll Up提取利润">
                      <Space>Roll Up触发涨幅(%) <InfoCircleOutlined /></Space>
                    </AntTooltip>
                  }>
                    <InputNumber style={{ width: '100%' }} min={5} max={100} step={5}
                      formatter={(v) => v + '%'} parser={(v) => v.replace('%', '')} />
                  </Form.Item>
                </Col>
                <Col xs={24} sm={6}>
                  <Form.Item name="add_on_dip_pct" label={
                    <AntTooltip title="标的从最高建仓价下跌超过此比例时逢跌加仓">
                      <Space>加仓触发跌幅(%) <InfoCircleOutlined /></Space>
                    </AntTooltip>
                  }>
                    <InputNumber style={{ width: '100%' }} min={5} max={50} step={5}
                      formatter={(v) => v + '%'} parser={(v) => v.replace('%', '')} />
                  </Form.Item>
                </Col>
                <Col xs={24} sm={6}>
                  <Form.Item name="max_positions" label="最大持仓数">
                    <InputNumber style={{ width: '100%' }} min={1} max={20} />
                  </Form.Item>
                </Col>
              </Row>

              <Row gutter={16}>
                <Col xs={24} sm={6}>
                  <Form.Item name="position_size_pct" label="每次开仓占比(%)">
                    <InputNumber style={{ width: '100%' }} min={5} max={100} step={5}
                      formatter={(v) => v + '%'} parser={(v) => v.replace('%', '')} />
                  </Form.Item>
                </Col>
                <Col xs={24} sm={6}>
                  <Form.Item name="cooldown_days" label={
                    <AntTooltip title="两次操作之间的最小间隔天数，避免频繁交易">
                      <Space>操作冷却期(天) <InfoCircleOutlined /></Space>
                    </AntTooltip>
                  }>
                    <InputNumber style={{ width: '100%' }} min={0} max={30} step={1} />
                  </Form.Item>
                </Col>
              </Row>

              <Form.Item>
                <Space style={{ width: '100%' }}>
                  <Button type="primary" htmlType="submit" loading={loading && !useRealData}
                    disabled={loading}
                    icon={useRealData ? <DatabaseOutlined /> : <ExperimentOutlined />}
                    size="large"
                    style={{
                      background: useRealData ? '#1890ff' : '#52c41a',
                      borderColor: useRealData ? '#1890ff' : '#52c41a',
                      minWidth: 300,
                    }}>
                    {useRealData ? '开始真实数据回测 (Deribit)' : '开始模拟回测'}
                  </Button>
                  {loading && useRealData && (
                    <Button danger icon={<StopOutlined />} size="large" onClick={handleStop}>
                      取消
                    </Button>
                  )}
                </Space>
              </Form.Item>
            </Form>
          </Card>

          {/* Streaming progress */}
          {loading && useRealData && streamProgress && (
            <Card style={{ marginTop: 16 }}>
              <Space direction="vertical" style={{ width: '100%' }}>
                <Text>
                  {streamProgress.status === '计算中'
                    ? '正在回测计算...'
                    : `正在${streamProgress.status || '处理'}...`}
                </Text>
                <Progress
                  percent={streamProgress.pct || 0}
                  status="active"
                  format={(pct) => `${pct}%`}
                />
                <Text type="secondary">
                  第 {streamProgress.day || 0} / {streamProgress.total || '?'} 天
                  {streamProgress.date && ` — ${streamProgress.date}`}
                </Text>
              </Space>
            </Card>
          )}

          {loading && !useRealData && (
            <Card style={{ marginTop: 16, textAlign: 'center' }}>
              <Spin tip="正在运行LEAPS策略回测..." size="large" />
            </Card>
          )}

          {result && (
            <>
              {/* Summary */}
              <Card title={
                <Space>
                  回测摘要
                  {result.summary?.data_mode === 'real' && <Tag color="green">真实数据</Tag>}
                  {result.summary?.data_mode !== 'real' && <Tag>模拟IV</Tag>}
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
                  <Col xs={12} sm={6}>
                    <Statistic title="年化收益率" value={result.summary.annualized_return_pct} suffix="%" precision={2}
                      valueStyle={{ color: result.summary.annualized_return_pct >= 0 ? '#389e0d' : '#cf1322' }} />
                  </Col>
                </Row>
                <Divider />
                <Row gutter={[24, 16]}>
                  <Col xs={12} sm={6}>
                    <Statistic title="最大回撤" value={result.summary.max_drawdown_pct} suffix="%" precision={2}
                      valueStyle={{ color: '#cf1322' }} />
                  </Col>
                  <Col xs={12} sm={6}>
                    <Statistic title="总投入" value={result.summary.total_invested} prefix="$" precision={2} />
                  </Col>
                  <Col xs={12} sm={6}>
                    <Statistic title="总提取" value={result.summary.total_extracted} prefix="$" precision={2}
                      valueStyle={{ color: '#389e0d' }} />
                  </Col>
                  <Col xs={12} sm={6}>
                    <Statistic
                      title={<Space>最终成本基础 {result.summary.cost_basis_negative &&
                        <Tag color="green">已归零</Tag>}</Space>}
                      value={result.summary.final_cost_basis} prefix="$" precision={2}
                      valueStyle={{ color: result.summary.final_cost_basis <= 0 ? '#389e0d' : '#595959' }} />
                  </Col>
                </Row>
                <Divider />
                <Row gutter={[24, 16]}>
                  <Col xs={12} sm={6}>
                    <Statistic title="总交易次数" value={result.summary.total_trades} />
                  </Col>
                  <Col xs={12} sm={6}>
                    <Statistic title="Roll Out次数" value={result.summary.roll_out_count}
                      valueStyle={{ color: '#faad14' }} />
                  </Col>
                  <Col xs={12} sm={6}>
                    <Statistic title="Roll Up次数" value={result.summary.roll_up_count}
                      valueStyle={{ color: '#52c41a' }} />
                  </Col>
                  <Col xs={12} sm={6}>
                    <Statistic title="逢跌加仓次数" value={result.summary.add_dip_count}
                      valueStyle={{ color: '#722ed1' }} />
                  </Col>
                </Row>
              </Card>

              {/* Equity + Cost Basis Chart */}
              <Card title={<Space><LineChartOutlined /><span>资金曲线 & 成本基础</span></Space>}
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
                    <ReferenceLine yAxisId="equity" y={0} stroke="#ff4d4f" strokeDasharray="3 3" />
                    <Area yAxisId="equity" type="monotone" dataKey="equity" name="权益"
                      stroke="#1890ff" fill="#1890ff20" strokeWidth={2} />
                    <Line yAxisId="equity" type="monotone" dataKey="cost_basis" name="成本基础"
                      stroke="#ff4d4f" dot={false} strokeWidth={2} strokeDasharray="5 5" />
                    <Line yAxisId="spot" type="monotone" dataKey="spot" name="标的价格"
                      stroke="#faad14" dot={false} strokeWidth={1} opacity={0.5} />
                  </ComposedChart>
                </ResponsiveContainer>
              </Card>

              {/* Trade History */}
              <Card title="交易记录" style={{ marginTop: 16 }}>
                <Table
                  columns={tradeColumns}
                  dataSource={result.trades.map((t, i) => ({ ...t, key: i }))}
                  size="small"
                  pagination={{ pageSize: 20, showSizeChanger: true, showTotal: (t) => `共 ${t} 条` }}
                  scroll={{ x: 1400 }}
                />
              </Card>
            </>
          )}
        </div>
      </Content>

      <Footer style={{ textAlign: 'center', background: '#001529', color: 'white' }}>
        期权风险监控系统 ©2024 | LEAPS "无限续杯" 策略
      </Footer>
    </Layout>
  );
}
