/**
 * Implied Volatility Calculator Page
 * T-Quote (T型报价) option chain display + IV calculator
 * Supports BTC and ETH underlying switching
 */
import { useState, useEffect, useMemo } from 'react';
import {
  Layout, Card, Form, Select, InputNumber, DatePicker, Button,
  Row, Col, Statistic, message, Typography, Tag, Space, Spin, Divider, Segmented,
} from 'antd';
import { CalculatorOutlined, ArrowLeftOutlined, CloudDownloadOutlined, SearchOutlined } from '@ant-design/icons';
import { Link } from 'react-router-dom';
import dayjs from 'dayjs';
import { ivService } from '../services/ivService';
import { okxService } from '../services/okxService';

const { Header: AntHeader, Content, Footer } = Layout;
const { Title } = Typography;
const { Option } = Select;

const ULY_OPTIONS = [
  { label: 'BTC 期权', value: 'BTC-USD' },
  { label: 'ETH 期权', value: 'ETH-USD' },
];

const fmtPrice = (v) => (v > 0 ? '$' + v.toFixed(2) : '-');

export default function IVCalculator() {
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);

  const [underlying, setUnderlying] = useState('BTC-USD');
  const [okxLoading, setOkxLoading] = useState(false);
  const [expiries, setExpiries] = useState([]);
  const [selectedExpiry, setSelectedExpiry] = useState(null);
  const [optionChain, setOptionChain] = useState(null);
  const [spotPrice, setSpotPrice] = useState(null);
  const [strikeFilter, setStrikeFilter] = useState(null);

  // Reload expiries when underlying changes
  useEffect(() => {
    setSelectedExpiry(null);
    setOptionChain(null);
    setSpotPrice(null);
    setStrikeFilter(null);
    loadExpiries(underlying);
  }, [underlying]);

  const loadExpiries = async (uly) => {
    try {
      const data = await okxService.getExpiryDates(uly);
      setExpiries(data.expiries || []);
    } catch (err) {
      console.error('Failed to load expiries:', err);
    }
  };

  const loadOptionChain = async (expiryCode) => {
    setOkxLoading(true);
    try {
      const data = await okxService.getOptionChain(underlying, expiryCode);
      setOptionChain(data.data || []);
      setSpotPrice(data.btcUsdPrice);
    } catch (err) {
      message.error('获取OKX期权行情失败');
      console.error(err);
    } finally {
      setOkxLoading(false);
    }
  };

  const handleExpiryChange = (code) => {
    setSelectedExpiry(code);
    if (code) loadOptionChain(code);
    else setOptionChain(null);
  };

  const handleUlyChange = (val) => {
    setUnderlying(val);
  };

  const ulyLabel = underlying.split('-')[0]; // BTC or ETH

  const handleFillFromOkx = (record, optType) => {
    let optionPrice = record.lastUsd;
    if (record.bidUsd > 0 && record.askUsd > 0) {
      optionPrice = (record.bidUsd + record.askUsd) / 2;
    }
    form.setFieldsValue({
      option_type: optType,
      option_price: parseFloat(optionPrice.toFixed(4)),
      underlying_price: spotPrice ? parseFloat(spotPrice.toFixed(2)) : undefined,
      strike_price: record.strike,
      expiration_date: record.expiryDate ? dayjs(record.expiryDate) : undefined,
      current_date: dayjs(),
    });
    message.success('已填入 ' + optType + ' ' + record.strike + ' 参数');
  };

  // Build T-quote rows: group by strike, merge CALL + PUT
  const tQuoteRows = useMemo(() => {
    if (!optionChain) return [];
    const byStrike = {};
    for (const item of optionChain) {
      if (!byStrike[item.strike]) {
        byStrike[item.strike] = { strike: item.strike, expiryDate: item.expiryDate };
      }
      if (item.optType === 'CALL') byStrike[item.strike].call = item;
      else byStrike[item.strike].put = item;
    }
    let rows = Object.values(byStrike).sort((a, b) => a.strike - b.strike);
    if (strikeFilter != null) {
      const lo = strikeFilter * 0.8;
      const hi = strikeFilter * 1.2;
      rows = rows.filter((r) => r.strike >= lo && r.strike <= hi);
    }
    return rows;
  }, [optionChain, strikeFilter]);

  const handleCalculate = async (values) => {
    setLoading(true);
    setResult(null);
    try {
      const data = await ivService.calculate({
        option_type: values.option_type,
        option_price: values.option_price,
        underlying_price: values.underlying_price,
        strike_price: values.strike_price,
        expiration_date: values.expiration_date.format('YYYY-MM-DD'),
        current_date: values.current_date.format('YYYY-MM-DD'),
      });
      setResult(data);
    } catch (error) {
      message.error(error.response?.data?.detail || '计算失败');
    } finally {
      setLoading(false);
    }
  };

  const atmStrike = useMemo(() => {
    if (!spotPrice || !tQuoteRows.length) return null;
    let closest = tQuoteRows[0];
    for (const r of tQuoteRows) {
      if (Math.abs(r.strike - spotPrice) < Math.abs(closest.strike - spotPrice)) closest = r;
    }
    return closest.strike;
  }, [spotPrice, tQuoteRows]);

  // Styles
  const cellStyle = { padding: '4px 8px', fontSize: 12, whiteSpace: 'nowrap', textAlign: 'right' };
  const headerStyle = { ...cellStyle, fontWeight: 600, background: '#fafafa', textAlign: 'center', borderBottom: '2px solid #e8e8e8' };
  const strikeColStyle = { ...cellStyle, textAlign: 'center', fontWeight: 700, fontSize: 13, background: '#f0f5ff', borderLeft: '2px solid #d9d9d9' };
  const ivStyle = { ...cellStyle, textAlign: 'center', fontWeight: 600, color: '#722ed1' };
  const btnStyle = { padding: '0 4px', fontSize: 11, height: 20, lineHeight: '20px' };

  const renderTQuoteTable = () => (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', border: '1px solid #e8e8e8' }}>
        <thead>
          <tr>
            <th colSpan={6} style={{ ...headerStyle, background: '#e6f7e6', color: '#389e0d', borderRight: '2px solid #d9d9d9' }}>CALL（看涨）</th>
            <th colSpan={2} style={{ ...headerStyle, background: '#f0f5ff' }}>行权价</th>
            <th colSpan={6} style={{ ...headerStyle, background: '#fff1f0', color: '#cf1322', borderLeft: '2px solid #d9d9d9' }}>PUT（看跌）</th>
          </tr>
          <tr>
            <th style={headerStyle}>填入</th>
            <th style={headerStyle}>买一</th>
            <th style={headerStyle}>卖一</th>
            <th style={headerStyle}>最新价</th>
            <th style={headerStyle}>24h量</th>
            <th style={{ ...headerStyle, background: '#f3e8ff', color: '#722ed1', borderRight: '2px solid #d9d9d9' }}>IV</th>
            <th style={{ ...headerStyle, background: '#f0f5ff' }}></th>
            <th style={{ ...headerStyle, background: '#f0f5ff' }}>距离%</th>
            <th style={{ ...headerStyle, background: '#f3e8ff', color: '#722ed1', borderLeft: '2px solid #d9d9d9' }}>IV</th>
            <th style={headerStyle}>24h量</th>
            <th style={headerStyle}>最新价</th>
            <th style={headerStyle}>买一</th>
            <th style={headerStyle}>卖一</th>
            <th style={headerStyle}>填入</th>
          </tr>
        </thead>
        <tbody>
          {tQuoteRows.map((row) => {
            const c = row.call || {};
            const p = row.put || {};
            const isAtm = row.strike === atmStrike;
            const rowBg = isAtm ? '#fffbe6' : 'white';
            const callItm = spotPrice != null && row.strike < spotPrice;
            const putItm = spotPrice != null && row.strike > spotPrice;
            const callBg = callItm ? '#f6ffed' : rowBg;
            const putBg = putItm ? '#fff2f0' : rowBg;
            return (
              <tr key={row.strike} style={{ borderBottom: '1px solid #f0f0f0' }}>
                <td style={{ ...cellStyle, textAlign: 'center', background: callBg }}>
                  {(c.lastUsd > 0 || c.bidUsd > 0) && (
                    <Button type="link" size="small" style={btnStyle} onClick={() => handleFillFromOkx(c, 'CALL')}>选择</Button>
                  )}
                </td>
                <td style={{ ...cellStyle, background: callBg, color: '#389e0d' }}>{fmtPrice(c.bidUsd || 0)}</td>
                <td style={{ ...cellStyle, background: callBg, color: '#cf1322' }}>{fmtPrice(c.askUsd || 0)}</td>
                <td style={{ ...cellStyle, background: callBg }}>{fmtPrice(c.lastUsd || 0)}</td>
                <td style={{ ...cellStyle, background: callBg }}>{c.vol24h || '-'}</td>
                <td style={{ ...ivStyle, background: callBg, borderRight: '2px solid #d9d9d9' }}>{c.iv != null ? c.iv + '%' : '-'}</td>
                <td style={{ ...strikeColStyle, background: isAtm ? '#e6f7ff' : '#f0f5ff' }}>
                  {row.strike.toLocaleString()}
                  {isAtm && <Tag color="blue" style={{ marginLeft: 4, fontSize: 10, padding: '0 3px', lineHeight: '16px' }}>ATM</Tag>}
                </td>
                <td style={{ ...cellStyle, textAlign: 'center', background: isAtm ? '#e6f7ff' : '#f0f5ff', fontSize: 11, color: '#666', borderRight: '2px solid #d9d9d9' }}>
                  {spotPrice ? ((row.strike - spotPrice) / spotPrice * 100).toFixed(1) + '%' : '-'}
                </td>
                <td style={{ ...ivStyle, background: putBg, borderLeft: '2px solid #d9d9d9' }}>{p.iv != null ? p.iv + '%' : '-'}</td>
                <td style={{ ...cellStyle, background: putBg }}>{p.vol24h || '-'}</td>
                <td style={{ ...cellStyle, background: putBg }}>{fmtPrice(p.lastUsd || 0)}</td>
                <td style={{ ...cellStyle, background: putBg, color: '#389e0d' }}>{fmtPrice(p.bidUsd || 0)}</td>
                <td style={{ ...cellStyle, background: putBg, color: '#cf1322' }}>{fmtPrice(p.askUsd || 0)}</td>
                <td style={{ ...cellStyle, textAlign: 'center', background: putBg }}>
                  {(p.lastUsd > 0 || p.bidUsd > 0) && (
                    <Button type="link" size="small" style={btnStyle} onClick={() => handleFillFromOkx(p, 'PUT')}>选择</Button>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <AntHeader style={{ background: '#001529', padding: '0 24px', display: 'flex', alignItems: 'center' }}>
        <Link to="/" style={{ display: 'flex', alignItems: 'center', marginRight: 16 }}>
          <ArrowLeftOutlined style={{ fontSize: 18, color: '#1890ff' }} />
        </Link>
        <CalculatorOutlined style={{ fontSize: 24, color: '#1890ff', marginRight: 12 }} />
        <Title level={3} style={{ color: 'white', margin: 0, fontSize: 20 }}>隐含波动率计算器</Title>
      </AntHeader>

      <Content style={{ padding: 24, background: '#f0f2f5' }}>
        <div style={{ maxWidth: 1400, margin: '0 auto' }}>
          {/* OKX T-Quote */}
          <Card
            title={
              <Space>
                <CloudDownloadOutlined />
                <span>OKX 期权 T型报价</span>
                {spotPrice && <Tag color="blue">{ulyLabel}/USD: {'$' + spotPrice.toLocaleString()}</Tag>}
              </Space>
            }
            extra={
              <Segmented
                options={ULY_OPTIONS}
                value={underlying}
                onChange={handleUlyChange}
                size="small"
              />
            }
            style={{ marginBottom: 16 }}
          >
            <Row gutter={12} style={{ marginBottom: 12 }} align="middle">
              <Col>
                <span style={{ color: '#888', fontSize: 13 }}>到期日:</span>
              </Col>
              <Col>
                <Select
                  style={{ width: 200 }}
                  placeholder="选择到期日"
                  allowClear
                  onChange={handleExpiryChange}
                  value={selectedExpiry}
                  size="small"
                >
                  {expiries.map((e) => (
                    <Option key={e.code} value={e.code}>{e.date} ({e.code})</Option>
                  ))}
                </Select>
              </Col>
              <Col>
                <InputNumber placeholder="行权价附近" size="small" style={{ width: 130 }} value={strikeFilter} onChange={setStrikeFilter} min={0} />
              </Col>
              {selectedExpiry && (
                <Col>
                  <Button size="small" icon={<SearchOutlined />} onClick={() => loadOptionChain(selectedExpiry)} loading={okxLoading}>刷新</Button>
                </Col>
              )}
              <Col>
                <Space size={16} style={{ marginLeft: 16, fontSize: 12, color: '#999' }}>
                  <span>🟢 实值CALL</span>
                  <span>🔴 实值PUT</span>
                  <span>🟡 ATM</span>
                </Space>
              </Col>
            </Row>

            {okxLoading ? (
              <div style={{ textAlign: 'center', padding: 40 }}><Spin tip="加载中..." /></div>
            ) : optionChain ? (
              renderTQuoteTable()
            ) : (
              <div style={{ textAlign: 'center', color: '#999', padding: 20 }}>请选择到期日以加载期权行情</div>
            )}
          </Card>

          <Divider />

          {/* IV Calculator Form */}
          <Card title="输入期权参数（手动输入或从上方T型报价选择）">
            <Form form={form} layout="vertical" onFinish={handleCalculate} initialValues={{ option_type: 'PUT', current_date: dayjs() }}>
              <Row gutter={16}>
                <Col xs={24} sm={12}>
                  <Form.Item name="option_type" label="期权类型" rules={[{ required: true }]}>
                    <Select>
                      <Option value="PUT">Put</Option>
                      <Option value="CALL">Call</Option>
                    </Select>
                  </Form.Item>
                </Col>
                <Col xs={24} sm={12}>
                  <Form.Item name="option_price" label="期权市场价格（权利金 USD）"
                    rules={[{ required: true, message: '请输入期权价格' }, { type: 'number', min: 0.0001, message: '价格必须大于0' }]}>
                    <InputNumber style={{ width: '100%' }} placeholder="例如: 2.50" precision={4} min={0.0001} />
                  </Form.Item>
                </Col>
              </Row>
              <Row gutter={16}>
                <Col xs={24} sm={12}>
                  <Form.Item name="underlying_price" label="标的当前价格"
                    rules={[{ required: true, message: '请输入标的价格' }, { type: 'number', min: 0.01, message: '价格必须大于0' }]}>
                    <InputNumber style={{ width: '100%' }} placeholder="例如: 100000" precision={2} min={0.01} />
                  </Form.Item>
                </Col>
                <Col xs={24} sm={12}>
                  <Form.Item name="strike_price" label="行权价"
                    rules={[{ required: true, message: '请输入行权价' }, { type: 'number', min: 0.01, message: '行权价必须大于0' }]}>
                    <InputNumber style={{ width: '100%' }} placeholder="例如: 95000" precision={2} min={0.01} />
                  </Form.Item>
                </Col>
              </Row>
              <Row gutter={16}>
                <Col xs={24} sm={12}>
                  <Form.Item name="current_date" label="当前日期" rules={[{ required: true, message: '请选择当前日期' }]}>
                    <DatePicker style={{ width: '100%' }} format="YYYY-MM-DD" />
                  </Form.Item>
                </Col>
                <Col xs={24} sm={12}>
                  <Form.Item name="expiration_date" label="到期日" rules={[{ required: true, message: '请选择到期日' }]}>
                    <DatePicker style={{ width: '100%' }} format="YYYY-MM-DD" />
                  </Form.Item>
                </Col>
              </Row>
              <Form.Item>
                <Button type="primary" htmlType="submit" loading={loading} icon={<CalculatorOutlined />} size="large" block>
                  计算隐含波动率
                </Button>
              </Form.Item>
            </Form>
          </Card>

          {result && (
            <Card title="计算结果" style={{ marginTop: 16 }}>
              <Row gutter={32} justify="center">
                <Col>
                  <Statistic title="隐含波动率" value={result.implied_volatility_pct} suffix="%" precision={2}
                    valueStyle={{ color: '#1890ff', fontSize: 36 }} />
                </Col>
                <Col>
                  <Statistic title="隐含波动率（小数）" value={result.implied_volatility} precision={6}
                    valueStyle={{ fontSize: 36 }} />
                </Col>
              </Row>
            </Card>
          )}
        </div>
      </Content>

      <Footer style={{ textAlign: 'center', background: '#001529', color: 'white' }}>
        期权风险监控系统 ©2024 | 隐含波动率计算器
      </Footer>
    </Layout>
  );
}
