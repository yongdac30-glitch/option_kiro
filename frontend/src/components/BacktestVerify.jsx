/**
 * Backtest Verification Page
 * Runs two simple sell-option strategies and shows step-by-step accounting
 */
import { useState } from 'react';
import {
  Layout, Card, Button, Row, Col, Typography, Space, Table, Tag,
  Statistic, Spin, Alert, Collapse, Descriptions,
} from 'antd';
import {
  ArrowLeftOutlined, ExperimentOutlined, CheckCircleOutlined,
} from '@ant-design/icons';
import { Link } from 'react-router-dom';
import api from '../services/api';

const { Header: AntHeader, Content, Footer } = Layout;
const { Title, Text } = Typography;

const STRATEGIES = [
  {
    name: '策略A: 卖出20% OTM PUT (单腿)',
    description: 'BTC, 卖出1张行权价偏移-20%的PUT, 每月滚动, 到期前1天平仓',
    params: {
      underlying: 'BTC',
      start_date: '2024-06-01',
      end_date: '2024-10-01',
      initial_capital: 10000,
      close_days_before_expiry: 1,
      legs: [{ option_type: 'PUT', strike_offset_pct: -0.20, quantity: -1, expiry_months: 1 }],
    },
  },
  {
    name: '策略B: 卖出10% OTM CALL (单腿)',
    description: 'BTC, 卖出1张行权价偏移+10%的CALL, 每月滚动, 到期前1天平仓',
    params: {
      underlying: 'BTC',
      start_date: '2024-06-01',
      end_date: '2024-10-01',
      initial_capital: 10000,
      close_days_before_expiry: 1,
      legs: [{ option_type: 'CALL', strike_offset_pct: 0.10, quantity: -1, expiry_months: 1 }],
    },
  },
];

function EventTag({ action }) {
  const map = {
    OPEN: { color: 'blue', label: '开仓' },
    CLOSE: { color: 'orange', label: '平仓' },
    EXPIRE: { color: 'red', label: '到期' },
  };
  const cfg = map[action] || { color: 'default', label: action };
  return <Tag color={cfg.color}>{cfg.label}</Tag>;
}

function StrategyResult({ name, result, loading }) {
  if (loading) return <Spin tip="正在运行验证回测..." />;
  if (!result) return null;

  const logColumns = [
    { title: '日期', dataIndex: 'date', key: 'date', width: 110 },
    { title: '现货', dataIndex: 'spot', key: 'spot', width: 100, render: (v) => '$' + v.toLocaleString() },
    { title: '现金', dataIndex: 'cash', key: 'cash', width: 110, render: (v) => '$' + v.toFixed(2) },
    { title: '未实现', dataIndex: 'unrealized', key: 'unrealized', width: 110,
      render: (v) => <span style={{ color: v >= 0 ? '#389e0d' : '#cf1322' }}>${v.toFixed(2)}</span> },
    { title: '总权益', dataIndex: 'total_equity', key: 'total_equity', width: 110,
      render: (v) => <span style={{ fontWeight: 600 }}>${v.toFixed(2)}</span> },
    { title: '持仓数', dataIndex: 'open_positions', key: 'open_positions', width: 70 },
  ];

  const tradeColumns = [
    { title: '开仓日', dataIndex: 'open_date', key: 'open_date', width: 100 },
    { title: '平仓日', dataIndex: 'close_date', key: 'close_date', width: 100 },
    { title: '合约', dataIndex: 'instrument', key: 'instrument', width: 220 },
    { title: '类型', dataIndex: 'option_type', key: 'option_type', width: 70,
      render: (v) => <Tag color={v === 'PUT' ? 'red' : 'green'}>{v}</Tag> },
    { title: '行权价', dataIndex: 'strike', key: 'strike', width: 100, render: (v) => '$' + v.toLocaleString() },
    { title: '数量', dataIndex: 'quantity', key: 'quantity', width: 60 },
    { title: '开仓价', dataIndex: 'open_price', key: 'open_price', width: 100, render: (v) => '$' + v.toFixed(2) },
    { title: '平仓价', dataIndex: 'close_price', key: 'close_price', width: 100, render: (v) => '$' + v.toFixed(2) },
    { title: '开仓现货', dataIndex: 'open_spot', key: 'open_spot', width: 100, render: (v) => '$' + v.toLocaleString() },
    { title: '平仓现货', dataIndex: 'close_spot', key: 'close_spot', width: 100, render: (v) => '$' + v.toLocaleString() },
    { title: '盈亏', dataIndex: 'pnl', key: 'pnl', width: 100,
      render: (v) => <span style={{ color: v >= 0 ? '#389e0d' : '#cf1322', fontWeight: 600 }}>${v.toFixed(2)}</span> },
    { title: '净值', dataIndex: 'equity_after', key: 'equity_after', width: 100, render: (v) => '$' + v.toFixed(2) },
  ];

  return (
    <Card title={name} style={{ marginTop: 16 }}>
      <Row gutter={24} style={{ marginBottom: 16 }}>
        <Col span={6}><Statistic title="最终现金" value={result.final_cash} prefix="$" precision={2} /></Col>
        <Col span={6}><Statistic title="总盈亏" value={result.total_pnl} prefix="$" precision={2}
          valueStyle={{ color: result.total_pnl >= 0 ? '#389e0d' : '#cf1322' }} /></Col>
        <Col span={6}><Statistic title="总交易数" value={result.total_trades} /></Col>
        <Col span={6}><Statistic title="收益率" value={(result.total_pnl / 10000 * 100).toFixed(2)} suffix="%" precision={2}
          valueStyle={{ color: result.total_pnl >= 0 ? '#389e0d' : '#cf1322' }} /></Col>
      </Row>

      <Collapse
        items={[
          {
            key: 'trades',
            label: `交易记录 (${result.trades.length} 笔)`,
            children: (
              <Table
                columns={tradeColumns}
                dataSource={result.trades.map((t, i) => ({ ...t, key: i }))}
                size="small" pagination={false} scroll={{ x: 1200 }}
              />
            ),
          },
          {
            key: 'log',
            label: `逐日事件日志 (${result.log.length} 天有事件)`,
            children: (
              <div>
                <Table
                  columns={logColumns}
                  dataSource={result.log.map((l, i) => ({ ...l, key: i }))}
                  size="small" pagination={false}
                  expandable={{
                    expandedRowRender: (record) => (
                      <div style={{ padding: '8px 0' }}>
                        {record.events.map((evt, idx) => (
                          <Descriptions key={idx} size="small" bordered column={4}
                            style={{ marginBottom: idx < record.events.length - 1 ? 8 : 0 }}>
                            <Descriptions.Item label="操作"><EventTag action={evt.action} /></Descriptions.Item>
                            <Descriptions.Item label="合约">{evt.instrument}</Descriptions.Item>
                            {evt.action === 'OPEN' ? (
                              <>
                                <Descriptions.Item label="到期日">{evt.expiry}</Descriptions.Item>
                                <Descriptions.Item label="行权价">${evt.strike?.toLocaleString()}</Descriptions.Item>
                                <Descriptions.Item label="数量">{evt.quantity}</Descriptions.Item>
                                <Descriptions.Item label="开仓价">${evt.open_price?.toFixed(2)}</Descriptions.Item>
                                <Descriptions.Item label="现金流">${evt.premium_cash_flow?.toFixed(2)}</Descriptions.Item>
                                <Descriptions.Item label="IV">{evt.iv ? (evt.iv * 100).toFixed(1) + '%' : '-'}</Descriptions.Item>
                                <Descriptions.Item label="数据源"><Tag>{evt.data_source}</Tag></Descriptions.Item>
                                <Descriptions.Item label="现货">${evt.spot?.toLocaleString()}</Descriptions.Item>
                                <Descriptions.Item label="操作后现金">${evt.equity_after?.toFixed(2)}</Descriptions.Item>
                              </>
                            ) : (
                              <>
                                <Descriptions.Item label="原因">{evt.reason}</Descriptions.Item>
                                <Descriptions.Item label="数量">{evt.quantity}</Descriptions.Item>
                                <Descriptions.Item label="开仓价">${evt.open_price?.toFixed(2)}</Descriptions.Item>
                                <Descriptions.Item label="平仓价">${evt.close_price?.toFixed(2)}</Descriptions.Item>
                                <Descriptions.Item label="现金流">${evt.close_cash_flow?.toFixed(2)}</Descriptions.Item>
                                <Descriptions.Item label="盈亏">
                                  <span style={{ color: evt.pnl >= 0 ? '#389e0d' : '#cf1322', fontWeight: 600 }}>
                                    ${evt.pnl?.toFixed(2)}
                                  </span>
                                </Descriptions.Item>
                                <Descriptions.Item label="操作后现金">${evt.equity_after?.toFixed(2)}</Descriptions.Item>
                              </>
                            )}
                          </Descriptions>
                        ))}
                      </div>
                    ),
                  }}
                />
              </div>
            ),
          },
        ]}
        defaultActiveKey={['trades']}
      />
    </Card>
  );
}

export default function BacktestVerify() {
  const [results, setResults] = useState({});
  const [loading, setLoading] = useState({});

  const runStrategy = async (idx) => {
    const strategy = STRATEGIES[idx];
    setLoading((prev) => ({ ...prev, [idx]: true }));
    setResults((prev) => ({ ...prev, [idx]: null }));
    try {
      const resp = await api.post('/api/deribit/verify-backtest', strategy.params);
      setResults((prev) => ({ ...prev, [idx]: resp.data }));
    } catch (err) {
      const detail = err.response?.data?.detail;
      setResults((prev) => ({ ...prev, [idx]: { error: detail || err.message } }));
    } finally {
      setLoading((prev) => ({ ...prev, [idx]: false }));
    }
  };

  const runAll = async () => {
    for (let i = 0; i < STRATEGIES.length; i++) {
      await runStrategy(i);
    }
  };

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <AntHeader style={{ background: '#001529', padding: '0 24px', display: 'flex', alignItems: 'center' }}>
        <Link to="/real-backtest" style={{ display: 'flex', alignItems: 'center', marginRight: 16 }}>
          <ArrowLeftOutlined style={{ fontSize: 18, color: '#1890ff' }} />
        </Link>
        <ExperimentOutlined style={{ fontSize: 24, color: '#faad14', marginRight: 12 }} />
        <Title level={3} style={{ color: 'white', margin: 0, fontSize: 20 }}>回测逻辑验证</Title>
      </AntHeader>

      <Content style={{ padding: 24, background: '#f0f2f5' }}>
        <div style={{ maxWidth: 1400, margin: '0 auto' }}>
          <Alert
            message="回测逻辑验证"
            description="使用两个简单的单腿卖方策略验证回测引擎的开平仓逻辑、资金计算和数据收集。每笔交易的现金流和盈亏都有详细记录，可展开查看逐日事件。"
            type="warning" showIcon style={{ marginBottom: 16 }}
          />

          <Card>
            <Space direction="vertical" style={{ width: '100%' }}>
              {STRATEGIES.map((s, idx) => (
                <Row key={idx} gutter={16} align="middle" style={{ padding: '8px 0', borderBottom: '1px solid #f0f0f0' }}>
                  <Col flex="auto">
                    <Text strong>{s.name}</Text>
                    <br />
                    <Text type="secondary" style={{ fontSize: 12 }}>{s.description}</Text>
                    <br />
                    <Text type="secondary" style={{ fontSize: 11 }}>
                      时间段: {s.params.start_date} ~ {s.params.end_date} | 初始资金: ${s.params.initial_capital}
                    </Text>
                  </Col>
                  <Col>
                    <Button
                      type="primary" size="small"
                      loading={loading[idx]}
                      onClick={() => runStrategy(idx)}
                      icon={<ExperimentOutlined />}
                    >
                      运行
                    </Button>
                  </Col>
                </Row>
              ))}
              <Button
                type="primary" size="large" block
                style={{ marginTop: 8, background: '#faad14', borderColor: '#faad14' }}
                onClick={runAll}
                loading={Object.values(loading).some(Boolean)}
                icon={<CheckCircleOutlined />}
              >
                运行全部验证
              </Button>
            </Space>
          </Card>

          {STRATEGIES.map((s, idx) => (
            results[idx] && !results[idx].error ? (
              <StrategyResult key={idx} name={s.name} result={results[idx]} loading={loading[idx]} />
            ) : results[idx]?.error ? (
              <Alert key={idx} type="error" message={`${s.name} 失败`} description={results[idx].error} style={{ marginTop: 16 }} />
            ) : null
          ))}
        </div>
      </Content>

      <Footer style={{ textAlign: 'center', background: '#001529', color: 'white' }}>
        期权风险监控系统 ©2024 | 回测逻辑验证
      </Footer>
    </Layout>
  );
}
