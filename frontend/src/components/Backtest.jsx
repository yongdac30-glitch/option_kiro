/**
 * Options Strategy Backtest Page
 */
import { useState, useEffect } from 'react';
import {
  Layout, Card, Form, Select, InputNumber, DatePicker, Button,
  Row, Col, Typography, Space, Table, Tag, Statistic, Divider, message, Spin,
  Modal, Input, Popconfirm,
} from 'antd';
import {
  ArrowLeftOutlined, ExperimentOutlined, PlusOutlined, DeleteOutlined,
  LineChartOutlined, SaveOutlined, FolderOpenOutlined, DatabaseOutlined,
} from '@ant-design/icons';
import { Link } from 'react-router-dom';
import dayjs from 'dayjs';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer, ReferenceLine } from 'recharts';
import { backtestService } from '../services/backtestService';

const { Header: AntHeader, Content, Footer } = Layout;
const { Title, Text } = Typography;
const { Option } = Select;
const { RangePicker } = DatePicker;

const DEFAULT_LEGS = [
  { key: 1, option_type: 'PUT', strike_offset_pct: -0.20, quantity: -1, expiry_months: 1, iv: 0.6 },
  { key: 2, option_type: 'PUT', strike_offset_pct: -0.25, quantity: 2, expiry_months: 1, iv: 0.6 },
];

const STORAGE_KEY = 'backtest_strategies';

const loadStrategiesFromStorage = () => {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]');
  } catch { return []; }
};

const saveStrategiesToStorage = (list) => {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(list));
};

export default function Backtest() {
  const [form] = Form.useForm();
  const [legs, setLegs] = useState(DEFAULT_LEGS);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [nextKey, setNextKey] = useState(3);
  const [savedStrategies, setSavedStrategies] = useState([]);
  const [saveModalOpen, setSaveModalOpen] = useState(false);
  const [strategyName, setStrategyName] = useState('');

  useEffect(() => {
    setSavedStrategies(loadStrategiesFromStorage());
  }, []);

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
      risk_free_rate: values.risk_free_rate,
      roll_day: values.roll_day,
      close_days_before: values.close_days_before,
      initial_capital: values.initial_capital,
      contract_multiplier: values.contract_multiplier,
      legs: legs.map(({ key, ...rest }) => rest),
      saved_at: new Date().toISOString(),
    };
    const existing = loadStrategiesFromStorage();
    const idx = existing.findIndex((s) => s.name === name);
    if (idx >= 0) { existing[idx] = preset; } else { existing.push(preset); }
    saveStrategiesToStorage(existing);
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
        ? [dayjs(preset.start_date), dayjs(preset.end_date)]
        : undefined,
      risk_free_rate: preset.risk_free_rate,
      roll_day: preset.roll_day,
      close_days_before: preset.close_days_before,
      initial_capital: preset.initial_capital,
      contract_multiplier: preset.contract_multiplier,
    });
    const restoredLegs = (preset.legs || []).map((l, i) => ({ ...l, key: i + 1 }));
    setLegs(restoredLegs);
    setNextKey(restoredLegs.length + 1);
    message.success(`已加载策略: ${name}`);
  };

  const handleDeleteStrategy = (name) => {
    const updated = savedStrategies.filter((s) => s.name !== name);
    saveStrategiesToStorage(updated);
    setSavedStrategies(updated);
    message.success('策略已删除');
  };

  const addLeg = () => {
    setLegs([...legs, { key: nextKey, option_type: 'PUT', strike_offset_pct: -0.10, quantity: -1, expiry_months: 1, iv: 0.6 }]);
    setNextKey(nextKey + 1);
  };

  const removeLeg = (key) => {
    setLegs(legs.filter((l) => l.key !== key));
  };

  const updateLeg = (key, field, value) => {
    setLegs(legs.map((l) => l.key === key ? { ...l, [field]: value } : l));
  };

  const handleRun = async (values) => {
    if (legs.length === 0) {
      message.error('请至少添加一个策略腿');
      return;
    }
    setLoading(true);
    setResult(null);
    try {
      const [startDate, endDate] = values.date_range;
      const params = {
        underlying: values.underlying,
        start_date: startDate.format('YYYY-MM-DD'),
        end_date: endDate.format('YYYY-MM-DD'),
        risk_free_rate: values.risk_free_rate,
        roll_day: values.roll_day,
        close_days_before_expiry: values.close_days_before,
        initial_capital: values.initial_capital,
        contract_multiplier: values.contract_multiplier,
        legs: legs.map(({ key, ...rest }) => rest),
      };
      const data = await backtestService.run(params);
      setResult(data);
      message.success('回测完成');
    } catch (error) {
      const detail = error.response?.data?.detail;
      message.error(detail || '回测失败');
      console.error(error);
    } finally {
      setLoading(false);
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
    { title: '平仓原因', dataIndex: 'close_reason', key: 'close_reason', width: 140 },
  ];

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <AntHeader style={{ background: '#001529', padding: '0 24px', display: 'flex', alignItems: 'center' }}>
        <Link to="/" style={{ display: 'flex', alignItems: 'center', marginRight: 16 }}>
          <ArrowLeftOutlined style={{ fontSize: 18, color: '#1890ff' }} />
        </Link>
        <ExperimentOutlined style={{ fontSize: 24, color: '#1890ff', marginRight: 12 }} />
        <Title level={3} style={{ color: 'white', margin: 0, fontSize: 20 }}>期权策略回测</Title>
        <Link to="/real-backtest" style={{ marginLeft: 'auto', color: '#52c41a', fontSize: 14, display: 'flex', alignItems: 'center', gap: 6 }}>
          <DatabaseOutlined />
          真实数据回测
        </Link>
      </AntHeader>

      <Content style={{ padding: 24, background: '#f0f2f5' }}>
        <div style={{ maxWidth: 1400, margin: '0 auto' }}>
          {/* Strategy Configuration */}
          <Card
            title="回测参数配置"
            extra={
              <Space>
                {savedStrategies.length > 0 && (
                  <Select
                    placeholder="加载已保存策略"
                    style={{ width: 200 }}
                    value={null}
                    onChange={handleLoadStrategy}
                    suffixIcon={<FolderOpenOutlined />}
                    optionRender={(option) => (
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <span>{option.label}</span>
                        <Popconfirm title="确定删除此策略？" onConfirm={(e) => { e.stopPropagation(); handleDeleteStrategy(option.value); }} onCancel={(e) => e.stopPropagation()}>
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
            title="保存策略"
            open={saveModalOpen}
            onOk={handleSaveStrategy}
            onCancel={() => { setSaveModalOpen(false); setStrategyName(''); }}
            okText="保存"
            cancelText="取消"
          >
            <Form.Item label="策略名称" style={{ marginBottom: 0, marginTop: 16 }}>
              <Input
                value={strategyName}
                onChange={(e) => setStrategyName(e.target.value)}
                placeholder="输入策略名称"
                onPressEnter={handleSaveStrategy}
              />
            </Form.Item>
            {savedStrategies.some((s) => s.name === strategyName.trim()) && (
              <Text type="warning" style={{ fontSize: 12 }}>同名策略已存在，保存将覆盖</Text>
            )}
          </Modal>
            <Form
              form={form}
              layout="vertical"
              onFinish={handleRun}
              initialValues={{
                underlying: 'BTC-USD',
                risk_free_rate: 0.05,
                roll_day: 1,
                close_days_before: 1,
                initial_capital: 10000,
                contract_multiplier: 0.01,
                date_range: [dayjs('2024-01-01'), dayjs('2025-12-31')],
              }}
            >
              <Row gutter={16}>
                <Col xs={24} sm={8}>
                  <Form.Item name="underlying" label="标的" rules={[{ required: true }]}>
                    <Select>
                      <Option value="BTC-USD">BTC</Option>
                      <Option value="ETH-USD">ETH</Option>
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
                  <Form.Item name="risk_free_rate" label="无风险利率">
                    <InputNumber style={{ width: '100%' }} min={0} max={0.5} step={0.01} precision={2} />
                  </Form.Item>
                </Col>
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

              <Divider orientation="left">策略腿配置</Divider>

              {legs.map((leg, idx) => (
                <Row key={leg.key} gutter={12} align="middle" style={{ marginBottom: 8 }}>
                  <Col>
                    <Tag color="blue">腿 {idx + 1}</Tag>
                  </Col>
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
                        size="small" style={{ width: 80 }}
                        step={1} precision={0}
                        formatter={(v) => v + '%'}
                        parser={(v) => v.replace('%', '')}
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
                    <Space size={4}>
                      <Text style={{ fontSize: 12, color: '#888' }}>IV:</Text>
                      <InputNumber
                        value={leg.iv}
                        onChange={(v) => updateLeg(leg.key, 'iv', v)}
                        size="small" style={{ width: 80 }}
                        min={0.01} max={5} step={0.05} precision={2}
                      />
                    </Space>
                  </Col>
                  <Col>
                    <Button type="link" danger size="small" icon={<DeleteOutlined />} onClick={() => removeLeg(leg.key)} disabled={legs.length <= 1} />
                  </Col>
                </Row>
              ))}

              <Button type="dashed" onClick={addLeg} icon={<PlusOutlined />} size="small" style={{ marginBottom: 16 }}>
                添加策略腿
              </Button>

              <Form.Item>
                <Button type="primary" htmlType="submit" loading={loading} icon={<ExperimentOutlined />} size="large" block>
                  开始回测
                </Button>
              </Form.Item>
            </Form>
          </Card>

          {/* Results */}
          {loading && (
            <Card style={{ marginTop: 16, textAlign: 'center' }}>
              <Spin tip="正在回测中，请稍候..." size="large" />
            </Card>
          )}

          {result && (
            <>
              {/* Summary */}
              <Card title="回测摘要" style={{ marginTop: 16 }}>
                <Row gutter={[24, 16]}>
                  <Col xs={12} sm={6}>
                    <Statistic title="初始资金" value={result.summary.initial_capital} prefix="$" precision={0} />
                  </Col>
                  <Col xs={12} sm={6}>
                    <Statistic
                      title="最终权益"
                      value={result.summary.final_equity}
                      prefix="$"
                      precision={2}
                      valueStyle={{ color: result.summary.total_pnl >= 0 ? '#389e0d' : '#cf1322' }}
                    />
                  </Col>
                  <Col xs={12} sm={6}>
                    <Statistic
                      title="总收益"
                      value={result.summary.total_pnl}
                      prefix="$"
                      precision={2}
                      valueStyle={{ color: result.summary.total_pnl >= 0 ? '#389e0d' : '#cf1322' }}
                    />
                  </Col>
                  <Col xs={12} sm={6}>
                    <Statistic
                      title="总收益率"
                      value={result.summary.total_return_pct}
                      suffix="%"
                      precision={2}
                      valueStyle={{ color: result.summary.total_return_pct >= 0 ? '#389e0d' : '#cf1322' }}
                    />
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
                    <Statistic title="平均盈亏" value={result.summary.avg_pnl} prefix="$" precision={2} />
                  </Col>
                </Row>
              </Card>

              {/* Equity Curve */}
              <Card title={<Space><LineChartOutlined /><span>资金曲线</span></Space>} style={{ marginTop: 16 }}>
                <ResponsiveContainer width="100%" height={400}>
                  <LineChart data={result.equity_curve}>
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis
                      dataKey="date"
                      tick={{ fontSize: 11 }}
                      interval="preserveStartEnd"
                      tickFormatter={(v) => v.substring(5)}
                    />
                    <YAxis yAxisId="equity" tick={{ fontSize: 11 }} domain={['auto', 'auto']} />
                    <YAxis yAxisId="spot" orientation="right" tick={{ fontSize: 11 }} domain={['auto', 'auto']} />
                    <Tooltip
                      formatter={(value, name) => {
                        if (name === '权益') return ['$' + value.toLocaleString(), name];
                        if (name === '标的价格') return ['$' + value.toLocaleString(), name];
                        return [value, name];
                      }}
                    />
                    <Legend />
                    <ReferenceLine yAxisId="equity" y={result.summary.initial_capital} stroke="#999" strokeDasharray="5 5" label="初始资金" />
                    <Line yAxisId="equity" type="monotone" dataKey="equity" name="权益" stroke="#1890ff" dot={false} strokeWidth={2} />
                    <Line yAxisId="spot" type="monotone" dataKey="spot" name="标的价格" stroke="#faad14" dot={false} strokeWidth={1} opacity={0.6} />
                  </LineChart>
                </ResponsiveContainer>
              </Card>

              {/* Trade History */}
              <Card title="交易记录" style={{ marginTop: 16 }}>
                <Table
                  columns={tradeColumns}
                  dataSource={result.trades.map((t, i) => ({ ...t, key: i }))}
                  size="small"
                  pagination={{ pageSize: 20, showSizeChanger: true, showTotal: (t) => '共 ' + t + ' 条' }}
                  scroll={{ x: 900 }}
                />
              </Card>
            </>
          )}
        </div>
      </Content>

      <Footer style={{ textAlign: 'center', background: '#001529', color: 'white' }}>
        期权风险监控系统 ©2024 | 策略回测
      </Footer>
    </Layout>
  );
}
