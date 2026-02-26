/**
 * 数据中心 — 管理回测所需的历史数据
 */
import { useState, useEffect, useCallback, useRef } from 'react';
import {
  Layout, Card, Row, Col, Statistic, Button, Select, DatePicker,
  Table, Space, Tag, message, Progress, Typography, Popconfirm,
  Tabs, Switch, InputNumber, Form, Modal, Tooltip, Spin,
} from 'antd';
import {
  DatabaseOutlined, CloudDownloadOutlined, DeleteOutlined,
  ReloadOutlined, LineChartOutlined, BarChartOutlined,
  HomeOutlined, WarningOutlined, EditOutlined, SaveOutlined,
  CloseOutlined, FilterOutlined, ScissorOutlined, PauseCircleOutlined,
  ApiOutlined, CheckCircleOutlined, CloseCircleOutlined,
  HeatMapOutlined,
  ThunderboltOutlined, PlayCircleOutlined, CameraOutlined,
} from '@ant-design/icons';
import { Link } from 'react-router-dom';
import dayjs from 'dayjs';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid,
  Tooltip as RTooltip, Legend, ResponsiveContainer,
} from 'recharts';
import { dataCenterService } from '../services/dataCenterService';

const { Content } = Layout;
const { Title, Text } = Typography;
const { RangePicker } = DatePicker;

const SOURCES = [
  { value: 'deribit', label: 'Deribit (BTC/ETH 期权)' },
  { value: 'okx', label: 'OKX (BTC/ETH 指数)' },
];

const UNDERLYINGS = {
  deribit: [
    { value: 'BTC', label: 'BTC' },
    { value: 'ETH', label: 'ETH' },
  ],
  okx: [
    { value: 'BTC', label: 'BTC-USD' },
    { value: 'ETH', label: 'ETH-USD' },
  ],
};

const TIME_PRESETS = [
  { label: '近1年', months: 12 },
  { label: '近2年', months: 24 },
  { label: '近3年', months: 36 },
  { label: '近5年', months: 60 },
];

export default function DataCenter() {
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(false);
  const [collecting, setCollecting] = useState(false);
  const [collectProgress, setCollectProgress] = useState(null);

  // Collect form
  const [source, setSource] = useState('deribit');
  const [underlying, setUnderlying] = useState('BTC');
  const [dateRange, setDateRange] = useState([dayjs().subtract(2, 'year'), dayjs()]);
  const [collectIV, setCollectIV] = useState(false);
  const [ivSampleInterval, setIvSampleInterval] = useState(7);

  // Editable IV data
  const [ivEditable, setIvEditable] = useState(null);
  const [ivEditLoading, setIvEditLoading] = useState(false);
  const [ivFilterExpiry, setIvFilterExpiry] = useState(null);
  const [ivFilterTarget, setIvFilterTarget] = useState(null);
  const [ivFilterType, setIvFilterType] = useState(null);
  const [ivStrikeRange, setIvStrikeRange] = useState([0, 200000]);
  const [editingIvId, setEditingIvId] = useState(null);
  const [editIvValues, setEditIvValues] = useState({});

  // Editable price data
  const [priceEditable, setPriceEditable] = useState(null);
  const [priceEditLoading, setPriceEditLoading] = useState(false);
  const [editingPriceId, setEditingPriceId] = useState(null);
  const [editPriceValue, setEditPriceValue] = useState(null);

  // Batch delete modal
  const [batchDeleteVisible, setBatchDeleteVisible] = useState(false);
  const [batchDeleteForm] = Form.useForm();

  // Proxy settings
  const [proxyEnabled, setProxyEnabled] = useState(false);
  const [proxyUrl, setProxyUrl] = useState('socks5://127.0.0.1:10808');
  const [proxyLoading, setProxyLoading] = useState(false);
  const [proxyTestResult, setProxyTestResult] = useState(null);
  const [proxyTestLoading, setProxyTestLoading] = useState(false);

  // Sentinel analysis
  const [sentinelData, setSentinelData] = useState(null);
  const [sentinelLoading, setSentinelLoading] = useState(false);
  const [sentinelRetrying, setSentinelRetrying] = useState(false);
  const [sentinelRetryProgress, setSentinelRetryProgress] = useState(null);
  const [selectedSentinelIds, setSelectedSentinelIds] = useState([]);
  const sentinelRetryRef = useRef(null);

  // Data availability heatmap
  const [heatmapDates, setHeatmapDates] = useState([]);
  const [heatmapTargetDate, setHeatmapTargetDate] = useState(null);
  const [heatmapOptionType, setHeatmapOptionType] = useState('PUT');
  const [heatmapData, setHeatmapData] = useState(null);
  const [heatmapLoading, setHeatmapLoading] = useState(false);
  const [heatmapUnderlying, setHeatmapUnderlying] = useState('BTC');

  // HF collector
  const [hfStatus, setHfStatus] = useState(null);
  const [hfUnderlying, setHfUnderlying] = useState('BTC');
  const [hfInterval, setHfInterval] = useState(60);
  const [hfDates, setHfDates] = useState([]);
  const [hfSelectedDate, setHfSelectedDate] = useState(null);
  const [hfTimes, setHfTimes] = useState([]);
  const [hfSelectedTime, setHfSelectedTime] = useState(null);
  const [hfOptionType, setHfOptionType] = useState('PUT');
  const [hfData, setHfData] = useState(null);
  const [hfLoading, setHfLoading] = useState(false);
  const [hfActionLoading, setHfActionLoading] = useState(false);
  const hfPollRef = useRef(null);

  // HF instrument time series chart
  const [hfSeriesInstrument, setHfSeriesInstrument] = useState(null); // {expiry, strike, option_type}
  const [hfSeriesData, setHfSeriesData] = useState(null);
  const [hfSeriesLoading, setHfSeriesLoading] = useState(false);
  const [hfSeriesUnit, setHfSeriesUnit] = useState('usd'); // 'usd' or 'btc'

  const loadStats = useCallback(async () => {
    setLoading(true);
    try {
      const data = await dataCenterService.getStats();
      setStats(data);
    } catch (e) {
      message.error('加载统计失败: ' + e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadStats(); }, [loadStats]);

  // ── Proxy ──
  const loadProxy = useCallback(async () => {
    try {
      const data = await dataCenterService.getProxy();
      setProxyEnabled(data.enabled);
      setProxyUrl(data.url || 'socks5://127.0.0.1:10808');
    } catch (e) { /* ignore */ }
  }, []);

  useEffect(() => { loadProxy(); }, [loadProxy]);

  const handleProxySave = async () => {
    setProxyLoading(true);
    try {
      const result = await dataCenterService.updateProxy(proxyEnabled, proxyUrl);
      message.success(result.message);
    } catch (e) {
      message.error('保存代理设置失败');
    } finally {
      setProxyLoading(false);
    }
  };

  const handleProxyTest = async () => {
    setProxyTestLoading(true);
    setProxyTestResult(null);
    try {
      const result = await dataCenterService.testProxy();
      setProxyTestResult(result);
    } catch (e) {
      message.error('测试失败: ' + e.message);
    } finally {
      setProxyTestLoading(false);
    }
  };

  // ── Collect ──
  const collectControllerRef = useRef(null);

  const handleCollect = () => {
    if (!dateRange || dateRange.length < 2) { message.warning('请选择时间范围'); return; }
    setCollecting(true);
    setCollectProgress({ pct: 0, message: '准备中...' });
    const controller = dataCenterService.collectStream(
      { source, underlying, start_date: dateRange[0].format('YYYY-MM-DD'),
        end_date: dateRange[1].format('YYYY-MM-DD'), collect_iv: collectIV,
        iv_sample_interval: ivSampleInterval },
      (prog) => setCollectProgress(prog),
      (result) => { message.success(result.message); setCollecting(false); setCollectProgress(null); collectControllerRef.current = null; loadStats(); },
      (err) => { message.error(err); setCollecting(false); setCollectProgress(null); collectControllerRef.current = null; },
    );
    collectControllerRef.current = controller;
  };

  const handleStopCollect = () => {
    if (collectControllerRef.current) {
      collectControllerRef.current.abort();
      collectControllerRef.current = null;
    }
    setCollecting(false);
    setCollectProgress(null);
    message.info('已停止数据收取，已收取的数据已保存');
    loadStats();
  };

  // ── IV editable ──
  const loadIvEditable = async () => {
    setIvEditLoading(true);
    try {
      const data = await dataCenterService.getIVDataEditable(
        underlying, ivFilterExpiry, ivFilterTarget, ivFilterType,
        ivStrikeRange[0] > 0 ? ivStrikeRange[0] : null,
        ivStrikeRange[1] < 200000 ? ivStrikeRange[1] : null,
        dateRange?.[0]?.format('YYYY-MM-DD'),
        dateRange?.[1]?.format('YYYY-MM-DD'),
      );
      setIvEditable(data);
      if (data.strike_range && data.strike_range[0] > 0) {
        setIvStrikeRange(data.strike_range);
      }
    } catch (e) {
      message.error('加载IV数据失败');
    } finally {
      setIvEditLoading(false);
    }
  };

  const handleSaveIv = async (record) => {
    try {
      await dataCenterService.updateIVRecord(record.id, editIvValues);
      message.success('已保存');
      setEditingIvId(null);
      setEditIvValues({});
      loadIvEditable();
    } catch (e) {
      message.error('保存失败');
    }
  };

  const handleDeleteIv = async (id) => {
    try {
      await dataCenterService.deleteIVRecord(id);
      message.success('已删除');
      loadIvEditable();
    } catch (e) {
      message.error('删除失败');
    }
  };

  // ── Price editable ──
  const loadPriceEditable = async () => {
    setPriceEditLoading(true);
    try {
      const data = await dataCenterService.getPricesEditable(
        source, underlying,
        dateRange?.[0]?.format('YYYY-MM-DD'),
        dateRange?.[1]?.format('YYYY-MM-DD'),
      );
      setPriceEditable(data);
    } catch (e) {
      message.error('加载价格数据失败');
    } finally {
      setPriceEditLoading(false);
    }
  };

  const handleSavePrice = async (record) => {
    try {
      if (source === 'deribit') {
        await dataCenterService.updateDeribitPrice(record.id, { close_price: editPriceValue });
      } else {
        await dataCenterService.updateOkxPrice(record.id, { close_price: editPriceValue });
      }
      message.success('已保存');
      setEditingPriceId(null);
      setEditPriceValue(null);
      loadPriceEditable();
    } catch (e) {
      message.error('保存失败');
    }
  };

  const handleDeletePrice = async (id) => {
    try {
      if (source === 'deribit') {
        await dataCenterService.deleteDeribitPrice(id);
      } else {
        await dataCenterService.deleteOkxPrice(id);
      }
      message.success('已删除');
      loadPriceEditable();
    } catch (e) {
      message.error('删除失败');
    }
  };

  // ── Batch delete IV ──
  const handleBatchDeleteIV = async () => {
    try {
      const vals = batchDeleteForm.getFieldsValue();
      const params = { underlying };
      if (vals.expiry_date) params.expiry_date = vals.expiry_date;
      if (vals.target_date) params.target_date = vals.target_date;
      if (vals.option_type) params.option_type = vals.option_type;
      if (vals.min_strike != null) params.min_strike = vals.min_strike;
      if (vals.max_strike != null) params.max_strike = vals.max_strike;
      const result = await dataCenterService.batchDeleteIV(params);
      message.success(result.message);
      setBatchDeleteVisible(false);
      loadIvEditable();
      loadStats();
    } catch (e) {
      message.error('批量删除失败');
    }
  };

  const handleClearCache = async (src) => {
    try { await dataCenterService.clearCache(src); message.success('缓存已清除'); loadStats(); }
    catch (e) { message.error('清除失败'); }
  };

  const handleClearSentinels = async () => {
    try { const r = await dataCenterService.clearSentinels(underlying); message.success(r.message); loadStats(); }
    catch (e) { message.error('清除失败'); }
  };

  // ── Sentinel analysis ──
  const loadSentinelDetails = async () => {
    setSentinelLoading(true);
    try {
      const data = await dataCenterService.getSentinelDetails(underlying);
      setSentinelData(data);
      setSelectedSentinelIds([]);
    } catch (e) {
      message.error('加载sentinel详情失败: ' + e.message);
    } finally {
      setSentinelLoading(false);
    }
  };

  const handleRetrySentinels = () => {
    if (selectedSentinelIds.length === 0) { message.warning('请先选择要重试的记录'); return; }
    setSentinelRetrying(true);
    setSentinelRetryProgress({ pct: 0, message: '准备中...' });
    const ctrl = dataCenterService.retrySentinelsStream(
      selectedSentinelIds,
      (prog) => setSentinelRetryProgress(prog),
      (result) => {
        message.success(`重试完成: 成功${result.success}, 失败${result.fail}`);
        setSentinelRetrying(false);
        setSentinelRetryProgress(null);
        sentinelRetryRef.current = null;
        loadSentinelDetails();
        loadStats();
      },
      (err) => {
        message.error('重试失败: ' + err);
        setSentinelRetrying(false);
        setSentinelRetryProgress(null);
        sentinelRetryRef.current = null;
      },
    );
    sentinelRetryRef.current = ctrl;
  };

  const handleStopRetry = () => {
    if (sentinelRetryRef.current) {
      sentinelRetryRef.current.abort();
      sentinelRetryRef.current = null;
    }
    setSentinelRetrying(false);
    setSentinelRetryProgress(null);
    message.info('已停止重试');
  };

  // ── HF collector handlers ──
  const loadHfStatus = async () => {
    try {
      const data = await dataCenterService.getHFStatus();
      setHfStatus(data);
    } catch (e) { /* ignore */ }
  };

  const loadHfDates = async (ul) => {
    try {
      const data = await dataCenterService.getHFDates(ul || hfUnderlying);
      setHfDates(data.dates || []);
    } catch (e) { message.error('加载日期失败'); }
  };

  const loadHfTimes = async (dateStr, ul) => {
    try {
      const data = await dataCenterService.getHFTimes(ul || hfUnderlying, dateStr);
      setHfTimes(data.times || []);
      if (data.times?.length > 0) {
        setHfSelectedTime(data.times[0]);
      }
    } catch (e) { message.error('加载时间列表失败'); }
  };

  const loadHfSnapshot = async (snapTime, ot, ul) => {
    setHfLoading(true);
    try {
      const data = await dataCenterService.getHFSnapshotData(
        ul || hfUnderlying, snapTime || hfSelectedTime, ot || hfOptionType
      );
      setHfData(data);
    } catch (e) {
      message.error('加载快照失败: ' + e.message);
    } finally {
      setHfLoading(false);
    }
  };

  const handleHfStart = async () => {
    setHfActionLoading(true);
    try {
      await dataCenterService.startHFCollector(hfUnderlying, hfInterval);
      message.success('收集器已启动');
      loadHfStatus();
    } catch (e) { message.error('启动失败: ' + e.message); }
    finally { setHfActionLoading(false); }
  };

  const handleHfStop = async () => {
    setHfActionLoading(true);
    try {
      await dataCenterService.stopHFCollector();
      message.success('收集器已停止');
      loadHfStatus();
    } catch (e) { message.error('停止失败'); }
    finally { setHfActionLoading(false); }
  };

  const handleHfManualSnapshot = async () => {
    setHfActionLoading(true);
    try {
      const r = await dataCenterService.manualSnapshot(hfUnderlying);
      message.success(`快照完成: ${r.saved_count} 条记录`);
      loadHfStatus();
      loadHfDates();
      if (hfSelectedDate) loadHfTimes(hfSelectedDate);
    } catch (e) { message.error('快照失败: ' + e.message); }
    finally { setHfActionLoading(false); }
  };

  // Load instrument time series for the chart
  const loadHfSeries = async (expiry, strike, optionType) => {
    setHfSeriesLoading(true);
    setHfSeriesInstrument({ expiry, strike, option_type: optionType || hfOptionType });
    try {
      const data = await dataCenterService.getHFInstrumentSeries(
        hfUnderlying, expiry, strike, optionType || hfOptionType
      );
      setHfSeriesData(data);
    } catch (e) {
      message.error('加载时间序列失败: ' + e.message);
    } finally {
      setHfSeriesLoading(false);
    }
  };

  // Auto-poll HF status + refresh times when collector is running
  useEffect(() => {
    loadHfStatus();
    const iv = setInterval(() => {
      loadHfStatus();
      // Auto-refresh dates and times when collector is running
      if (hfStatus?.running) {
        loadHfDates();
        if (hfSelectedDate) loadHfTimes(hfSelectedDate);
      }
    }, 15000);
    return () => clearInterval(iv);
  }, [hfStatus?.running, hfSelectedDate]);

    // ── Data availability heatmap ──
  const loadHeatmapDates = async (ul) => {
    try {
      const data = await dataCenterService.getAvailabilityDates(ul || heatmapUnderlying);
      setHeatmapDates(data.dates || []);
      if (data.dates?.length > 0 && !heatmapTargetDate) {
        setHeatmapTargetDate(data.dates[0]);
      }
    } catch (e) {
      message.error('加载日期列表失败');
    }
  };

  const loadHeatmapData = async (td, ot, ul) => {
    const targetDate = td || heatmapTargetDate;
    if (!targetDate) { message.warning('请选择查询日期'); return; }
    setHeatmapLoading(true);
    try {
      const data = await dataCenterService.getDataAvailability(
        ul || heatmapUnderlying, targetDate, ot || heatmapOptionType
      );
      setHeatmapData(data);
    } catch (e) {
      message.error('加载数据可得性失败: ' + e.message);
    } finally {
      setHeatmapLoading(false);
    }
  };

    // ── IV columns ──
  const ivColumns = [
    { title: '到期日', dataIndex: 'expiry_date', width: 100, fixed: 'left' },
    { title: '查询日', dataIndex: 'target_date', width: 100 },
    { title: '类型', dataIndex: 'option_type', width: 60,
      render: (v) => <Tag color={v === 'CALL' ? 'green' : 'red'}>{v}</Tag> },
    { title: '行权价', dataIndex: 'strike', width: 100,
      render: (v) => `$${Number(v).toLocaleString()}`,
      sorter: (a, b) => a.strike - b.strike },
    { title: 'IV', dataIndex: 'iv', width: 110,
      render: (v, record) => {
        if (editingIvId === record.id) {
          return <InputNumber size="small" style={{ width: 90 }}
            value={editIvValues.iv ?? v} step={0.01} min={0} max={10}
            onChange={(val) => setEditIvValues(prev => ({ ...prev, iv: val }))} />;
        }
        return `${(v * 100).toFixed(1)}%`;
      },
      sorter: (a, b) => a.iv - b.iv },
    { title: '价格(USD)', dataIndex: 'trade_price_usd', width: 120,
      render: (v, record) => {
        if (editingIvId === record.id) {
          return <InputNumber size="small" style={{ width: 100 }}
            value={editIvValues.trade_price_usd ?? v} step={1} min={0}
            onChange={(val) => setEditIvValues(prev => ({ ...prev, trade_price_usd: val }))} />;
        }
        return `$${Number(v).toFixed(2)}`;
      } },
    { title: '现货', dataIndex: 'spot_price', width: 100,
      render: (v) => `$${Number(v).toLocaleString()}` },
    { title: '合约', dataIndex: 'instrument', width: 200, ellipsis: true },
    { title: '操作', width: 120, fixed: 'right',
      render: (_, record) => {
        if (editingIvId === record.id) {
          return (
            <Space size="small">
              <Button type="link" size="small" icon={<SaveOutlined />}
                onClick={() => handleSaveIv(record)}>保存</Button>
              <Button type="link" size="small" icon={<CloseOutlined />}
                onClick={() => { setEditingIvId(null); setEditIvValues({}); }}>取消</Button>
            </Space>
          );
        }
        return (
          <Space size="small">
            <Button type="link" size="small" icon={<EditOutlined />}
              onClick={() => { setEditingIvId(record.id); setEditIvValues({ iv: record.iv, trade_price_usd: record.trade_price_usd }); }}>
              编辑
            </Button>
            <Popconfirm title="确定删除？" onConfirm={() => handleDeleteIv(record.id)}>
              <Button type="link" size="small" danger icon={<DeleteOutlined />} />
            </Popconfirm>
          </Space>
        );
      } },
  ];

  // ── Price columns ──
  const priceColumns = [
    { title: '日期', dataIndex: 'date', width: 120 },
    { title: '收盘价', dataIndex: 'close', width: 150,
      render: (v, record) => {
        if (editingPriceId === record.id) {
          return <InputNumber size="small" style={{ width: 130 }}
            value={editPriceValue ?? v} step={1} min={0}
            onChange={setEditPriceValue} />;
        }
        return v ? `$${Number(v).toLocaleString()}` : '-';
      },
      sorter: (a, b) => (a.close || 0) - (b.close || 0) },
    ...(source === 'okx' ? [
      { title: '开盘价', dataIndex: 'open', width: 120, render: (v) => v ? `$${Number(v).toLocaleString()}` : '-' },
      { title: '最高价', dataIndex: 'high', width: 120, render: (v) => v ? `$${Number(v).toLocaleString()}` : '-' },
      { title: '最低价', dataIndex: 'low', width: 120, render: (v) => v ? `$${Number(v).toLocaleString()}` : '-' },
    ] : []),
    { title: '操作', width: 120,
      render: (_, record) => {
        if (editingPriceId === record.id) {
          return (
            <Space size="small">
              <Button type="link" size="small" icon={<SaveOutlined />}
                onClick={() => handleSavePrice(record)}>保存</Button>
              <Button type="link" size="small" icon={<CloseOutlined />}
                onClick={() => { setEditingPriceId(null); setEditPriceValue(null); }}>取消</Button>
            </Space>
          );
        }
        return (
          <Space size="small">
            <Button type="link" size="small" icon={<EditOutlined />}
              onClick={() => { setEditingPriceId(record.id); setEditPriceValue(record.close); }}>编辑</Button>
            <Popconfirm title="确定删除？" onConfirm={() => handleDeletePrice(record.id)}>
              <Button type="link" size="small" danger icon={<DeleteOutlined />} />
            </Popconfirm>
          </Space>
        );
      } },
  ];

  return (
    <Layout style={{ minHeight: '100vh', background: '#f0f2f5' }}>
      <div style={{ background: '#001529', padding: '0 24px', display: 'flex', alignItems: 'center', height: 64 }}>
        <DatabaseOutlined style={{ fontSize: 24, color: '#1890ff', marginRight: 12 }} />
        <Title level={3} style={{ color: 'white', margin: 0, fontSize: 20 }}>数据中心</Title>
        <div style={{ flex: 1 }} />
        <Link to="/" style={{ color: '#ffffffb3', display: 'flex', alignItems: 'center', gap: 6 }}>
          <HomeOutlined /> 返回主页
        </Link>
      </div>

      <Content style={{ padding: 24 }}>
        {/* 统计概览 */}
        <Row gutter={16} style={{ marginBottom: 24 }}>
          <Col span={6}>
            <Card><Statistic title="Deribit 价格数据" value={stats?.deribit_prices?.reduce((s, r) => s + r.count, 0) || 0} suffix="条" prefix={<LineChartOutlined />} />
              {stats?.deribit_prices?.map((r, i) => (<Text key={i} type="secondary" style={{ display: 'block', fontSize: 12 }}>{r.underlying}: {r.min_date} ~ {r.max_date}</Text>))}
            </Card>
          </Col>
          <Col span={6}>
            <Card><Statistic title="Deribit IV 数据" value={stats?.deribit_iv?.reduce((s, r) => s + r.count, 0) || 0} suffix="条" prefix={<BarChartOutlined />} />
              {stats?.deribit_iv?.map((r, i) => (<Text key={i} type="secondary" style={{ display: 'block', fontSize: 12 }}>{r.underlying}: {r.expiry_count}个到期日, {r.min_date} ~ {r.max_date}</Text>))}
            </Card>
          </Col>
          <Col span={6}>
            <Card><Statistic title="OKX 价格数据" value={stats?.okx_prices?.reduce((s, r) => s + r.count, 0) || 0} suffix="条" prefix={<LineChartOutlined />} />
              {stats?.okx_prices?.map((r, i) => (<Text key={i} type="secondary" style={{ display: 'block', fontSize: 12 }}>{r.underlying}: {r.min_date} ~ {r.max_date}</Text>))}
            </Card>
          </Col>
          <Col span={6}>
            <Card><Statistic title="无数据标记" value={stats?.deribit_sentinels?.reduce((s, r) => s + r.no_data_count, 0) || 0} suffix="条" prefix={<WarningOutlined />} valueStyle={{ color: '#faad14' }} />
              <Text type="secondary" style={{ fontSize: 12 }}>已确认无数据的到期日（不会重复请求API）</Text>
            </Card>
          </Col>
        </Row>

        {/* 数据收取 */}
        <Card title="一键收取数据" style={{ marginBottom: 24 }}>
          <Row gutter={16} align="middle">
            <Col><Select value={source} onChange={(v) => { setSource(v); setUnderlying(UNDERLYINGS[v][0].value); }} style={{ width: 200 }} options={SOURCES} /></Col>
            <Col><Select value={underlying} onChange={setUnderlying} style={{ width: 120 }} options={UNDERLYINGS[source] || []} /></Col>
            <Col><RangePicker value={dateRange} onChange={setDateRange} /></Col>
            <Col><Space>{TIME_PRESETS.map((p) => (<Button key={p.months} size="small" onClick={() => setDateRange([dayjs().subtract(p.months, 'month'), dayjs()])}>{p.label}</Button>))}</Space></Col>
            {source === 'deribit' && (<Col><Space><Text>同时收取IV:</Text><Switch checked={collectIV} onChange={setCollectIV} size="small" /></Space></Col>)}
            {source === 'deribit' && collectIV && (
              <Col>
                <Space>
                  <Text>IV采样间隔:</Text>
                  <Select value={ivSampleInterval} onChange={setIvSampleInterval} size="small" style={{ width: 100 }}
                    options={[
                      { value: 1, label: '每天' },
                      { value: 3, label: '每3天' },
                      { value: 7, label: '每周' },
                      { value: 14, label: '每2周' },
                      { value: 30, label: '每月' },
                    ]} />
                </Space>
              </Col>
            )}
            <Col>
              <Space>
                <Button type="primary" icon={<CloudDownloadOutlined />} loading={collecting} onClick={handleCollect}>开始收取</Button>
                {collecting && (
                  <Button danger icon={<PauseCircleOutlined />} onClick={handleStopCollect}>停止</Button>
                )}
              </Space>
            </Col>
          </Row>
          {collectProgress && (<div style={{ marginTop: 16 }}><Progress percent={collectProgress.pct || 0} status="active" /><Text type="secondary">{collectProgress.message}</Text></div>)}
        </Card>

        {/* 数据查看 & 编辑 */}
        <Card>
          <Tabs items={[
            {
              key: 'iv-edit',
              label: '合约数据编辑',
              children: (
                <div>
                  <Card size="small" style={{ marginBottom: 16, background: '#fafafa' }}>
                    <Row gutter={[16, 8]} align="middle">
                      <Col><Text strong><FilterOutlined /> 筛选条件:</Text></Col>
                      <Col>
                        <Select value={underlying} onChange={setUnderlying} style={{ width: 100 }}
                          options={[{ value: 'BTC', label: 'BTC' }, { value: 'ETH', label: 'ETH' }]} />
                      </Col>
                      <Col>
                        <RangePicker value={dateRange} onChange={setDateRange} size="small" />
                      </Col>
                      <Col>
                        <Select value={ivFilterExpiry} onChange={setIvFilterExpiry} style={{ width: 140 }}
                          allowClear placeholder="到期日"
                          options={ivEditable?.expiry_dates?.map(d => ({ value: d, label: d })) || []} />
                      </Col>
                      <Col>
                        <Select value={ivFilterTarget} onChange={setIvFilterTarget} style={{ width: 140 }}
                          allowClear placeholder="查询日期"
                          options={ivEditable?.target_dates?.map(d => ({ value: d, label: d })) || []} />
                      </Col>
                      <Col>
                        <Select value={ivFilterType} onChange={setIvFilterType} style={{ width: 100 }}
                          allowClear placeholder="类型"
                          options={[{ value: 'CALL', label: 'CALL' }, { value: 'PUT', label: 'PUT' }]} />
                      </Col>
                      <Col flex="auto">
                        <Text style={{ marginRight: 8 }}>行权价范围:</Text>
                        <InputNumber size="small" style={{ width: 100 }} value={ivStrikeRange[0]}
                          onChange={(v) => setIvStrikeRange([v || 0, ivStrikeRange[1]])} />
                        <Text style={{ margin: '0 4px' }}>~</Text>
                        <InputNumber size="small" style={{ width: 100 }} value={ivStrikeRange[1]}
                          onChange={(v) => setIvStrikeRange([ivStrikeRange[0], v || 200000])} />
                      </Col>
                      <Col>
                        <Space>
                          <Button type="primary" icon={<ReloadOutlined />} onClick={loadIvEditable} loading={ivEditLoading}>加载</Button>
                          <Button icon={<ScissorOutlined />} onClick={() => { batchDeleteForm.resetFields(); setBatchDeleteVisible(true); }}>批量删除</Button>
                        </Space>
                      </Col>
                    </Row>
                  </Card>
                  {ivEditable && (
                    <div>
                      <Text type="secondary" style={{ marginBottom: 8, display: 'block' }}>
                        {ivEditable.underlying}: {ivEditable.total} 条记录,
                        行权价范围 ${ivEditable.strike_range[0]?.toLocaleString()} ~ ${ivEditable.strike_range[1]?.toLocaleString()},
                        {ivEditable.expiry_dates?.length || 0} 个到期日
                      </Text>
                      <Table
                        dataSource={ivEditable.data}
                        columns={ivColumns}
                        rowKey="id"
                        size="small"
                        pagination={{ pageSize: 20, showSizeChanger: true, pageSizeOptions: [10, 20, 50, 100] }}
                        scroll={{ x: 1100, y: 500 }}
                      />
                    </div>
                  )}
                </div>
              ),
            },
            {
              key: 'price-edit',
              label: '价格数据编辑',
              children: (
                <div>
                  <Space style={{ marginBottom: 16 }}>
                    <Select value={source} onChange={(v) => { setSource(v); setUnderlying(UNDERLYINGS[v][0].value); }}
                      style={{ width: 200 }} options={SOURCES} />
                    <Select value={underlying} onChange={setUnderlying}
                      style={{ width: 120 }} options={UNDERLYINGS[source] || []} />
                    <RangePicker value={dateRange} onChange={setDateRange} />
                    <Button type="primary" icon={<ReloadOutlined />} onClick={loadPriceEditable} loading={priceEditLoading}>加载</Button>
                  </Space>
                  {priceEditable && (
                    <div>
                      <Text type="secondary" style={{ marginBottom: 8, display: 'block' }}>
                        {priceEditable.source}/{priceEditable.underlying}: {priceEditable.count} 条记录
                      </Text>
                      <Table
                        dataSource={priceEditable.data}
                        columns={priceColumns}
                        rowKey="id"
                        size="small"
                        pagination={{ pageSize: 20, showSizeChanger: true, pageSizeOptions: [10, 20, 50, 100] }}
                        scroll={{ y: 500 }}
                      />
                    </div>
                  )}
                </div>
              ),
            },
            {
              key: 'manage',
              label: '数据管理',
              children: (
                <Space direction="vertical" style={{ width: '100%' }}>
                  <Card size="small" title={<><ApiOutlined /> 网络代理设置</>}>
                    <Row gutter={16} align="middle" style={{ marginBottom: 12 }}>
                      <Col>
                        <Space>
                          <Text>启用代理:</Text>
                          <Switch checked={proxyEnabled} onChange={(v) => setProxyEnabled(v)} />
                          <Tag color={proxyEnabled ? 'green' : 'default'}>{proxyEnabled ? '已启用' : '已关闭'}</Tag>
                        </Space>
                      </Col>
                      <Col flex="auto">
                        <Space>
                          <Text>代理地址:</Text>
                          <input
                            value={proxyUrl}
                            onChange={(e) => setProxyUrl(e.target.value)}
                            placeholder="socks5://127.0.0.1:10808"
                            style={{ width: 300, padding: '4px 8px', border: '1px solid #d9d9d9', borderRadius: 4 }}
                          />
                        </Space>
                      </Col>
                      <Col>
                        <Space>
                          <Button type="primary" icon={<SaveOutlined />} onClick={handleProxySave} loading={proxyLoading}>保存</Button>
                          <Button icon={<ApiOutlined />} onClick={handleProxyTest} loading={proxyTestLoading}>测试连接</Button>
                        </Space>
                      </Col>
                    </Row>
                    <Text type="secondary" style={{ display: 'block', marginBottom: 8 }}>
                      支持 socks5 和 http 代理，例如 v2rayN 默认: socks5://127.0.0.1:10808 或 http://127.0.0.1:10809
                    </Text>
                    {proxyTestResult && (
                      <Row gutter={16} style={{ marginTop: 8 }}>
                        <Col span={12}>
                          <Card size="small" title="直连">
                            {proxyTestResult.direct?.ok
                              ? <Text type="success"><CheckCircleOutlined /> 连接成功 (HTTP {proxyTestResult.direct.status})</Text>
                              : <Text type="danger"><CloseCircleOutlined /> 连接失败: {proxyTestResult.direct?.error}</Text>}
                          </Card>
                        </Col>
                        <Col span={12}>
                          <Card size="small" title="代理">
                            {proxyTestResult.proxy?.ok
                              ? <Text type="success"><CheckCircleOutlined /> 连接成功 (HTTP {proxyTestResult.proxy.status})</Text>
                              : <Text type="danger"><CloseCircleOutlined /> 连接失败: {proxyTestResult.proxy?.error}</Text>}
                          </Card>
                        </Col>
                      </Row>
                    )}
                  </Card>
                  <Card size="small" title="缓存统计">
                    <Button icon={<ReloadOutlined />} onClick={loadStats} loading={loading} style={{ marginBottom: 16 }}>刷新统计</Button>
                    {stats && (
                      <Row gutter={16}>
                        <Col span={12}>
                          <Title level={5}>Deribit</Title>
                          {stats.deribit_prices?.map((r, i) => (<div key={i}><Tag color="blue">{r.underlying}</Tag>价格: {r.count}条 ({r.min_date} ~ {r.max_date})</div>))}
                          {stats.deribit_iv?.map((r, i) => (<div key={i}><Tag color="purple">{r.underlying}</Tag>IV: {r.count}条, {r.expiry_count}个到期日</div>))}
                          {stats.deribit_sentinels?.map((r, i) => (<div key={i}><Tag color="orange">{r.underlying}</Tag>无数据标记: {r.no_data_count}条</div>))}
                        </Col>
                        <Col span={12}>
                          <Title level={5}>OKX</Title>
                          {stats.okx_prices?.map((r, i) => (<div key={i}><Tag color="green">{r.underlying}</Tag>价格: {r.count}条 ({r.min_date} ~ {r.max_date})</div>))}
                        </Col>
                      </Row>
                    )}
                  </Card>
                  <Card size="small" title="危险操作">
                    <Space>
                      <Popconfirm title="确定清除所有缓存数据？" onConfirm={() => handleClearCache()}><Button danger icon={<DeleteOutlined />}>清除全部缓存</Button></Popconfirm>
                      <Popconfirm title="确定清除所有Deribit缓存？" onConfirm={() => handleClearCache('deribit')}><Button danger icon={<DeleteOutlined />}>清除Deribit缓存</Button></Popconfirm>
                      <Popconfirm title="确定清除所有OKX缓存？" onConfirm={() => handleClearCache('okx')}><Button danger icon={<DeleteOutlined />}>清除OKX缓存</Button></Popconfirm>
                      <Popconfirm title="确定清除所有无数据标记？" onConfirm={handleClearSentinels}><Button danger icon={<DeleteOutlined />}>清除无数据标记</Button></Popconfirm>
                    </Space>
                  </Card>
                </Space>
              ),
            },
            {
              key: 'heatmap',
              label: <span><HeatMapOutlined /> 数据可得性</span>,
              children: (
                <div>
                  <Card size="small" style={{ marginBottom: 16, background: '#f6ffed', borderColor: '#b7eb8f' }}>
                    <Row gutter={16} align="middle">
                      <Col>
                        <Select value={heatmapUnderlying} onChange={(v) => { setHeatmapUnderlying(v); setHeatmapDates([]); setHeatmapTargetDate(null); setHeatmapData(null); loadHeatmapDates(v); }}
                          style={{ width: 100 }}
                          options={[{ value: 'BTC', label: 'BTC' }, { value: 'ETH', label: 'ETH' }]} />
                      </Col>
                      <Col>
                        <Button onClick={() => loadHeatmapDates()} icon={<ReloadOutlined />}>加载日期</Button>
                      </Col>
                      <Col>
                        <Select
                          value={heatmapTargetDate}
                          onChange={(v) => { setHeatmapTargetDate(v); loadHeatmapData(v); }}
                          style={{ width: 160 }}
                          showSearch
                          placeholder="选择查询日期"
                          options={heatmapDates.map(d => ({ value: d, label: d }))}
                        />
                      </Col>
                      <Col>
                        <Select value={heatmapOptionType} onChange={(v) => { setHeatmapOptionType(v); if (heatmapTargetDate) loadHeatmapData(heatmapTargetDate, v); }}
                          style={{ width: 100 }}
                          options={[{ value: 'PUT', label: 'PUT' }, { value: 'CALL', label: 'CALL' }]} />
                      </Col>
                      <Col>
                        <Button type="primary" icon={<ReloadOutlined />} onClick={() => loadHeatmapData()} loading={heatmapLoading}>查询</Button>
                      </Col>
                      {heatmapData?.summary && (
                        <Col>
                          <Space>
                            <Tag color="green">真实: {heatmapData.summary.real}</Tag>
                            <Tag color="gold">估算: {heatmapData.summary.estimated || 0}</Tag>
                            <Tag color="red">无数据: {heatmapData.summary.no_data}</Tag>
                            <Tag>总计: {heatmapData.summary.total}</Tag>
                            {heatmapData.spot_price && <Tag color="blue">现货: ${Number(heatmapData.spot_price).toLocaleString()}</Tag>}
                          </Space>
                        </Col>
                      )}
                    </Row>
                  </Card>

                  {heatmapLoading && <div style={{ textAlign: 'center', padding: 40 }}><Spin size="large" /></div>}

                  {heatmapData && !heatmapLoading && heatmapData.strikes?.length > 0 && (
                    <Card size="small" title={`${heatmapData.underlying} ${heatmapData.option_type} 数据可得性 - ${heatmapData.target_date}`}>
                      <div style={{ overflowX: 'auto', overflowY: 'auto', maxHeight: 600 }}>
                        <table style={{ borderCollapse: 'collapse', fontSize: 11 }}>
                          <thead>
                            <tr>
                              <th style={{ padding: '4px 8px', background: '#fafafa', border: '1px solid #e8e8e8', position: 'sticky', left: 0, zIndex: 2, minWidth: 80 }}>
                                Strike \ Expiry
                              </th>
                              {heatmapData.expiries.map(exp => (
                                <th key={exp} style={{ padding: '4px 6px', background: '#fafafa', border: '1px solid #e8e8e8', whiteSpace: 'nowrap', writingMode: 'vertical-lr', textOrientation: 'mixed', height: 80 }}>
                                  {exp}
                                </th>
                              ))}
                            </tr>
                          </thead>
                          <tbody>
                            {(() => {
                              const cellMap = {};
                              (heatmapData.cells || []).forEach(c => {
                                cellMap[`${c.expiry}_${c.strike}`] = c;
                              });
                              return heatmapData.strikes.map(strike => {
                              const isAtm = heatmapData.spot_price && Math.abs(strike - heatmapData.spot_price) / heatmapData.spot_price < 0.05;
                              return (
                                <tr key={strike}>
                                  <td style={{ padding: '3px 8px', background: isAtm ? '#e6f7ff' : '#fafafa', border: '1px solid #e8e8e8', fontWeight: isAtm ? 'bold' : 'normal', position: 'sticky', left: 0, zIndex: 1, whiteSpace: 'nowrap' }}>
                                    {Number(strike).toLocaleString()}
                                    {isAtm && ' \u2605'}
                                  </td>
                                  {heatmapData.expiries.map(exp => {
                                    const cell = cellMap[`${exp}_${strike}`];
                                    const status = cell ? cell.status : 'no_data';
                                    const bgColor = status === 'real' ? '#52c41a' : status === 'estimated' ? '#faad14' : status === 'no_data' ? '#cf1322' : '#d9d9d9';
                                    const tipParts = [`${exp} / $${Number(strike).toLocaleString()}`];
                                    if (cell?.iv) tipParts.push(`IV: ${(cell.iv * 100).toFixed(1)}%`);
                                    if (cell?.price_usd) tipParts.push(`Price: $${Number(cell.price_usd).toFixed(2)}`);
                                    if (status === 'estimated') tipParts.push('(插值估算)');
                                    if (cell?.info) tipParts.push(cell.info);
                                    const tip = tipParts.join('\n');
                                    return (
                                      <Tooltip key={`${exp}-${strike}`} title={<div style={{ whiteSpace: 'pre-line', fontSize: 11 }}>{tip}</div>}>
                                        <td style={{ width: 20, height: 20, minWidth: 20, background: bgColor, border: '1px solid #e8e8e8', cursor: 'pointer' }} />
                                      </Tooltip>
                                    );
                                  })}
                                </tr>
                              );
                            });
                            })()}
                          </tbody>
                        </table>
                      </div>
                      <div style={{ marginTop: 12 }}>
                        <Space>
                          <span><span style={{ display: 'inline-block', width: 14, height: 14, background: '#52c41a', border: '1px solid #e8e8e8', verticalAlign: 'middle', marginRight: 4 }} />真实数据</span>
                          <span><span style={{ display: 'inline-block', width: 14, height: 14, background: '#faad14', border: '1px solid #e8e8e8', verticalAlign: 'middle', marginRight: 4 }} />IV插值估算</span>
                          <span><span style={{ display: 'inline-block', width: 14, height: 14, background: '#cf1322', border: '1px solid #e8e8e8', verticalAlign: 'middle', marginRight: 4 }} />无数据/Sentinel</span>
                          <span><span style={{ display: 'inline-block', width: 14, height: 14, background: '#d9d9d9', border: '1px solid #e8e8e8', verticalAlign: 'middle', marginRight: 4 }} />无记录</span>
                        </Space>
                      </div>
                    </Card>
                  )}

                  {heatmapData && !heatmapLoading && (!heatmapData.strikes || heatmapData.strikes.length === 0) && (
                    <Card size="small">
                      <Text type="secondary">该日期没有IV数据记录</Text>
                    </Card>
                  )}

                  {!heatmapData && !heatmapLoading && (
                    <Card size="small">
                      <Text type="secondary">请先点击"加载日期"获取可用日期列表，然后选择日期查看数据可得性</Text>
                    </Card>
                  )}
                </div>
              ),
            },
            {
              key: 'hf-collector',
              label: <span><ThunderboltOutlined /> 实时高频收集</span>,
              children: (
                <div>
                  {/* 控制面板 */}
                  <Card size="small" style={{ marginBottom: 16, background: '#f0f5ff', borderColor: '#adc6ff' }}>
                    <Row gutter={16} align="middle" style={{ marginBottom: 8 }}>
                      <Col>
                        <Select value={hfUnderlying} onChange={(v) => { setHfUnderlying(v); setHfDates([]); setHfTimes([]); setHfData(null); }}
                          style={{ width: 100 }}
                          options={[{ value: 'BTC', label: 'BTC' }, { value: 'ETH', label: 'ETH' }]} />
                      </Col>
                      <Col>
                        <Space>
                          <Text>采集间隔:</Text>
                          <Select value={hfInterval} onChange={setHfInterval} style={{ width: 100 }}
                            options={[
                              { value: 30, label: '30秒' },
                              { value: 60, label: '1分钟' },
                              { value: 120, label: '2分钟' },
                              { value: 300, label: '5分钟' },
                            ]} />
                        </Space>
                      </Col>
                      <Col>
                        <Space>
                          {!hfStatus?.running ? (
                            <Button type="primary" icon={<PlayCircleOutlined />} onClick={handleHfStart} loading={hfActionLoading}>
                              启动收集
                            </Button>
                          ) : (
                            <Button danger icon={<PauseCircleOutlined />} onClick={handleHfStop} loading={hfActionLoading}>
                              停止收集
                            </Button>
                          )}
                          <Button icon={<CameraOutlined />} onClick={handleHfManualSnapshot} loading={hfActionLoading}>
                            手动快照
                          </Button>
                        </Space>
                      </Col>
                      <Col>
                        <Space>
                          <Tag color={hfStatus?.running ? 'green' : 'default'}>
                            {hfStatus?.running ? '运行中' : '已停止'}
                          </Tag>
                          {hfStatus?.last_snapshot && (
                            <Text type="secondary" style={{ fontSize: 12 }}>
                              最后快照: {hfStatus.last_snapshot?.substring(11, 19)} ({hfStatus.last_count}条)
                              | 总计: {hfStatus.total_snapshots}次
                            </Text>
                          )}
                          {hfStatus?.error && <Tag color="red">错误: {hfStatus.error}</Tag>}
                        </Space>
                      </Col>
                    </Row>
                    <Row gutter={16} align="middle">
                      <Col>
                        <Button size="small" onClick={() => loadHfDates()} icon={<ReloadOutlined />}>加载日期</Button>
                      </Col>
                      <Col>
                        <Select
                          value={hfSelectedDate}
                          onChange={(v) => { setHfSelectedDate(v); loadHfTimes(v); }}
                          style={{ width: 160 }}
                          showSearch
                          placeholder="选择日期"
                          options={hfDates.map(d => ({ value: d.date, label: `${d.date} (${d.snapshots}次)` }))}
                        />
                      </Col>
                      <Col>
                        <Select
                          value={hfSelectedTime}
                          onChange={(v) => { setHfSelectedTime(v); loadHfSnapshot(v); }}
                          style={{ width: 220 }}
                          showSearch
                          placeholder="选择时间点"
                          options={hfTimes.map(t => {
                            const label = t.length > 19 ? t.substring(11, 19) : t.substring(11, 19);
                            return { value: t, label: `${label} UTC` };
                          })}
                        />
                      </Col>
                      <Col>
                        <Select value={hfOptionType} onChange={(v) => { setHfOptionType(v); if (hfSelectedTime) loadHfSnapshot(hfSelectedTime, v); }}
                          style={{ width: 100 }}
                          options={[{ value: 'PUT', label: 'PUT' }, { value: 'CALL', label: 'CALL' }]} />
                      </Col>
                      <Col>
                        <Button type="primary" icon={<ReloadOutlined />} onClick={() => loadHfSnapshot()} loading={hfLoading}>查询</Button>
                      </Col>
                      {hfData?.summary && (
                        <Col>
                          <Space>
                            <Tag color="green">实时: {hfData.summary.real}</Tag>
                            <Tag color="gold">估算: {hfData.summary.estimated || 0}</Tag>
                            <Tag color="red">无数据: {hfData.summary.no_data}</Tag>
                            {hfData.spot_price && <Tag color="blue">现货: ${Number(hfData.spot_price).toLocaleString()}</Tag>}
                          </Space>
                        </Col>
                      )}
                    </Row>
                  </Card>

                  {hfLoading && <div style={{ textAlign: 'center', padding: 40 }}><Spin size="large" /></div>}

                  {hfData && !hfLoading && hfData.strikes?.length > 0 && (
                    <>
                    <Card size="small" title={`${hfData.underlying} ${hfData.option_type} 实时盘口 - ${hfData.snapshot_time?.substring(0, 19)} UTC`}>
                      <div style={{ overflowX: 'auto', overflowY: 'auto', maxHeight: 600 }}>
                        <table style={{ borderCollapse: 'collapse', fontSize: 11 }}>
                          <thead>
                            <tr>
                              <th style={{ padding: '4px 8px', background: '#fafafa', border: '1px solid #e8e8e8', position: 'sticky', left: 0, zIndex: 2, minWidth: 80 }}>
                                Strike \\ Expiry
                              </th>
                              {hfData.expiries.map(exp => (
                                <th key={exp} style={{ padding: '4px 6px', background: '#fafafa', border: '1px solid #e8e8e8', whiteSpace: 'nowrap', writingMode: 'vertical-lr', textOrientation: 'mixed', height: 80 }}>
                                  {exp}
                                </th>
                              ))}
                            </tr>
                          </thead>
                          <tbody>
                            {(() => {
                              const cellMap = {};
                              (hfData.cells || []).forEach(c => { cellMap[`${c.expiry}_${c.strike}`] = c; });
                              return hfData.strikes.map(strike => {
                                const isAtm = hfData.spot_price && Math.abs(strike - hfData.spot_price) / hfData.spot_price < 0.05;
                                return (
                                  <tr key={strike}>
                                    <td style={{ padding: '3px 8px', background: isAtm ? '#e6f7ff' : '#fafafa', border: '1px solid #e8e8e8', fontWeight: isAtm ? 'bold' : 'normal', position: 'sticky', left: 0, zIndex: 1, whiteSpace: 'nowrap' }}>
                                      {Number(strike).toLocaleString()}
                                      {isAtm && ' \u2605'}
                                    </td>
                                    {hfData.expiries.map(exp => {
                                      const cell = cellMap[`${exp}_${strike}`];
                                      const status = cell ? cell.status : 'no_data';
                                      const bgColor = status === 'real' ? '#52c41a' : status === 'estimated' ? '#faad14' : '#cf1322';
                                      const tipParts = [`${exp} / $${Number(strike).toLocaleString()}`];
                                      if (cell?.bid_usd != null) tipParts.push(`Bid: $${Number(cell.bid_usd).toFixed(2)}`);
                                      if (cell?.ask_usd != null) tipParts.push(`Ask: $${Number(cell.ask_usd).toFixed(2)}`);
                                      if (cell?.last_usd != null) tipParts.push(`Last: $${Number(cell.last_usd).toFixed(2)}`);
                                      if (cell?.mark_usd != null) tipParts.push(`Mark: $${Number(cell.mark_usd).toFixed(2)}`);
                                      if (cell?.iv) tipParts.push(`IV: ${(cell.iv > 1 ? cell.iv : cell.iv * 100).toFixed(1)}%`);
                                      if (cell?.volume != null) tipParts.push(`Vol: ${cell.volume}`);
                                      if (cell?.oi != null) tipParts.push(`OI: ${cell.oi}`);
                                      if (status === 'estimated') tipParts.push('(插值估算)');
                                      const tip = tipParts.join('\n');
                                      return (
                                        <Tooltip key={`${exp}-${strike}`} title={<div style={{ whiteSpace: 'pre-line', fontSize: 11 }}>{tip}</div>}>
                                          <td
                                            onClick={() => { if (status === 'real') loadHfSeries(exp, strike); }}
                                            style={{
                                              width: 20, height: 20, minWidth: 20, background: bgColor,
                                              border: (hfSeriesInstrument?.expiry === exp && hfSeriesInstrument?.strike === strike) ? '2px solid #1890ff' : '1px solid #e8e8e8',
                                              cursor: status === 'real' ? 'pointer' : 'default',
                                            }}
                                          />
                                        </Tooltip>
                                      );
                                    })}
                                  </tr>
                                );
                              });
                            })()}
                          </tbody>
                        </table>
                      </div>
                      <div style={{ marginTop: 12 }}>
                        <Space>
                          <span><span style={{ display: 'inline-block', width: 14, height: 14, background: '#52c41a', border: '1px solid #e8e8e8', verticalAlign: 'middle', marginRight: 4 }} />实时盘口</span>
                          <span><span style={{ display: 'inline-block', width: 14, height: 14, background: '#faad14', border: '1px solid #e8e8e8', verticalAlign: 'middle', marginRight: 4 }} />IV插值估算</span>
                          <span><span style={{ display: 'inline-block', width: 14, height: 14, background: '#cf1322', border: '1px solid #e8e8e8', verticalAlign: 'middle', marginRight: 4 }} />无数据</span>
                          <Text type="secondary" style={{ marginLeft: 16, fontSize: 12 }}>点击绿色格子查看该期权的时间序列图→</Text>
                        </Space>
                      </div>
                    </Card>

                    {/* 期权时间序列图 */}
                    {hfSeriesInstrument && (
                      <Card size="small" style={{ marginTop: 16 }}
                        title={
                          <Space>
                            <LineChartOutlined />
                            <span>{hfSeriesData?.instrument || `${hfSeriesInstrument.expiry} / ${Number(hfSeriesInstrument.strike).toLocaleString()} ${hfSeriesInstrument.option_type}`}</span>
                            <Tag color="blue">{hfSeriesData?.count || 0} 个数据点</Tag>
                          </Space>
                        }
                        extra={
                          <Space>
                            <Select value={hfSeriesUnit} onChange={setHfSeriesUnit} size="small" style={{ width: 90 }}
                              options={[{ value: 'usd', label: 'USD' }, { value: 'btc', label: 'BTC' }]} />
                            <Button size="small" icon={<ReloadOutlined />} onClick={() => loadHfSeries(hfSeriesInstrument.expiry, hfSeriesInstrument.strike, hfSeriesInstrument.option_type)} loading={hfSeriesLoading}>刷新</Button>
                            <Button size="small" onClick={() => { setHfSeriesInstrument(null); setHfSeriesData(null); }}>关闭</Button>
                          </Space>
                        }
                      >
                        {hfSeriesLoading && <div style={{ textAlign: 'center', padding: 40 }}><Spin /></div>}
                        {hfSeriesData && !hfSeriesLoading && hfSeriesData.series?.length > 0 && (
                          <ResponsiveContainer width="100%" height={350}>
                            <LineChart data={hfSeriesData.series.map(p => ({
                              ...p,
                              time_label: p.time ? p.time.substring(5, 7) + '/' + p.time.substring(8, 10) + ' ' + p.time.substring(11, 16) : '',
                              bid_val: hfSeriesUnit === 'usd' ? p.bid_usd : p.bid,
                              ask_val: hfSeriesUnit === 'usd' ? p.ask_usd : p.ask,
                              last_val: hfSeriesUnit === 'usd' ? p.last_usd : p.last,
                              mark_val: hfSeriesUnit === 'usd' ? p.mark_usd : p.mark,
                            }))}>
                              <CartesianGrid strokeDasharray="3 3" />
                              <XAxis dataKey="time_label" tick={{ fontSize: 10 }} />
                              <YAxis tick={{ fontSize: 10 }} domain={['auto', 'auto']}
                                tickFormatter={(v) => hfSeriesUnit === 'usd' ? `$${Number(v).toLocaleString()}` : Number(v).toFixed(4)} />
                              <RTooltip
                                contentStyle={{ fontSize: 11 }}
                                formatter={(val, name) => {
                                  if (val == null) return ['-', name];
                                  return [hfSeriesUnit === 'usd' ? `$${Number(val).toFixed(2)}` : Number(val).toFixed(6), name];
                                }}
                                labelFormatter={(label) => `时间: ${label} UTC`}
                              />
                              <Legend wrapperStyle={{ fontSize: 11 }} />
                              <Line type="monotone" dataKey="bid_val" name="Bid" stroke="#52c41a" dot={false} strokeWidth={1.5} connectNulls />
                              <Line type="monotone" dataKey="ask_val" name="Ask" stroke="#f5222d" dot={false} strokeWidth={1.5} connectNulls />
                              <Line type="monotone" dataKey="last_val" name="Last" stroke="#1890ff" dot={false} strokeWidth={2} connectNulls />
                              <Line type="monotone" dataKey="mark_val" name="Mark" stroke="#722ed1" dot={false} strokeWidth={1} strokeDasharray="5 5" connectNulls />
                            </LineChart>
                          </ResponsiveContainer>
                        )}
                        {hfSeriesData && !hfSeriesLoading && (!hfSeriesData.series || hfSeriesData.series.length === 0) && (
                          <Text type="secondary">该期权在当天没有时间序列数据</Text>
                        )}
                      </Card>
                    )}
                    </>
                  )}

                  {hfData && !hfLoading && (!hfData.strikes || hfData.strikes.length === 0) && (
                    <Card size="small">
                      <Text type="secondary">该时间点没有高频数据</Text>
                    </Card>
                  )}

                  {!hfData && !hfLoading && (
                    <Card size="small">
                      <Text type="secondary">点击"启动收集"开始实时收集数据，或点击"手动快照"获取一次快照。选择日期和时间查看历史快照。</Text>
                    </Card>
                  )}
                </div>
              ),
            },
            {
              key: 'sentinels',
              label: <span><WarningOutlined /> 无数据分析 ({sentinelData?.total || '?'})</span>,
              children: (
                <div>
                  <Card size="small" style={{ marginBottom: 16, background: '#fffbe6', borderColor: '#ffe58f' }}>
                    <Row gutter={16} align="middle">
                      <Col>
                        <Select value={underlying} onChange={setUnderlying} style={{ width: 100 }}
                          options={[{ value: 'BTC', label: 'BTC' }, { value: 'ETH', label: 'ETH' }]} />
                      </Col>
                      <Col>
                        <Button type="primary" icon={<ReloadOutlined />} onClick={loadSentinelDetails}
                          loading={sentinelLoading}>加载分析</Button>
                      </Col>
                      <Col>
                        <Button icon={<CloudDownloadOutlined />} onClick={handleRetrySentinels}
                          loading={sentinelRetrying}
                          disabled={selectedSentinelIds.length === 0}>
                          重试选中 ({selectedSentinelIds.length})
                        </Button>
                      </Col>
                      {sentinelRetrying && (
                        <Col>
                          <Button danger icon={<PauseCircleOutlined />} onClick={handleStopRetry}>停止</Button>
                        </Col>
                      )}
                      <Col>
                        <Button onClick={() => {
                          if (sentinelData?.sentinels) setSelectedSentinelIds(sentinelData.sentinels.map(s => s.id));
                        }} disabled={!sentinelData}>全选</Button>
                      </Col>
                      <Col>
                        <Button onClick={() => setSelectedSentinelIds([])} disabled={selectedSentinelIds.length === 0}>取消全选</Button>
                      </Col>
                    </Row>
                    {sentinelRetryProgress && (
                      <div style={{ marginTop: 12 }}>
                        <Progress percent={sentinelRetryProgress.pct || 0} status="active" />
                        <Text type="secondary">{sentinelRetryProgress.message}</Text>
                      </div>
                    )}
                  </Card>

                  {sentinelData && sentinelData.summary_by_expiry?.length > 0 && (
                    <Card size="small" title="按到期日汇总" style={{ marginBottom: 16 }}>
                      <Table
                        dataSource={sentinelData.summary_by_expiry.map((s, i) => ({ ...s, key: i }))}
                        size="small" pagination={false}
                        columns={[
                          { title: '到期日', dataIndex: 'key', width: 160 },
                          { title: '无数据条数', dataIndex: 'count', width: 100 },
                          { title: '可能原因', dataIndex: 'reasons',
                            render: (v) => v?.map((r, i) => <Tag key={i} color="orange" style={{ marginBottom: 2 }}>{r}</Tag>) },
                        ]}
                      />
                    </Card>
                  )}

                  {sentinelData && (
                    <Card size="small" title={`详细记录 (${sentinelData.total}条)`}>
                      <Table
                        dataSource={sentinelData.sentinels?.map(s => ({ ...s, key: s.id })) || []}
                        size="small"
                        pagination={{ pageSize: 30, showSizeChanger: true, pageSizeOptions: [30, 50, 100, 200] }}
                        scroll={{ x: 1400, y: 500 }}
                        rowSelection={{
                          selectedRowKeys: selectedSentinelIds,
                          onChange: (keys) => setSelectedSentinelIds(keys),
                        }}
                        columns={[
                          { title: '标的', dataIndex: 'underlying', width: 60 },
                          { title: '到期日', dataIndex: 'expiry_date', width: 100,
                            render: (v, r) => <span style={{ color: r.is_last_friday ? '#389e0d' : '#cf1322' }}>{v}</span> },
                          { title: '类型', dataIndex: 'option_type', width: 60,
                            render: (v) => <Tag color={v === 'CALL' ? 'green' : 'red'}>{v}</Tag> },
                          { title: '查询日', dataIndex: 'target_date', width: 100 },
                          { title: '现货价', dataIndex: 'spot_price', width: 100,
                            render: (v) => v ? `$${Number(v).toLocaleString()}` : '-' },
                          { title: '距到期', dataIndex: 'days_to_expiry', width: 70,
                            render: (v) => <span style={{ color: v < 0 ? '#cf1322' : v < 7 ? '#faad14' : '#389e0d' }}>{v}天</span> },
                          { title: '最后周五', dataIndex: 'is_last_friday', width: 80,
                            render: (v) => v ? <Tag color="green">是</Tag> : <Tag color="red">否</Tag> },
                          { title: '实际最后周五', dataIndex: 'actual_last_friday', width: 110 },
                          { title: '季度月', dataIndex: 'is_quarterly', width: 70,
                            render: (v) => v ? <Tag color="blue">是</Tag> : <Tag>否</Tag> },
                          { title: '同到期有数据', dataIndex: 'has_real_data_same_expiry', width: 100,
                            render: (v) => v ? <Tag color="green">有</Tag> : <Tag color="orange">无</Tag> },
                          { title: '可能原因', dataIndex: 'reasons', width: 300,
                            render: (v) => v?.map((r, i) => <div key={i} style={{ fontSize: 11, color: '#8c8c8c' }}>{r}</div>) },
                        ]}
                      />
                    </Card>
                  )}

                  {!sentinelData && !sentinelLoading && (
                    <Card size="small">
                      <Text type="secondary">点击"加载分析"查看无数据标记的详细信息和原因分析</Text>
                    </Card>
                  )}
                </div>
              ),
            },
          ]} />
        </Card>

        {/* 批量删除 Modal */}
        <Modal title="批量删除IV数据" open={batchDeleteVisible}
          onOk={handleBatchDeleteIV} onCancel={() => setBatchDeleteVisible(false)}
          okText="确认删除" okButtonProps={{ danger: true }}>
          <Form form={batchDeleteForm} layout="vertical">
            <Form.Item label="到期日" name="expiry_date">
              <Select allowClear placeholder="全部" options={ivEditable?.expiry_dates?.map(d => ({ value: d, label: d })) || []} />
            </Form.Item>
            <Form.Item label="查询日期" name="target_date">
              <Select allowClear placeholder="全部" options={ivEditable?.target_dates?.map(d => ({ value: d, label: d })) || []} />
            </Form.Item>
            <Form.Item label="期权类型" name="option_type">
              <Select allowClear placeholder="全部" options={[{ value: 'CALL', label: 'CALL' }, { value: 'PUT', label: 'PUT' }]} />
            </Form.Item>
            <Row gutter={16}>
              <Col span={12}><Form.Item label="最小行权价" name="min_strike"><InputNumber style={{ width: '100%' }} /></Form.Item></Col>
              <Col span={12}><Form.Item label="最大行权价" name="max_strike"><InputNumber style={{ width: '100%' }} /></Form.Item></Col>
            </Row>
            <Text type="warning">将删除 {underlying} 下符合以上条件的所有IV缓存记录，此操作不可撤销。</Text>
          </Form>
        </Modal>
      </Content>
    </Layout>
  );
}
