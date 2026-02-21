/**
 * QQQ LEAPS 策略回测页面
 *
 * 基于 "Options with Davis" 的 QQQ LEAPS 策略:
 * - 买入深度实值(ITM) CALL LEAPS, Delta ≈ 0.90
 * - 到期前 60-90 天滚仓到下一个 1年+ 到期日
 * - 作为 QQQ 的杠杆替代持仓策略
 */
import { useState, useRef, useCallback } from 'react';
import {
  Layout, Card, Row, Col, Button, Select, DatePicker, InputNumber,
  Table, Space, Tag, message, Progress, Typography, Statistic, Slider,
  Descriptions, Tabs, Tooltip, Divider,
} from 'antd';
import {
  RocketOutlined, HomeOutlined, PlayCircleOutlined, PauseCircleOutlined,
  ArrowUpOutlined, ArrowDownOutlined, LineChartOutlined, SwapOutlined,
  InfoCircleOutlined, DollarOutlined, BarChartOutlined,
} from '@ant-design/icons';
import { Link } from 'react-router-dom';
import dayjs from 'dayjs';
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
  { value: 'TQQQ', label: 'TQQQ (3x纳指)' },
];

export default function QQQLeaps() {
  // Config
  const [ticker, setTicker] = useState('QQQ');
  const [dateRange, setDateRange] = useState([dayjs().subtract(3, 'year'), dayjs()]);
  const [initialCapital, setInitialCapital] = useState(50000);
  const [targetDelta, setTargetDelta] = useState(0.90);
  const [defaultIV, setDefaultIV] = useState(0.25);
  const [rollDTE, setRollDTE] = useState(75);
  const [minExpiryDays, setMinExpiryDays] = useState(365);
  const [numContracts, setNumContracts] = useState(1);

  // State
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState(null);
  const [result, setResult] = useState(null);
  const controllerRef = useRef(null);

  const handleRun = useCallback(() => {
    if (!dateRange || dateRange.length < 2) { message.warning('请选择时间范围'); return; }
    setRunning(true);
    setProgress({ pct: 0, status: '准备中...' });
    setResult(null);

    const params = {
      ticker,
      start_date: dateRange[0].format('YYYY-MM-DD'),
      end_date: dateRange[1].format('YYYY-MM-DD'),
      initial_capital: initialCapital,
      target_delta: targetDelta,
      default_iv: defaultIV,
      roll_dte: rollDTE,
      min_expiry_days: minExpiryDays,
      num_contracts: numContracts,
      compare_tickers: ['QQQ', 'SPY', 'XLK'],
    };

    const ctrl = qqqLeapsService.backtestStream(
      params,
      (prog) => setProgress(prog),
      (data) => { setResult(data); setRunning(false); setProgress(null); controllerRef.current = null; },
      (err) => { message.error(err); setRunning(false); setProgress(null); controllerRef.current = null; },
    );
    controllerRef.current = ctrl;
  }, [ticker, dateRange, initialCapital, targetDelta, defaultIV, rollDTE, minExpiryDays, numContracts]);

  const handleStop = () => {
    if (controllerRef.current) { controllerRef.current.abort(); controllerRef.current = null; }
    setRunning(false); setProgress(null);
    message.info('已停止');
  };

  const summary = result?.summary;
  const trades = result?.trades || [];
  const curve = result?.equity_curve || [];
  const compare = result?.compare || {};

  // Trade columns
  const tradeColumns = [
    { title: '日期', dataIndex: 'date', width: 100, fixed: 'left' },
    { title: '操作', dataIndex: 'action', width: 100,
      render: (v) => {
        const colors = { OPEN: 'green', CLOSE: 'red', ROLL_CLOSE: 'orange', ROLL_OPEN: 'blue' };
        const labels = { OPEN: '开仓', CLOSE: '平仓', ROLL_CLOSE: '滚仓平', ROLL_OPEN: '滚仓开' };
        return <Tag color={colors[v]}>{labels[v] || v}</Tag>;
      },
    },
    { title: '行权价', dataIndex: 'strike', width: 90, render: (v) => `$${v}` },
    { title: '到期日', dataIndex: 'expiry', width: 100 },
    { title: '现货', dataIndex: 'spot', width: 90, render: (v) => `$${v}` },
    { title: '期权价', dataIndex: 'option_price', width: 100, render: (v) => `$${v?.toFixed(2)}` },
    { title: 'Delta', dataIndex: 'delta', width: 70, render: (v) => v?.toFixed(3) },
    { title: '数量', dataIndex: 'quantity', width: 60 },
    { title: '现金流', dataIndex: 'cash_flow', width: 110,
      render: (v) => <Text type={v >= 0 ? 'success' : 'danger'}>${v?.toLocaleString()}</Text> },
    { title: 'PnL', dataIndex: 'pnl', width: 100,
      render: (v) => v ? <Text type={v >= 0 ? 'success' : 'danger'}>${v?.toFixed(2)}</Text> : '-' },
    { title: '说明', dataIndex: 'note', ellipsis: true },
  ];

  return (
    <Layout style={{ minHeight: '100vh', background: '#f0f2f5' }}>
      <div style={{ background: '#001529', padding: '0 24px', display: 'flex', alignItems: 'center', height: 64 }}>
        <RocketOutlined style={{ fontSize: 24, color: '#eb2f96', marginRight: 12 }} />
        <Title level={3} style={{ color: 'white', margin: 0, fontSize: 20 }}>QQQ LEAPS 策略回测</Title>
        <div style={{ flex: 1 }} />
        <Link to="/" style={{ color: '#ffffffb3', display: 'flex', alignItems: 'center', gap: 6 }}>
          <HomeOutlined /> 返回主页
        </Link>
      </div>

      <Content style={{ padding: 24 }}>
        {/* 策略说明 */}
        <Card size="small" style={{ marginBottom: 16, background: '#f6ffed', borderColor: '#b7eb8f' }}>
          <Row gutter={16}>
            <Col span={16}>
              <Text strong><InfoCircleOutlined /> 策略说明 (来源: Options with Davis)</Text>
              <Paragraph style={{ margin: '8px 0 0', fontSize: 13 }}>
                <ul style={{ margin: 0, paddingLeft: 20 }}>
                  <li>买入深度实值(ITM) CALL LEAPS，目标 Delta ≈ 0.90，最小化时间价值损耗</li>
                  <li>到期前 60-90 天滚仓(Roll)到下一个 1年以上到期日，避免 Theta 加速衰减</li>
                  <li>作为 QQQ 的杠杆替代持仓策略，用更少资金获得类似敞口</li>
                  <li>回测显示: 1年收益约66.3%(vs QQQ 30%), 5年收益约197%(vs 140%), 但回撤更大</li>
                  <li>滚仓时可能需要追加资金(Capital Top-up)，尤其在亏损时</li>
                </ul>
              </Paragraph>
            </Col>
            <Col span={8}>
              <Text strong>视频回测数据参考:</Text>
              <div style={{ fontSize: 12, marginTop: 4 }}>
                <div>1年: LEAPS +66.3% vs B&H +30%, 回撤 33.3% vs 13.6%</div>
                <div>5年: LEAPS +197% vs B&H +140%, 回撤 48.8% vs 35.6%</div>
                <div>杠杆倍数: 约 1.4-2.2x</div>
              </div>
            </Col>
          </Row>
        </Card>

        {/* 参数配置 */}
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
              <Text type="secondary" style={{ fontSize: 12 }}>目标Delta</Text>
              <Slider value={targetDelta} onChange={setTargetDelta}
                min={0.70} max={0.98} step={0.01}
                marks={{ 0.80: '0.80', 0.90: '0.90', 0.95: '0.95' }} />
            </Col>
            <Col span={2}>
              <Text type="secondary" style={{ fontSize: 12 }}>默认IV</Text>
              <InputNumber value={defaultIV} onChange={setDefaultIV}
                min={0.05} max={1.0} step={0.05} style={{ width: '100%' }} />
            </Col>
            <Col span={2}>
              <Text type="secondary" style={{ fontSize: 12 }}>滚仓DTE</Text>
              <InputNumber value={rollDTE} onChange={setRollDTE}
                min={30} max={120} step={5} style={{ width: '100%' }} />
            </Col>
            <Col span={2}>
              <Text type="secondary" style={{ fontSize: 12 }}>合约数</Text>
              <InputNumber value={numContracts} onChange={setNumContracts}
                min={1} max={100} style={{ width: '100%' }} />
            </Col>
            <Col span={2}>
              <Space style={{ marginTop: 18 }}>
                <Button type="primary" icon={<PlayCircleOutlined />}
                  loading={running} onClick={handleRun}>回测</Button>
                {running && <Button danger icon={<PauseCircleOutlined />} onClick={handleStop}>停止</Button>}
              </Space>
            </Col>
          </Row>
          <Row style={{ marginTop: 8 }}>
            <Col>
              <Space>
                {[1, 2, 3, 5].map((y) => (
                  <Button key={y} size="small"
                    onClick={() => setDateRange([dayjs().subtract(y, 'year'), dayjs()])}>
                    近{y}年
                  </Button>
                ))}
              </Space>
            </Col>
          </Row>
          {progress && (
            <div style={{ marginTop: 12 }}>
              <Progress percent={progress.pct || 0} status="active" />
              <Text type="secondary">{progress.status}</Text>
            </div>
          )}
        </Card>

        {/* 结果展示 */}
        {summary && (
          <>
            {/* 核心指标对比 */}
            <Row gutter={16} style={{ marginBottom: 16 }}>
              <Col span={12}>
                <Card title={<><RocketOutlined /> LEAPS 策略</>} size="small"
                  style={{ borderColor: '#1890ff' }}>
                  <Row gutter={16}>
                    <Col span={8}>
                      <Statistic title="总收益率" value={summary.return_on_capital_pct}
                        suffix="%" precision={2}
                        valueStyle={{ color: summary.return_on_capital_pct >= 0 ? '#3f8600' : '#cf1322' }}
                        prefix={summary.return_on_capital_pct >= 0 ? <ArrowUpOutlined /> : <ArrowDownOutlined />} />
                    </Col>
                    <Col span={8}>
                      <Statistic title="年化收益" value={summary.annualized_return_pct}
                        suffix="%" precision={2}
                        valueStyle={{ color: summary.annualized_return_pct >= 0 ? '#3f8600' : '#cf1322' }} />
                    </Col>
                    <Col span={8}>
                      <Statistic title="最大回撤" value={summary.max_drawdown_pct}
                        suffix="%" precision={2}
                        valueStyle={{ color: '#cf1322' }} />
                    </Col>
                  </Row>
                  <Divider style={{ margin: '12px 0' }} />
                  <Row gutter={16}>
                    <Col span={6}><Statistic title="Sharpe" value={summary.sharpe_ratio} precision={3} /></Col>
                    <Col span={6}><Statistic title="滚仓次数" value={summary.roll_count} /></Col>
                    <Col span={6}><Statistic title="追加资金" value={summary.total_topups} prefix="$"
                      precision={0} valueStyle={{ color: summary.total_topups > 0 ? '#faad14' : '#3f8600' }} /></Col>
                    <Col span={6}><Statistic title="杠杆倍数" value={summary.leverage_ratio} suffix="x" precision={2} /></Col>
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
                        suffix="%" precision={2}
                        valueStyle={{ color: '#cf1322' }} />
                    </Col>
                  </Row>
                  <Divider style={{ margin: '12px 0' }} />
                  <Row gutter={16}>
                    <Col span={8}><Statistic title="初始资金" value={summary.initial_capital} prefix="$" precision={0} /></Col>
                    <Col span={8}><Statistic title="总投入资金" value={summary.total_capital_used} prefix="$" precision={0} /></Col>
                    <Col span={8}><Statistic title="最终权益" value={summary.final_equity} prefix="$" precision={0}
                      valueStyle={{ color: summary.final_equity >= summary.total_capital_used ? '#3f8600' : '#cf1322' }} /></Col>
                  </Row>
                </Card>
              </Col>
            </Row>

            {/* ETF对比 */}
            {Object.keys(compare).length > 0 && (
              <Card title={<><BarChartOutlined /> ETF 收益对比</>} size="small" style={{ marginBottom: 16 }}>
                <Row gutter={16}>
                  <Col span={6}>
                    <Card size="small" style={{ background: '#e6f7ff' }}>
                      <Statistic title={`LEAPS ${summary.ticker}`}
                        value={summary.return_on_capital_pct} suffix="%" precision={2}
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

            {/* 权益曲线表格 + 交易记录 */}
            <Card>
              <Tabs items={[
                {
                  key: 'equity',
                  label: `权益曲线 (${curve.length}天)`,
                  children: (
                    <Table
                      dataSource={curve}
                      rowKey="date"
                      size="small"
                      pagination={{ pageSize: 30, showSizeChanger: true, pageSizeOptions: [20, 30, 50, 100] }}
                      scroll={{ y: 500 }}
                      columns={[
                        { title: '日期', dataIndex: 'date', width: 100 },
                        { title: '权益', dataIndex: 'equity', width: 120,
                          render: (v) => `$${v?.toLocaleString()}`,
                          sorter: (a, b) => a.equity - b.equity },
                        { title: '现货', dataIndex: 'spot', width: 100,
                          render: (v) => `$${v?.toLocaleString()}` },
                        { title: '现金', dataIndex: 'cash', width: 110,
                          render: (v) => `$${v?.toLocaleString()}` },
                        { title: '持仓市值', dataIndex: 'holdings', width: 110,
                          render: (v) => `$${v?.toLocaleString()}` },
                        { title: 'Delta', dataIndex: 'delta', width: 70,
                          render: (v) => v?.toFixed(3) },
                        { title: 'DTE', dataIndex: 'dte', width: 60 },
                        { title: '持仓', dataIndex: 'has_position', width: 60,
                          render: (v) => v ? <Tag color="green">是</Tag> : <Tag>否</Tag> },
                      ]}
                    />
                  ),
                },
                {
                  key: 'trades',
                  label: `交易记录 (${trades.length}笔)`,
                  children: (
                    <Table
                      dataSource={trades}
                      rowKey={(r, i) => `${r.date}-${r.action}-${i}`}
                      size="small"
                      pagination={{ pageSize: 20, showSizeChanger: true }}
                      scroll={{ x: 1200, y: 500 }}
                      columns={tradeColumns}
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
                      <Descriptions.Item label="滚仓DTE">{summary.roll_dte}天</Descriptions.Item>
                      <Descriptions.Item label="默认IV">{(summary.default_iv * 100).toFixed(0)}%</Descriptions.Item>
                      <Descriptions.Item label="合约乘数">100</Descriptions.Item>
                      <Descriptions.Item label="初始资金">${summary.initial_capital?.toLocaleString()}</Descriptions.Item>
                      <Descriptions.Item label="总投入">${summary.total_capital_used?.toLocaleString()}</Descriptions.Item>
                      <Descriptions.Item label="追加资金">${summary.total_topups?.toLocaleString()}</Descriptions.Item>
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
