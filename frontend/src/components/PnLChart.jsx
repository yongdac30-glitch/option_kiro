/**
 * P&L Chart Component
 * Visualize profit/loss curve across price range with range selector
 */
import { useState, useMemo } from 'react';
import { Row, Col, InputNumber, Space, Typography } from 'antd';
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ReferenceLine,
  ResponsiveContainer,
} from 'recharts';

const { Text } = Typography;

export default function PnLChart({ pnlData }) {
  const [priceMin, setPriceMin] = useState(null);
  const [priceMax, setPriceMax] = useState(null);

  // Compute full data range
  const fullRange = useMemo(() => {
    if (!pnlData?.price_points?.length) return { min: 0, max: 0 };
    const prices = pnlData.price_points.map(p => p.price);
    return { min: Math.min(...prices), max: Math.max(...prices) };
  }, [pnlData]);

  if (!pnlData || !pnlData.price_points || pnlData.price_points.length === 0) {
    return (
      <div style={{ textAlign: 'center', padding: '40px', color: '#999' }}>
        暂无数据，请先计算盈亏
      </div>
    );
  }

  const effectiveMin = priceMin ?? fullRange.min;
  const effectiveMax = priceMax ?? fullRange.max;

  const chartData = pnlData.price_points
    .filter(point => point.price >= effectiveMin && point.price <= effectiveMax)
    .map(point => ({
      price: parseFloat(point.price.toFixed(2)),
      pnl: parseFloat(point.total_pnl.toFixed(2)),
    }));

  const formatCurrency = (value) => `$${parseFloat(value).toLocaleString()}`;

  const CustomTooltip = ({ active, payload }) => {
    if (active && payload && payload.length) {
      const data = payload[0].payload;
      return (
        <div
          style={{
            backgroundColor: 'white',
            padding: '10px',
            border: '1px solid #ccc',
            borderRadius: '4px',
          }}
        >
          <p style={{ margin: 0, fontWeight: 'bold' }}>
            价格: ${data.price.toLocaleString()}
          </p>
          <p
            style={{
              margin: '5px 0 0 0',
              color: data.pnl >= 0 ? '#52c41a' : '#ff4d4f',
            }}
          >
            盈亏: {formatCurrency(data.pnl)}
          </p>
        </div>
      );
    }
    return null;
  };

  // Only show reference lines if they fall within the visible range
  const showCurrentPrice =
    pnlData.current_price >= effectiveMin && pnlData.current_price <= effectiveMax;
  const showMaxLoss =
    pnlData.max_loss &&
    pnlData.max_loss.at_price >= effectiveMin &&
    pnlData.max_loss.at_price <= effectiveMax;

  return (
    <div>
      <Row gutter={16} style={{ marginBottom: 16 }} align="middle">
        <Col>
          <Space>
            <Text>价格范围:</Text>
            <InputNumber
              style={{ width: 140 }}
              placeholder={`最低 ${fullRange.min.toFixed(0)}`}
              value={priceMin}
              onChange={setPriceMin}
              min={0}
              step={100}
              prefix="$"
            />
            <Text>—</Text>
            <InputNumber
              style={{ width: 140 }}
              placeholder={`最高 ${fullRange.max.toFixed(0)}`}
              value={priceMax}
              onChange={setPriceMax}
              min={0}
              step={100}
              prefix="$"
            />
            <a
              onClick={() => { setPriceMin(null); setPriceMax(null); }}
              style={{ marginLeft: 8 }}
            >
              重置
            </a>
          </Space>
        </Col>
      </Row>

      <ResponsiveContainer width="100%" height={400}>
        <LineChart data={chartData} margin={{ top: 5, right: 30, left: 20, bottom: 5 }}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis
            dataKey="price"
            label={{ value: '标的价格 ($)', position: 'insideBottom', offset: -5 }}
            tick={{ fontSize: 12 }}
            tickFormatter={(v) => `$${Number(v).toLocaleString()}`}
          />
          <YAxis
            label={{ value: '盈亏 ($)', angle: -90, position: 'insideLeft' }}
            tick={{ fontSize: 12 }}
            tickFormatter={formatCurrency}
          />
          <Tooltip content={<CustomTooltip />} />
          <Legend />

          {/* Zero line */}
          <ReferenceLine y={0} stroke="#666" strokeDasharray="3 3" />

          {/* Current price line */}
          {showCurrentPrice && (
            <ReferenceLine
              x={parseFloat(pnlData.current_price.toFixed(2))}
              stroke="#1890ff"
              strokeWidth={2}
              label={{ value: '当前价格', position: 'top', fill: '#1890ff' }}
            />
          )}

          {/* Max loss point */}
          {showMaxLoss && (
            <ReferenceLine
              x={parseFloat(pnlData.max_loss.at_price.toFixed(2))}
              stroke="#ff4d4f"
              strokeWidth={2}
              strokeDasharray="5 5"
              label={{ value: '最大亏损', position: 'top', fill: '#ff4d4f' }}
            />
          )}

          <Line
            type="monotone"
            dataKey="pnl"
            stroke="#8884d8"
            strokeWidth={2}
            dot={false}
            name="组合盈亏"
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
