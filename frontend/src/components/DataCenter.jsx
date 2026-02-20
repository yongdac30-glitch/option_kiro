/**
 * 数据中心 — 管理回测所需的历史数据
 */
import { useState, useEffect, useCallback, useRef } from 'react';
import {
  Layout, Card, Row, Col, Statistic, Button, Select, DatePicker,
  Table, Space, Tag, message, Progress, Typography, Popconfirm,
  Tabs, Switch, InputNumber, Form, Modal,
} from 'antd';
import {
  DatabaseOutlined, CloudDownloadOutlined, DeleteOutlined,
  ReloadOutlined, LineChartOutlined, BarChartOutlined,
  HomeOutlined, WarningOutlined, EditOutlined, SaveOutlined,
  CloseOutlined, FilterOutlined, ScissorOutlined, PauseCircleOutlined,
} from '@ant-design/icons';
import { Link } from 'react-router-dom';
import dayjs from 'dayjs';
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
