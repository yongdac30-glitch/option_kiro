/**
 * Deribit 数据调试页面
 * 用于测试和收集历史期权数据、IV数据
 */
import { useState, useEffect, useCallback, useRef } from 'react';
import {
  Layout, Card, Form, Select, InputNumber, DatePicker, Button,
  Row, Col, Typography, Space, Table, Tag, Statistic, Divider,
  message, Spin, Tabs, Input, Popconfirm, Alert, Switch,
} from 'antd';
import {
  ArrowLeftOutlined, SearchOutlined, DatabaseOutlined,
  DeleteOutlined, SaveOutlined, CloudDownloadOutlined,
  BugOutlined, ThunderboltOutlined, LineChartOutlined,
  GoldOutlined, DownloadOutlined,
} from '@ant-design/icons';
import { Link } from 'react-router-dom';
import dayjs from 'dayjs';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ResponsiveContainer, ReferenceLine,
} from 'recharts';
import { deribitDebugService } from '../services/deribitDebugService';
import { okxXauService } from '../services/okxXauService';

const { Header: AntHeader, Content, Footer } = Layout;
const { Title, Text } = Typography;
const { Option } = Select;
const { RangePicker } = DatePicker;

// ── Helper: compute last Friday of month in JS ──
function lastFridayOfMonth(year, month) {
  // month is 1-based
  const lastDay = new Date(year, month, 0).getDate(); // last day of month
  const d = new Date(year, month - 1, lastDay);
  while (d.getDay() !== 5) { // 5 = Friday
    d.setDate(d.getDate() - 1);
  }
  return d;
}

function generateExpiryDates(startYear = 2023, endYear = 2026) {
  const MONTH_ABBR = {
    0: 'JAN', 1: 'FEB', 2: 'MAR', 3: 'APR', 4: 'MAY', 5: 'JUN',
    6: 'JUL', 7: 'AUG', 8: 'SEP', 9: 'OCT', 10: 'NOV', 11: 'DEC',
  };
  const results = [];
  for (let y = startYear; y <= endYear; y++) {
    for (let m = 1; m <= 12; m++) {
      const d = lastFridayOfMonth(y, m);
      const iso = d.toISOString().split('T')[0];
      const deribitStr = `${d.getDate()}${MONTH_ABBR[d.getMonth()]}${String(d.getFullYear()).slice(2)}`;
      results.push({ date: iso, label: `${iso} (${deribitStr})`, deribitStr });
    }
  }
  return results;
}

const EXPIRY_DATES = generateExpiryDates(2023, 2026);

export default function DeribitDebug() {
  const [loading, setLoading] = useState(false);
  const [activeTab, setActiveTab] = useState('trade-test');

  // Trade test
  const [tradeResult, setTradeResult] = useState(null);
  const [tradeForm] = Form.useForm();
  const [instrumentPreview, setInstrumentPreview] = useState('');

  // Smile test
  const [smileResult, setSmileResult] = useState(null);
  const [smileForm] = Form.useForm();

  // Batch
  const [batchResult, setBatchResult] = useState(null);

  // Cache
  const [cacheStats, setCacheStats] = useState(null);
  const [cacheData, setCacheData] = useState(null);

  // ATM IV History
  const [atmIvData, setAtmIvData] = useState(null);

  // OKX XAU
  const [xauRunning, setXauRunning] = useState(false);
  const [xauLatest, setXauLatest] = useState(null);
  const [xauHistory, setXauHistory] = useState([]);
  const [xauRecords, setXauRecords] = useState(0);
  const [xauMode, setXauMode] = useState('unknown');
  const [xauDbStats, setXauDbStats] = useState(null);
  const [arbResult, setArbResult] = useState(null);
  const [arbLoading, setArbLoading] = useState(false);

  // ── Instrument name preview for trade test ──
  const updateInstrumentPreview = useCallback(() => {
    const vals = tradeForm.getFieldsValue();
    if (vals.underlying && vals.expiry_date && vals.strike && vals.option_type) {
      const expiry = EXPIRY_DATES.find(e => e.date === vals.expiry_date);
      if (expiry) {
        const suffix = vals.option_type === 'PUT' ? 'P' : 'C';
        const name = `${vals.underlying}-${expiry.deribitStr}-${Math.round(vals.strike)}-${suffix}`;
        setInstrumentPreview(name);
        tradeForm.setFieldValue('instrument', name);
      }
    }
  }, [tradeForm]);

  // ── Trade Test ──
  const handleTradeTest = async (values) => {
    setLoading(true);
    try {
      const params = {
        instrument: values.instrument,
        target_date: values.target_date.format('YYYY-MM-DD'),
        window_days: values.window_days || 5,
      };
      // Pass optional IV calc params from the auto-generate fields
      if (values.strike) params.strike = values.strike;
      if (values.option_type) params.option_type = values.option_type;
      if (values.expiry_date) params.expiry_date = values.expiry_date; // already ISO string from Select
      if (values.spot_override) params.spot_price = values.spot_override;
      const result = await deribitDebugService.testTrades(params);
      setTradeResult(result);
      const tradeCount = result.trade_api?.count || 0;
      const chartCount = result.chart_api?.count || 0;
      if (tradeCount > 0 || chartCount > 0) {
        message.success(`Trade API: ${tradeCount} 笔, Chart API: ${chartCount} 条K线`);
      } else {
        message.warning('两种API均未找到数据');
      }
    } catch (e) {
      message.error('请求失败: ' + (e.response?.data?.detail || e.message));
    } finally {
      setLoading(false);
    }
  };

  // ── Smile Test ──
  const handleSmileTest = async (values) => {
    setLoading(true);
    try {
      const result = await deribitDebugService.testSmile({
        underlying: values.underlying || 'BTC',
        expiry_date: values.expiry_date, // already ISO string from Select
        target_date: values.target_date.format('YYYY-MM-DD'),
        option_type: values.option_type || 'PUT',
        spot_price: values.spot_price || null,
        num_strikes: values.num_strikes || 7,
        window_days: values.window_days || 5,
      });
      setSmileResult(result);
      message.success(`测试完成: ${result.valid_iv_points} 个有效IV点`);
    } catch (e) {
      message.error('请求失败: ' + (e.response?.data?.detail || e.message));
    } finally {
      setLoading(false);
    }
  };

  const handleSaveSmile = async () => {
    if (!smileResult || !smileResult.smile_points.length) {
      message.warning('没有可保存的数据');
      return;
    }
    try {
      const points = smileResult.details
        .filter(d => d.status === 'ok')
        .map(d => ({
          strike: d.strike,
          iv: d.iv_calculated,
          trade_price_usd: d.trade_price_usd,
          instrument: d.instrument,
        }));
      await deribitDebugService.saveSmile({
        underlying: smileResult.underlying,
        expiry_date: smileResult.expiry_date,
        target_date: smileResult.target_date,
        option_type: smileResult.option_type,
        spot_price: smileResult.spot_price,
        points,
      });
      message.success(`已保存 ${points.length} 个点到缓存`);
    } catch (e) {
      message.error('保存失败: ' + e.message);
    }
  };

  // ── Batch ──
  const handleBatch = async (values) => {
    setLoading(true);
    setBatchResult(null);
    try {
      const result = await deribitDebugService.batchSmile({
        underlying: values.underlying || 'BTC',
        expiry_date: values.expiry_date, // ISO string from Select
        option_type: values.option_type || 'PUT',
        start_date: values.date_range[0].format('YYYY-MM-DD'),
        end_date: values.date_range[1].format('YYYY-MM-DD'),
        num_strikes: values.num_strikes || 7,
        window_days: values.window_days || 5,
        save_to_cache: values.save_to_cache !== false,
      });
      setBatchResult(result);
      message.success(`批量完成: API ${result.from_api} 天, 缓存 ${result.from_cache} 天, 保存 ${result.points_saved} 点`);
    } catch (e) {
      message.error('批量请求失败: ' + (e.response?.data?.detail || e.message));
    } finally {
      setLoading(false);
    }
  };

  // ── Cache ──
  const handleLoadCacheStats = async () => {
    try {
      const stats = await deribitDebugService.getCacheStats();
      setCacheStats(stats);
    } catch (e) {
      message.error('获取缓存统计失败');
    }
  };

  const handleLoadCacheData = async (underlying = 'BTC') => {
    setLoading(true);
    try {
      const data = await deribitDebugService.getIVCacheData(underlying, 500);
      setCacheData(data);
    } catch (e) {
      message.error('获取缓存数据失败');
    } finally {
      setLoading(false);
    }
  };

  const handleClearIVCache = async () => {
    try {
      const result = await deribitDebugService.clearIVCache();
      message.success(result.message);
      handleLoadCacheStats();
    } catch (e) {
      message.error('清除失败');
    }
  };

  // ── ATM IV History ──
  const handleATMIVHistory = async (values) => {
    setLoading(true);
    setAtmIvData(null);
    try {
      const [start, end] = values.date_range;
      const result = await deribitDebugService.getATMIVHistory({
        underlying: values.underlying || 'BTC',
        start_date: start.format('YYYY-MM-DD'),
        end_date: end.format('YYYY-MM-DD'),
        option_type: values.option_type || 'PUT',
      });
      setAtmIvData(result);
      message.success(`获取完成: ${result.valid_dates}/${result.total_dates} 天有IV数据`);
    } catch (e) {
      message.error('请求失败: ' + (e.response?.data?.detail || e.message));
    } finally {
      setLoading(false);
    }
  };

  // ── OKX XAU ──
  const xauStreamRef = useRef(null);

  const fetchXauStatus = useCallback(async () => {
    try {
      const resp = await okxXauService.status();
      setXauRunning(resp.data.running);
      setXauRecords(resp.data.records);
      setXauMode(resp.data.mode || 'unknown');
    } catch (_) { /* ignore */ }
  }, []);

  const fetchXauHistory = useCallback(async () => {
    try {
      const resp = await okxXauService.history(200);
      setXauHistory(resp.data.data || []);
      setXauRecords(resp.data.total || 0);
    } catch (_) { /* ignore */ }
  }, []);

  const fetchXauDbStats = useCallback(async () => {
    try {
      const resp = await okxXauService.dbStats();
      setXauDbStats(resp.data);
    } catch (_) { /* ignore */ }
  }, []);

  // Check status on mount
  useEffect(() => {
    fetchXauStatus();
    fetchXauDbStats();
  }, [fetchXauStatus]);

  // SSE stream management
  useEffect(() => {
    if (!xauRunning) {
      if (xauStreamRef.current) {
        xauStreamRef.current.close();
        xauStreamRef.current = null;
      }
      return;
    }
    const es = okxXauService.createStream();
    xauStreamRef.current = es;
    es.onmessage = (event) => {
      try {
        const snap = JSON.parse(event.data);
        setXauLatest(snap);
        setXauHistory(prev => {
          const next = [...prev, snap];
          return next.length > 200 ? next.slice(-200) : next;
        });
        setXauRecords(r => r + 1);
      } catch (_) { /* ignore */ }
    };
    es.onerror = () => {
      // will auto-reconnect via browser EventSource
    };
    return () => {
      es.close();
      xauStreamRef.current = null;
    };
  }, [xauRunning]);

  const handleXauToggle = async () => {
    try {
      if (xauRunning) {
        await okxXauService.stop();
        setXauRunning(false);
        setXauMode('unknown');
        message.success('已停止采集');
      } else {
        await okxXauService.start();
        setXauRunning(true);
        message.success('开始采集');
        // Check mode after a short delay (WS needs time to connect/fallback)
        setTimeout(async () => {
          try {
            const resp = await okxXauService.status();
            setXauMode(resp.data.mode || 'unknown');
          } catch (_) {}
        }, 3000);
      }
    } catch (e) {
      message.error('操作失败: ' + (e.response?.data?.detail || e.message));
    }
  };

  const handleXauClear = async () => {
    try {
      await okxXauService.clear();
      setXauHistory([]);
      setXauRecords(0);
      setXauLatest(null);
      message.success('已清除历史数据');
    } catch (e) {
      message.error('清除失败');
    }
  };

  const handleXauExportCsv = () => {
    if (!xauHistory.length) {
      message.warning('没有数据可导出');
      return;
    }
    const header = 'timestamp,spot_ask,spot_bid,swap_ask,swap_bid,basis_sell,basis_buy,funding_rate';
    const rows = xauHistory.map(r =>
      [r.timestamp, r.spot_ask, r.spot_bid, r.swap_ask, r.swap_bid, r.basis_sell ?? r.basis, r.basis_buy ?? '', r.funding_rate ?? ''].join(',')
    );
    const csv = [header, ...rows].join('\n');
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `okx_xau_${new Date().toISOString().slice(0, 19).replace(/[:-]/g, '')}.csv`;
    a.click();
    URL.revokeObjectURL(url);
    message.success(`已导出 ${xauHistory.length} 条数据`);
  };

  const handleArbBacktest = async (values) => {
    setArbLoading(true);
    setArbResult(null);
    try {
      const resp = await okxXauService.arbBacktest({
        open_threshold: values.open_threshold,
        close_threshold: values.close_threshold,
        quantity: values.quantity,
        fee_rate: values.fee_rate,
        funding_interval_hours: values.funding_interval_hours,
      });
      setArbResult(resp.data);
      const s = resp.data.summary;
      message.success(`回测完成: ${s.total_trades} 笔交易, 净盈亏 ${s.net_pnl}`);
    } catch (e) {
      message.error('回测失败: ' + (e.response?.data?.detail || e.message));
    } finally {
      setArbLoading(false);
    }
  };

  // ── Columns ──
  const tradeColumns = [
    { title: '时间', dataIndex: 'datetime', key: 'datetime', width: 200 },
    { title: '价格(BTC)', dataIndex: 'price', key: 'price', render: v => v?.toFixed(6) },
    { title: '数量', dataIndex: 'amount', key: 'amount' },
    { title: '方向', dataIndex: 'direction', key: 'direction',
      render: v => <Tag color={v === 'buy' ? 'green' : 'red'}>{v}</Tag> },
    { title: 'Index Price', dataIndex: 'index_price', key: 'index_price', render: v => v?.toFixed(2) },
    { title: 'IV(Deribit)', dataIndex: 'iv', key: 'iv', render: v => v != null ? (v + '%') : '-' },
    { title: '来源', dataIndex: 'source', key: 'source',
      render: v => <Tag color={v === 'trade_api' ? 'blue' : 'green'}>{v}</Tag> },
  ];

  const chartColumns = [
    { title: '时间', dataIndex: 'datetime', key: 'datetime', width: 200 },
    { title: 'Open', dataIndex: 'open', key: 'open', render: v => v?.toFixed(6) },
    { title: 'High', dataIndex: 'high', key: 'high', render: v => v?.toFixed(6) },
    { title: 'Low', dataIndex: 'low', key: 'low', render: v => v?.toFixed(6) },
    { title: 'Close', dataIndex: 'close', key: 'close', render: v => v?.toFixed(6) },
    { title: 'Volume', dataIndex: 'volume', key: 'volume', render: v => v?.toFixed(2) },
    { title: '现货', dataIndex: 'spot_used', key: 'spot_used', render: v => v ? v.toLocaleString() : '-' },
    { title: '价格(USD)', dataIndex: 'price_usd', key: 'price_usd', render: v => v ? v.toFixed(2) : '-' },
    { title: 'IV(计算)', dataIndex: 'iv_calculated', key: 'iv_calculated',
      render: v => v != null ? <Tag color="green">{(v * 100).toFixed(2)}%</Tag> : <Tag color="default">-</Tag> },
  ];

  const smileDetailColumns = [
    { title: 'Strike', dataIndex: 'strike', key: 'strike', render: v => v?.toLocaleString() },
    { title: '合约', dataIndex: 'instrument', key: 'instrument', width: 220 },
    { title: '距离%', dataIndex: 'distance_pct', key: 'distance_pct', render: v => v != null ? v + '%' : '-' },
    { title: '找到交易', dataIndex: 'trade_found', key: 'trade_found',
      render: v => v ? <Tag color="green">是</Tag> : <Tag color="red">否</Tag> },
    { title: '价格(BTC)', dataIndex: 'trade_price_btc', key: 'trade_price_btc',
      render: v => v != null ? v.toFixed(6) : '-' },
    { title: '价格(USD)', dataIndex: 'trade_price_usd', key: 'trade_price_usd',
      render: v => v != null ? v.toFixed(2) : '-' },
    { title: '交易时间', dataIndex: 'trade_datetime', key: 'trade_datetime', width: 180,
      render: v => v ? v.substring(0, 19) : '-' },
    { title: 'IV(Deribit)', dataIndex: 'trade_iv_deribit', key: 'trade_iv_deribit',
      render: v => v != null ? (v + '%') : '-' },
    { title: 'IV(计算)', dataIndex: 'iv_calculated', key: 'iv_calculated',
      render: v => v != null ? (v * 100).toFixed(2) + '%' : '-' },
    { title: '状态', dataIndex: 'status', key: 'status',
      render: v => {
        const colors = { ok: 'green', no_trade: 'default', iv_out_of_range: 'orange', price_zero_or_expired: 'red' };
        return <Tag color={colors[v] || 'default'}>{v}</Tag>;
      }
    },
  ];

  const batchColumns = [
    { title: '日期', dataIndex: 'date', key: 'date' },
    { title: '现货', dataIndex: 'spot', key: 'spot', render: v => v?.toLocaleString() },
    { title: '来源', dataIndex: 'source', key: 'source',
      render: v => <Tag color={v === 'cache' ? 'blue' : 'green'}>{v === 'cache' ? '缓存' : 'API'}</Tag> },
    { title: 'IV点数', dataIndex: 'points', key: 'points',
      render: v => v > 0 ? <Tag color="green">{v}</Tag> : <Tag color="red">0</Tag> },
  ];

  const cacheColumns = [
    { title: '查询日期', dataIndex: 'target_date', key: 'target_date' },
    { title: '到期日', dataIndex: 'expiry_date', key: 'expiry_date' },
    { title: '类型', dataIndex: 'option_type', key: 'option_type' },
    { title: 'Strike', dataIndex: 'strike', key: 'strike', render: v => v?.toLocaleString() },
    { title: 'IV', dataIndex: 'iv', key: 'iv', render: v => (v * 100).toFixed(2) + '%' },
    { title: '现货', dataIndex: 'spot_price', key: 'spot_price', render: v => v?.toLocaleString() },
    { title: '价格(USD)', dataIndex: 'trade_price_usd', key: 'trade_price_usd', render: v => v?.toFixed(2) },
    { title: '合约', dataIndex: 'instrument', key: 'instrument' },
  ];

  // ── Expiry Select options ──
  const expiryOptions = EXPIRY_DATES.map(e => (
    <Option key={e.date} value={e.date}>{e.label}</Option>
  ));

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <AntHeader style={{ background: '#fff', padding: '0 24px', display: 'flex', alignItems: 'center', borderBottom: '1px solid #f0f0f0' }}>
        <Space>
          <Link to="/"><Button icon={<ArrowLeftOutlined />}>返回</Button></Link>
          <BugOutlined style={{ fontSize: 20, color: '#fa8c16' }} />
          <Title level={4} style={{ margin: 0 }}>Deribit 数据调试</Title>
        </Space>
      </AntHeader>

      <Content style={{ padding: 24 }}>
        <Spin spinning={loading}>
          <Tabs activeKey={activeTab} onChange={setActiveTab} items={[

            // ── Tab 1: 单合约交易测试 ──
            {
              key: 'trade-test',
              label: <span><SearchOutlined /> 单合约测试</span>,
              children: (
                <Row gutter={24}>
                  <Col span={8}>
                    <Card title="测试参数" size="small">
                      <Alert type="info" showIcon style={{ marginBottom: 12 }}
                        message="可以通过下方字段自动生成合约名，也可以直接手动输入" />
                      <Form form={tradeForm} layout="vertical" onFinish={handleTradeTest}
                        initialValues={{ underlying: 'BTC', option_type: 'PUT', strike: 80000, window_days: 5 }}
                        onValuesChange={updateInstrumentPreview}>
                        <Row gutter={8}>
                          <Col span={8}>
                            <Form.Item label="标的" name="underlying">
                              <Select onChange={updateInstrumentPreview}>
                                <Option value="BTC">BTC</Option>
                                <Option value="ETH">ETH</Option>
                              </Select>
                            </Form.Item>
                          </Col>
                          <Col span={16}>
                            <Form.Item label="到期日(Deribit)" name="expiry_date"
                              extra="仅限每月最后一个周五">
                              <Select showSearch optionFilterProp="children"
                                placeholder="选择到期日" allowClear
                                onChange={updateInstrumentPreview}>
                                {expiryOptions}
                              </Select>
                            </Form.Item>
                          </Col>
                        </Row>
                        <Row gutter={8}>
                          <Col span={12}>
                            <Form.Item label="Strike" name="strike">
                              <InputNumber style={{ width: '100%' }} min={0}
                                onChange={updateInstrumentPreview} />
                            </Form.Item>
                          </Col>
                          <Col span={12}>
                            <Form.Item label="类型" name="option_type">
                              <Select onChange={updateInstrumentPreview}>
                                <Option value="PUT">PUT</Option>
                                <Option value="CALL">CALL</Option>
                              </Select>
                            </Form.Item>
                          </Col>
                        </Row>
                        <Divider style={{ margin: '8px 0' }} />
                        <Form.Item label="合约名称(自动生成或手动输入)" name="instrument" rules={[{ required: true }]}
                          extra={instrumentPreview ? `预览: ${instrumentPreview}` : '填写上方字段自动生成，或直接输入'}>
                          <Input placeholder="BTC-28MAR25-80000-P" />
                        </Form.Item>
                        <Form.Item label="目标日期(查询交易的日期)" name="target_date" rules={[{ required: true }]}
                          extra="必须在合约到期日之前">
                          <DatePicker style={{ width: '100%' }} />
                        </Form.Item>
                        <Form.Item label="搜索窗口(天)" name="window_days">
                          <InputNumber min={1} max={30} style={{ width: '100%' }} />
                        </Form.Item>
                        <Button type="primary" htmlType="submit" icon={<SearchOutlined />} block>
                          查询交易
                        </Button>
                      </Form>
                    </Card>
                  </Col>
                  <Col span={16}>
                  {tradeResult && (
                      <Space direction="vertical" style={{ width: '100%' }}>
                        <Card title="Trade API (逐笔交易)" size="small"
                          extra={<Text type={tradeResult.trade_api?.error ? 'danger' : 'success'}>
                            {tradeResult.trade_api?.error ? `错误: ${JSON.stringify(tradeResult.trade_api.error)}` : `${tradeResult.trade_api?.count || 0} 笔交易`}
                          </Text>}>
                          <Text type="secondary">搜索范围: {tradeResult.search_range?.from} ~ {tradeResult.search_range?.to}</Text>
                          {tradeResult.trade_api?.data?.length > 0 ? (
                            <Table dataSource={tradeResult.trade_api.data} columns={tradeColumns}
                              rowKey="timestamp" size="small" pagination={false} scroll={{ x: 900 }}
                              style={{ marginTop: 8 }} />
                          ) : (
                            <Alert type="warning" showIcon style={{ marginTop: 8 }}
                              message="Trade API 未返回数据"
                              description="对于已过期较久的合约，Deribit 的逐笔交易API可能不保留数据。请查看下方 Chart API 结果。" />
                          )}
                        </Card>

                        <Card title="Chart API (K线数据 — 适用于已过期合约)" size="small"
                          extra={<Text type={tradeResult.chart_api?.error ? 'danger' : 'success'}>
                            {tradeResult.chart_api?.error ? `错误: ${JSON.stringify(tradeResult.chart_api.error)}` : `${tradeResult.chart_api?.count || 0} 条K线`}
                          </Text>}>
                          {tradeResult.chart_api?.data?.length > 0 ? (
                            <>
                              {tradeResult.chart_api.data.some(d => d.iv_calculated) ? (
                                <Alert type="success" showIcon style={{ marginBottom: 8 }}
                                  message={`已计算IV: ${tradeResult.chart_api.data.filter(d => d.iv_calculated).length} 条有IV数据`} />
                              ) : (
                                <Alert type="info" showIcon style={{ marginBottom: 8 }}
                                  message="填写上方的标的、到期日、Strike、类型字段后，Chart数据会自动计算IV" />
                              )}
                              <Table dataSource={tradeResult.chart_api.data} columns={chartColumns}
                                rowKey="timestamp" size="small" pagination={false} scroll={{ x: 1100 }} />
                            </>
                          ) : (
                            <Alert type="info" showIcon
                              message="Chart API 也未返回数据"
                              description="可能合约名称不正确，或该合约在指定日期范围内确实没有交易。" />
                          )}
                        </Card>

                        {tradeResult.trade_api?.count === 0 && tradeResult.chart_api?.count > 0 && (
                          <Alert type="success" showIcon
                            message="Chart API 成功获取数据"
                            description="回测引擎已自动使用 Chart API 作为 fallback，当 Trade API 找不到数据时会自动切换到 Chart API 获取K线收盘价。" />
                        )}
                      </Space>
                    )}
                  </Col>
                </Row>
              ),
            },

            // ── Tab 2: IV微笑测试 ──
            {
              key: 'smile-test',
              label: <span><ThunderboltOutlined /> IV微笑测试</span>,
              children: (
                <Row gutter={24}>
                  <Col span={8}>
                    <Card title="测试参数" size="small">
                      <Form form={smileForm} layout="vertical" onFinish={handleSmileTest}
                        initialValues={{ underlying: 'BTC', option_type: 'PUT', num_strikes: 7, window_days: 5 }}>
                        <Form.Item label="标的" name="underlying">
                          <Select>
                            <Option value="BTC">BTC</Option>
                            <Option value="ETH">ETH</Option>
                          </Select>
                        </Form.Item>
                        <Form.Item label="到期日(Deribit)" name="expiry_date" rules={[{ required: true }]}
                          extra="仅限每月最后一个周五">
                          <Select showSearch optionFilterProp="children" placeholder="选择到期日">
                            {expiryOptions}
                          </Select>
                        </Form.Item>
                        <Form.Item label="查询日期" name="target_date" rules={[{ required: true }]}
                          extra="必须在到期日之前">
                          <DatePicker style={{ width: '100%' }} />
                        </Form.Item>
                        <Form.Item label="期权类型" name="option_type">
                          <Select>
                            <Option value="PUT">PUT</Option>
                            <Option value="CALL">CALL</Option>
                          </Select>
                        </Form.Item>
                        <Form.Item label="现货价格(可选)" name="spot_price" extra="留空自动获取">
                          <InputNumber style={{ width: '100%' }} min={0} />
                        </Form.Item>
                        <Form.Item label="Strike数量(每侧)" name="num_strikes">
                          <InputNumber min={3} max={15} style={{ width: '100%' }} />
                        </Form.Item>
                        <Form.Item label="搜索窗口(天)" name="window_days">
                          <InputNumber min={1} max={30} style={{ width: '100%' }} />
                        </Form.Item>
                        <Button type="primary" htmlType="submit" icon={<SearchOutlined />} block>
                          测试IV微笑
                        </Button>
                      </Form>
                    </Card>
                  </Col>
                  <Col span={16}>
                    {smileResult && (
                      <Space direction="vertical" style={{ width: '100%' }}>
                        <Card title="测试结果" size="small"
                          extra={
                            <Button icon={<SaveOutlined />} onClick={handleSaveSmile}
                              disabled={!smileResult.smile_points?.length}>
                              保存到缓存
                            </Button>
                          }>
                          <Row gutter={16} style={{ marginBottom: 16 }}>
                            <Col span={4}><Statistic title="现货" value={smileResult.spot_price} precision={2} /></Col>
                            <Col span={4}><Statistic title="ATM Strike" value={smileResult.atm_strike} /></Col>
                            <Col span={3}><Statistic title="Step" value={smileResult.strike_step} /></Col>
                            <Col span={4}><Statistic title="测试数" value={smileResult.candidates_tested} /></Col>
                            <Col span={4}><Statistic title="找到交易" value={smileResult.trades_found} /></Col>
                            <Col span={3}><Statistic title="有效IV" value={smileResult.valid_iv_points}
                              valueStyle={{ color: smileResult.valid_iv_points > 0 ? '#3f8600' : '#cf1322' }} /></Col>
                            <Col span={4}><Statistic title="距到期(天)" value={smileResult.time_to_expiry_days} /></Col>
                          </Row>

                          {smileResult.smile_points?.length > 0 && (
                            <>
                              <Divider>IV 微笑曲线</Divider>
                              <ResponsiveContainer width="100%" height={300}>
                                <LineChart data={[...smileResult.smile_points].sort((a, b) => a.strike - b.strike)}>
                                  <CartesianGrid strokeDasharray="3 3" />
                                  <XAxis dataKey="strike" tickFormatter={v => (v / 1000).toFixed(0) + 'K'} />
                                  <YAxis tickFormatter={v => (v * 100).toFixed(0) + '%'} />
                                  <Tooltip formatter={(v) => (v * 100).toFixed(2) + '%'}
                                    labelFormatter={v => 'Strike: ' + v?.toLocaleString()} />
                                  <Line type="monotone" dataKey="iv" stroke="#1890ff" strokeWidth={2} dot={{ r: 4 }} />
                                  <ReferenceLine x={smileResult.atm_strike} stroke="#ff4d4f" strokeDasharray="3 3"
                                    label={{ value: 'ATM', position: 'top' }} />
                                </LineChart>
                              </ResponsiveContainer>
                            </>
                          )}

                          {smileResult.valid_iv_points === 0 && (
                            <Alert type="error" showIcon style={{ marginTop: 8 }}
                              message="未找到有效IV数据"
                              description="可能原因: 1) 到期日不是有效的Deribit到期日 2) 查询日期距到期日太远或太近 3) 该时间段流动性不足。建议增大搜索窗口或换一个到期日。" />
                          )}

                          {smileResult.cached_points?.length > 0 && (
                            <Alert type="info" showIcon style={{ marginTop: 8 }}
                              message={`缓存中已有 ${smileResult.cached_points.length} 个点`} />
                          )}
                        </Card>

                        <Card title="逐Strike详情" size="small">
                          <Table dataSource={smileResult.details} columns={smileDetailColumns}
                            rowKey="strike" size="small" pagination={false} scroll={{ x: 1400 }} />
                        </Card>
                      </Space>
                    )}
                  </Col>
                </Row>
              ),
            },

            // ── Tab 3: 批量采集 ──
            {
              key: 'batch',
              label: <span><CloudDownloadOutlined /> 批量采集</span>,
              children: (
                <Row gutter={24}>
                  <Col span={8}>
                    <Card title="批量参数" size="small">
                      <Form layout="vertical" onFinish={handleBatch}
                        initialValues={{ underlying: 'BTC', option_type: 'PUT', num_strikes: 7, window_days: 5, save_to_cache: true }}>
                        <Form.Item label="标的" name="underlying">
                          <Select>
                            <Option value="BTC">BTC</Option>
                            <Option value="ETH">ETH</Option>
                          </Select>
                        </Form.Item>
                        <Form.Item label="到期日(Deribit)" name="expiry_date" rules={[{ required: true }]}
                          extra="仅限每月最后一个周五">
                          <Select showSearch optionFilterProp="children" placeholder="选择到期日">
                            {expiryOptions}
                          </Select>
                        </Form.Item>
                        <Form.Item label="期权类型" name="option_type">
                          <Select>
                            <Option value="PUT">PUT</Option>
                            <Option value="CALL">CALL</Option>
                          </Select>
                        </Form.Item>
                        <Form.Item label="日期范围" name="date_range" rules={[{ required: true }]}
                          extra="采集这段时间内每天的IV微笑数据">
                          <RangePicker style={{ width: '100%' }} />
                        </Form.Item>
                        <Form.Item label="Strike数量(每侧)" name="num_strikes">
                          <InputNumber min={3} max={15} style={{ width: '100%' }} />
                        </Form.Item>
                        <Form.Item label="搜索窗口(天)" name="window_days">
                          <InputNumber min={1} max={30} style={{ width: '100%' }} />
                        </Form.Item>
                        <Form.Item label="自动保存到缓存" name="save_to_cache" valuePropName="checked">
                          <Switch defaultChecked />
                        </Form.Item>
                        <Button type="primary" htmlType="submit" icon={<CloudDownloadOutlined />} block>
                          开始批量采集
                        </Button>
                      </Form>
                      <Alert type="warning" showIcon style={{ marginTop: 12 }}
                        message="注意" description="批量采集会调用大量API，请控制日期范围。每天约14个API请求，每个间隔0.3秒。" />
                    </Card>
                  </Col>
                  <Col span={16}>
                    {batchResult && (
                      <Space direction="vertical" style={{ width: '100%' }}>
                        <Card title="批量结果" size="small">
                          <Row gutter={16} style={{ marginBottom: 16 }}>
                            <Col span={4}><Statistic title="总天数" value={batchResult.total_dates} /></Col>
                            <Col span={4}><Statistic title="来自缓存" value={batchResult.from_cache} valueStyle={{ color: '#1890ff' }} /></Col>
                            <Col span={4}><Statistic title="来自API" value={batchResult.from_api} valueStyle={{ color: '#52c41a' }} /></Col>
                            <Col span={4}><Statistic title="保存点数" value={batchResult.points_saved} /></Col>
                            <Col span={8}><Statistic title="日期范围" value={batchResult.date_range} /></Col>
                          </Row>
                          {batchResult.results?.length > 0 && (
                            <>
                              <Divider>每日IV点数</Divider>
                              <ResponsiveContainer width="100%" height={200}>
                                <LineChart data={batchResult.results}>
                                  <CartesianGrid strokeDasharray="3 3" />
                                  <XAxis dataKey="date" />
                                  <YAxis />
                                  <Tooltip />
                                  <Line type="monotone" dataKey="points" stroke="#52c41a" strokeWidth={2} dot={{ r: 2 }} name="IV点数" />
                                </LineChart>
                              </ResponsiveContainer>
                            </>
                          )}
                          <Table dataSource={batchResult.results} columns={batchColumns}
                            rowKey="date" size="small" pagination={{ pageSize: 20 }} />
                        </Card>
                      </Space>
                    )}
                  </Col>
                </Row>
              ),
            },

            // ── Tab 4: ATM IV 历史 ──
            {
              key: 'atm-iv',
              label: <span><LineChartOutlined /> ATM IV历史</span>,
              children: (
                <Row gutter={24}>
                  <Col span={6}>
                    <Card title="查询参数" size="small">
                      <Form layout="vertical" onFinish={handleATMIVHistory}
                        initialValues={{ underlying: 'BTC', option_type: 'PUT' }}>
                        <Form.Item label="标的" name="underlying">
                          <Select>
                            <Option value="BTC">BTC</Option>
                            <Option value="ETH">ETH</Option>
                          </Select>
                        </Form.Item>
                        <Form.Item label="期权类型" name="option_type">
                          <Select>
                            <Option value="PUT">PUT</Option>
                            <Option value="CALL">CALL</Option>
                          </Select>
                        </Form.Item>
                        <Form.Item label="日期范围" name="date_range" rules={[{ required: true }]}>
                          <RangePicker style={{ width: '100%' }} />
                        </Form.Item>
                        <Button type="primary" htmlType="submit" icon={<SearchOutlined />} block>
                          查询ATM IV
                        </Button>
                      </Form>
                      <Alert type="info" showIcon style={{ marginTop: 12 }}
                        message="说明" description="展示每日ATM期权的隐含波动率走势。优先使用缓存数据，缓存不存在时从API获取。" />
                    </Card>
                  </Col>
                  <Col span={18}>
                    {atmIvData && (
                      <Space direction="vertical" style={{ width: '100%' }}>
                        <Card title={`ATM IV 走势 — ${atmIvData.underlying} ${atmIvData.option_type}`} size="small"
                          extra={<Text type="secondary">{atmIvData.valid_dates}/{atmIvData.total_dates} 天有数据</Text>}>
                          <Row gutter={16} style={{ marginBottom: 16 }}>
                            <Col span={4}><Statistic title="总天数" value={atmIvData.total_dates} /></Col>
                            <Col span={4}><Statistic title="有效天数" value={atmIvData.valid_dates} valueStyle={{ color: '#3f8600' }} /></Col>
                            {(() => {
                              const validPts = atmIvData.data.filter(d => d.atm_iv_pct != null);
                              if (validPts.length === 0) return null;
                              const ivs = validPts.map(d => d.atm_iv_pct);
                              const avg = ivs.reduce((a, b) => a + b, 0) / ivs.length;
                              const max = Math.max(...ivs);
                              const min = Math.min(...ivs);
                              return (
                                <>
                                  <Col span={4}><Statistic title="平均IV" value={avg.toFixed(1)} suffix="%" /></Col>
                                  <Col span={4}><Statistic title="最高IV" value={max.toFixed(1)} suffix="%" valueStyle={{ color: '#cf1322' }} /></Col>
                                  <Col span={4}><Statistic title="最低IV" value={min.toFixed(1)} suffix="%" valueStyle={{ color: '#3f8600' }} /></Col>
                                  <Col span={4}><Statistic title="当前IV" value={validPts[validPts.length - 1].atm_iv_pct.toFixed(1)} suffix="%" /></Col>
                                </>
                              );
                            })()}
                          </Row>
                          <ResponsiveContainer width="100%" height={400}>
                            <LineChart data={atmIvData.data.filter(d => d.atm_iv_pct != null)} margin={{ top: 5, right: 30, left: 10, bottom: 5 }}>
                              <CartesianGrid strokeDasharray="3 3" />
                              <XAxis dataKey="date" tick={{ fontSize: 11 }} interval="preserveStartEnd"
                                tickFormatter={(v) => v.substring(5)} />
                              <YAxis yAxisId="iv" tick={{ fontSize: 11 }}
                                label={{ value: 'IV (%)', angle: -90, position: 'insideLeft', fontSize: 12 }}
                                domain={['auto', 'auto']} />
                              <YAxis yAxisId="spot" orientation="right" tick={{ fontSize: 11 }}
                                label={{ value: '现货价格', angle: 90, position: 'insideRight', fontSize: 12 }}
                                domain={['auto', 'auto']} />
                              <Tooltip formatter={(value, name) => {
                                if (name === 'ATM IV') return [value.toFixed(1) + '%', name];
                                if (name === '现货价格') return ['$' + value.toLocaleString(), name];
                                return [value, name];
                              }} />
                              <Legend />
                              <Line yAxisId="iv" type="monotone" dataKey="atm_iv_pct" name="ATM IV"
                                stroke="#722ed1" strokeWidth={2} dot={false} />
                              <Line yAxisId="spot" type="monotone" dataKey="spot" name="现货价格"
                                stroke="#faad14" strokeWidth={1} dot={false} opacity={0.6} />
                            </LineChart>
                          </ResponsiveContainer>
                        </Card>

                        <Card title="每日数据" size="small">
                          <Table
                            dataSource={atmIvData.data.filter(d => d.atm_iv_pct != null)}
                            rowKey="date" size="small" pagination={{ pageSize: 30 }}
                            columns={[
                              { title: '日期', dataIndex: 'date', key: 'date', width: 110 },
                              { title: '现货', dataIndex: 'spot', key: 'spot', width: 100, render: v => '$' + v?.toLocaleString() },
                              { title: 'ATM Strike', dataIndex: 'atm_strike', key: 'atm_strike', width: 100, render: v => v?.toLocaleString() },
                              { title: '到期日', dataIndex: 'expiry', key: 'expiry', width: 110 },
                              { title: 'ATM IV', dataIndex: 'atm_iv_pct', key: 'atm_iv_pct', width: 100,
                                render: v => v != null ? <Tag color={v > 80 ? 'red' : v < 40 ? 'green' : 'blue'}>{v.toFixed(1)}%</Tag> : '-' },
                              { title: 'Smile点数', dataIndex: 'smile_points', key: 'smile_points', width: 80 },
                            ]}
                          />
                        </Card>
                      </Space>
                    )}
                  </Col>
                </Row>
              ),
            },

            // ── Tab 5: 缓存管理 ──
            {
              key: 'cache',
              label: <span><DatabaseOutlined /> 缓存管理</span>,
              children: (
                <Space direction="vertical" style={{ width: '100%' }}>
                  <Card title="缓存统计" size="small"
                    extra={
                      <Space>
                        <Button onClick={handleLoadCacheStats} icon={<SearchOutlined />}>刷新统计</Button>
                        <Button onClick={() => handleLoadCacheData('BTC')} icon={<DatabaseOutlined />}>查看BTC数据</Button>
                        <Button onClick={() => handleLoadCacheData('ETH')} icon={<DatabaseOutlined />}>查看ETH数据</Button>
                        <Popconfirm title="确定清除所有IV缓存?" onConfirm={handleClearIVCache}>
                          <Button danger icon={<DeleteOutlined />}>清除IV缓存</Button>
                        </Popconfirm>
                      </Space>
                    }>
                    {cacheStats ? (
                      <Row gutter={16}>
                        <Col span={6}><Statistic title="价格记录" value={cacheStats.price_records} /></Col>
                        <Col span={6}><Statistic title="IV数据点" value={cacheStats.iv_smile_points} valueStyle={{ color: '#3f8600' }} /></Col>
                        <Col span={6}><Statistic title="空标记(遗留)" value={cacheStats.empty_smile_markers}
                          valueStyle={{ color: cacheStats.empty_smile_markers > 0 ? '#cf1322' : '#999' }} /></Col>
                      </Row>
                    ) : (
                      <Alert type="info" message="点击「刷新统计」查看缓存状态" showIcon />
                    )}
                  </Card>
                  {cacheData && (
                    <Card title={`缓存数据 (${cacheData.count} 条)`} size="small">
                      <Table dataSource={cacheData.data} columns={cacheColumns}
                        rowKey="id" size="small" pagination={{ pageSize: 20 }} scroll={{ x: 1200 }} />
                    </Card>
                  )}
                </Space>
              ),
            },

            // ── Tab 6: OKX XAU数据 ──
            {
              key: 'okx-xau',
              label: <span><GoldOutlined /> OKX XAU数据</span>,
              children: (
                <Space direction="vertical" style={{ width: '100%' }}>
                  <Card size="small"
                    title={
                      <Space>
                        <Tag color={xauRunning ? 'green' : 'default'}>{xauRunning ? '采集中' : '已停止'}</Tag>
                        {xauRunning && <Tag color={xauMode === 'ws' ? 'blue' : 'orange'}>{xauMode === 'ws' ? 'WebSocket' : 'REST轮询'}</Tag>}
                        <Text>XAUT-USDT 现货 + XAU-USDT-SWAP 永续</Text>
                        <Text type="secondary">({xauRecords} 条记录)</Text>
                      </Space>
                    }
                    extra={
                      <Space>
                        <Button type={xauRunning ? 'default' : 'primary'} danger={xauRunning}
                          onClick={handleXauToggle}>
                          {xauRunning ? '停止采集' : '开始采集'}
                        </Button>
                        <Button onClick={fetchXauHistory}>刷新历史</Button>
                        <Button icon={<DownloadOutlined />} onClick={handleXauExportCsv} disabled={!xauHistory.length}>导出CSV</Button>
                        <Popconfirm title="确定清除所有历史数据?" onConfirm={handleXauClear}>
                          <Button danger icon={<DeleteOutlined />}>清除</Button>
                        </Popconfirm>
                      </Space>
                    }>
                    {xauLatest ? (
                      <Row gutter={16}>
                        <Col span={4}>
                          <Statistic title="现货 Ask" value={xauLatest.spot_ask} precision={2}
                            valueStyle={{ color: '#cf1322' }} />
                        </Col>
                        <Col span={4}>
                          <Statistic title="现货 Bid" value={xauLatest.spot_bid} precision={2}
                            valueStyle={{ color: '#3f8600' }} />
                        </Col>
                        <Col span={4}>
                          <Statistic title="永续 Ask" value={xauLatest.swap_ask} precision={2}
                            valueStyle={{ color: '#cf1322' }} />
                        </Col>
                        <Col span={4}>
                          <Statistic title="永续 Bid" value={xauLatest.swap_bid} precision={2}
                            valueStyle={{ color: '#3f8600' }} />
                        </Col>
                        <Col span={4}>
                          <Statistic title="基差" value={xauLatest.basis} precision={4}
                            valueStyle={{ color: xauLatest.basis >= 0 ? '#3f8600' : '#cf1322' }} />
                        </Col>
                        <Col span={4}>
                          <Statistic title="资金费率" value={xauLatest.funding_rate != null ? (xauLatest.funding_rate * 100).toFixed(6) + '%' : '-'} />
                        </Col>
                      </Row>
                    ) : (
                      <Alert type="info" showIcon message="点击「开始采集」获取实时数据" />
                    )}
                  </Card>

                  <Card size="small" title="数据库存储"
                    extra={
                      <Space>
                        <Button size="small" onClick={fetchXauDbStats}>刷新</Button>
                        <Button size="small" icon={<DownloadOutlined />}
                          disabled={!xauDbStats || !xauDbStats.total_records}
                          onClick={() => {
                            const base = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';
                            window.open(`${base}/api/okx-xau/db-export-csv`, '_blank');
                          }}>
                          导出全部CSV
                        </Button>
                      </Space>
                    }>
                    {xauDbStats ? (
                      <Row gutter={16}>
                        <Col span={6}><Statistic title="总记录数" value={xauDbStats.total_records} /></Col>
                        <Col span={9}><Statistic title="最早时间" value={xauDbStats.first_time ? xauDbStats.first_time.replace('T', ' ').substring(0, 19) : '-'} /></Col>
                        <Col span={9}><Statistic title="最新时间" value={xauDbStats.last_time ? xauDbStats.last_time.replace('T', ' ').substring(0, 19) : '-'} /></Col>
                      </Row>
                    ) : (
                      <Text type="secondary">数据自动存入数据库，每10秒批量写入</Text>
                    )}
                  </Card>

                  {xauHistory.length > 0 && (
                    <Card title="基差走势 (点差)" size="small">
                      <ResponsiveContainer width="100%" height={250}>
                        <LineChart data={xauHistory.slice(-120)}>
                          <CartesianGrid strokeDasharray="3 3" />
                          <XAxis dataKey="time" tick={{ fontSize: 10 }} interval="preserveStartEnd" />
                          <YAxis domain={['auto', 'auto']} tick={{ fontSize: 11 }} />
                          <Tooltip formatter={(v, name) => [v?.toFixed(4), name]} />
                          <Legend />
                          <Line type="monotone" dataKey="basis_sell" stroke="#3f8600" strokeWidth={1.5} dot={false} name="卖出基差 (swap_bid - spot_ask)" />
                          <Line type="monotone" dataKey="basis_buy" stroke="#cf1322" strokeWidth={1.5} dot={false} name="买入基差 (swap_ask - spot_bid)" />
                        </LineChart>
                      </ResponsiveContainer>
                    </Card>
                  )}

                  {xauHistory.length > 0 && (
                    <Card title="最近数据" size="small">
                      <Table
                        dataSource={[...xauHistory].reverse().slice(0, 100)}
                        rowKey="timestamp"
                        size="small"
                        pagination={{ pageSize: 20 }}
                        columns={[
                          { title: '时间', dataIndex: 'time', key: 'time', width: 90 },
                          { title: '现货Ask', dataIndex: 'spot_ask', key: 'spot_ask', render: v => v?.toFixed(2) },
                          { title: '现货Bid', dataIndex: 'spot_bid', key: 'spot_bid', render: v => v?.toFixed(2) },
                          { title: '永续Ask', dataIndex: 'swap_ask', key: 'swap_ask', render: v => v?.toFixed(2) },
                          { title: '永续Bid', dataIndex: 'swap_bid', key: 'swap_bid', render: v => v?.toFixed(2) },
                          { title: '卖出基差', dataIndex: 'basis_sell', key: 'basis_sell',
                            render: (v, r) => (v ?? r.basis)?.toFixed(4),
                            sorter: (a, b) => (a.basis_sell ?? a.basis) - (b.basis_sell ?? b.basis) },
                          { title: '买入基差', dataIndex: 'basis_buy', key: 'basis_buy',
                            render: v => v?.toFixed(4) },
                          { title: '资金费率', dataIndex: 'funding_rate', key: 'funding_rate',
                            render: v => v != null ? (v * 100).toFixed(6) + '%' : '-' },
                        ]}
                      />
                    </Card>
                  )}

                  <Divider>永续-现货套利回测</Divider>
                  <Card size="small" title="回测参数">
                    <Form layout="inline" onFinish={handleArbBacktest}
                      initialValues={{ open_threshold: 8, close_threshold: 2, quantity: 1, fee_rate: 0.0005, funding_interval_hours: 8 }}>
                      <Form.Item label="开仓基差" name="open_threshold" rules={[{ required: true }]}>
                        <InputNumber step={0.5} style={{ width: 100 }} />
                      </Form.Item>
                      <Form.Item label="平仓基差" name="close_threshold" rules={[{ required: true }]}>
                        <InputNumber step={0.5} style={{ width: 100 }} />
                      </Form.Item>
                      <Form.Item label="数量(oz)" name="quantity" rules={[{ required: true }]}>
                        <InputNumber min={0.01} step={0.1} style={{ width: 100 }} />
                      </Form.Item>
                      <Form.Item label="手续费率" name="fee_rate" rules={[{ required: true }]}>
                        <InputNumber min={0} max={0.01} step={0.0001} style={{ width: 120 }} />
                      </Form.Item>
                      <Form.Item label="资金费间隔(h)" name="funding_interval_hours" rules={[{ required: true }]}>
                        <InputNumber min={1} max={24} style={{ width: 100 }} />
                      </Form.Item>
                      <Form.Item>
                        <Button type="primary" htmlType="submit" loading={arbLoading}>运行回测</Button>
                      </Form.Item>
                    </Form>
                  </Card>

                  {arbResult && (
                    <>
                      <Card size="small" title="回测结果">
                        <Row gutter={16}>
                          <Col span={3}><Statistic title="交易次数" value={arbResult.summary.total_trades} /></Col>
                          <Col span={4}><Statistic title="基差盈亏" value={arbResult.summary.total_pnl} precision={4}
                            valueStyle={{ color: arbResult.summary.total_pnl >= 0 ? '#3f8600' : '#cf1322' }} /></Col>
                          <Col span={4}><Statistic title="资金费收入" value={arbResult.summary.total_funding} precision={4}
                            valueStyle={{ color: '#1890ff' }} /></Col>
                          <Col span={3}><Statistic title="手续费" value={arbResult.summary.total_fees} precision={4}
                            valueStyle={{ color: '#cf1322' }} /></Col>
                          <Col span={4}><Statistic title="净盈亏" value={arbResult.summary.net_pnl} precision={4}
                            valueStyle={{ color: arbResult.summary.net_pnl >= 0 ? '#3f8600' : '#cf1322' }} /></Col>
                          <Col span={3}><Statistic title="未实现" value={arbResult.summary.unrealized} precision={4} /></Col>
                          <Col span={3}><Statistic title="数据点" value={arbResult.summary.data_points} /></Col>
                        </Row>
                        {arbResult.summary.in_position && (
                          <Alert type="warning" showIcon style={{ marginTop: 8 }} message="当前仍持有仓位（未平仓）" />
                        )}
                      </Card>

                      {arbResult.equity_curve?.length > 0 && (
                        <Card size="small" title="权益曲线 & 基差">
                          <ResponsiveContainer width="100%" height={300}>
                            <LineChart data={arbResult.equity_curve}>
                              <CartesianGrid strokeDasharray="3 3" />
                              <XAxis dataKey="timestamp" tick={{ fontSize: 10 }} interval="preserveStartEnd"
                                tickFormatter={v => new Date(v * 1000).toLocaleTimeString()} />
                              <YAxis yAxisId="eq" tick={{ fontSize: 11 }}
                                label={{ value: '权益', angle: -90, position: 'insideLeft', fontSize: 12 }} />
                              <YAxis yAxisId="basis" orientation="right" tick={{ fontSize: 11 }}
                                label={{ value: '基差', angle: 90, position: 'insideRight', fontSize: 12 }} />
                              <Tooltip labelFormatter={v => new Date(v * 1000).toLocaleString()}
                                formatter={(v, name) => [typeof v === 'number' ? v.toFixed(4) : v, name]} />
                              <Legend />
                              <Line yAxisId="eq" type="monotone" dataKey="equity" stroke="#1890ff" strokeWidth={2} dot={false} name="权益" />
                              <Line yAxisId="basis" type="monotone" dataKey="basis_sell" stroke="#3f8600" strokeWidth={1} dot={false} name="卖出基差" />
                              <ReferenceLine yAxisId="basis" y={arbResult.params.open_threshold} stroke="#ff4d4f" strokeDasharray="3 3" label="开仓" />
                              <ReferenceLine yAxisId="basis" y={arbResult.params.close_threshold} stroke="#52c41a" strokeDasharray="3 3" label="平仓" />
                            </LineChart>
                          </ResponsiveContainer>
                        </Card>
                      )}

                      {arbResult.trades?.length > 0 && (
                        <Card size="small" title={`交易明细 (${arbResult.trades.length} 笔)`}>
                          <Table
                            dataSource={arbResult.trades}
                            rowKey="open_ts"
                            size="small"
                            pagination={{ pageSize: 20 }}
                            columns={[
                              { title: '开仓时间', dataIndex: 'open_ts', key: 'open_ts', width: 170,
                                render: v => new Date(v * 1000).toLocaleString() },
                              { title: '平仓时间', dataIndex: 'close_ts', key: 'close_ts', width: 170,
                                render: v => new Date(v * 1000).toLocaleString() },
                              { title: '持仓(秒)', dataIndex: 'hold_seconds', key: 'hold_seconds', width: 80 },
                              { title: '入场基差', dataIndex: 'entry_basis', key: 'entry_basis',
                                render: v => v?.toFixed(4) },
                              { title: '出场基差', dataIndex: 'exit_basis', key: 'exit_basis',
                                render: v => v?.toFixed(4) },
                              { title: '现货盈亏', dataIndex: 'spot_pnl', key: 'spot_pnl',
                                render: v => <span style={{ color: v >= 0 ? '#3f8600' : '#cf1322' }}>{v?.toFixed(4)}</span> },
                              { title: '永续盈亏', dataIndex: 'swap_pnl', key: 'swap_pnl',
                                render: v => <span style={{ color: v >= 0 ? '#3f8600' : '#cf1322' }}>{v?.toFixed(4)}</span> },
                              { title: '交易盈亏', dataIndex: 'trade_pnl', key: 'trade_pnl',
                                render: v => <span style={{ color: v >= 0 ? '#3f8600' : '#cf1322' }}>{v?.toFixed(4)}</span> },
                              { title: '手续费', dataIndex: 'fees', key: 'fees',
                                render: v => v?.toFixed(4) },
                              { title: '累计盈亏', dataIndex: 'cumulative_pnl', key: 'cumulative_pnl',
                                render: v => <span style={{ color: v >= 0 ? '#3f8600' : '#cf1322' }}>{v?.toFixed(4)}</span> },
                            ]}
                          />
                        </Card>
                      )}
                    </>
                  )}
                </Space>
              ),
            },
          ]} />
        </Spin>
      </Content>

      <Footer style={{ textAlign: 'center', color: '#999' }}>
        Deribit 数据调试工具 — 用于测试API连通性和数据采集
      </Footer>
    </Layout>
  );
}
