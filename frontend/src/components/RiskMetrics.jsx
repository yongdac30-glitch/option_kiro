/**
 * Risk Metrics Component
 * Display key risk indicators
 */
import React from 'react';
import { Card, Statistic, Row, Col, Alert } from 'antd';
import { ArrowUpOutlined, ArrowDownOutlined, DollarOutlined } from '@ant-design/icons';

export default function RiskMetrics({ pnlData }) {
  if (!pnlData || !pnlData.price_points || pnlData.price_points.length === 0) {
    return null;
  }

  // Find current P&L (closest to current price)
  const currentPricePoint = pnlData.price_points.reduce((closest, point) => {
    const diff = Math.abs(point.price - pnlData.current_price);
    const closestDiff = Math.abs(closest.price - pnlData.current_price);
    return diff < closestDiff ? point : closest;
  }, pnlData.price_points[0]);

  const currentPnL = currentPricePoint ? currentPricePoint.total_pnl : 0;
  const maxLoss = pnlData.max_loss?.amount || 0;
  const maxProfit = pnlData.max_profit?.amount || 0;
  const maxLossPrice = pnlData.max_loss?.at_price || 0;
  const maxProfitPrice = pnlData.max_profit?.at_price || 0;

  // Risk warning threshold (e.g., if max loss > $10,000)
  const showWarning = maxLoss < -10000;

  return (
    <div>
      {showWarning && (
        <Alert
          message="风险警告"
          description={`最大潜在亏损超过 $${Math.abs(maxLoss).toLocaleString()}，请注意风险控制！`}
          type="warning"
          showIcon
          style={{ marginBottom: 16 }}
        />
      )}

      <Row gutter={16}>
        <Col xs={24} sm={8}>
          <Card>
            <Statistic
              title="当前盈亏"
              value={currentPnL}
              precision={2}
              valueStyle={{ color: currentPnL >= 0 ? '#3f8600' : '#cf1322' }}
              prefix={currentPnL >= 0 ? <ArrowUpOutlined /> : <ArrowDownOutlined />}
              suffix="USD"
            />
            <div style={{ marginTop: 8, fontSize: 12, color: '#999' }}>
              当前价格: ${pnlData.current_price.toFixed(2)}
            </div>
          </Card>
        </Col>

        <Col xs={24} sm={8}>
          <Card>
            <Statistic
              title="最大潜在亏损"
              value={Math.abs(maxLoss)}
              precision={2}
              valueStyle={{ color: '#cf1322' }}
              prefix={<ArrowDownOutlined />}
              suffix="USD"
            />
            <div style={{ marginTop: 8, fontSize: 12, color: '#999' }}>
              触发价格: ${maxLossPrice.toFixed(2)}
            </div>
          </Card>
        </Col>

        <Col xs={24} sm={8}>
          <Card>
            <Statistic
              title="最大潜在盈利"
              value={maxProfit}
              precision={2}
              valueStyle={{ color: '#3f8600' }}
              prefix={<ArrowUpOutlined />}
              suffix="USD"
            />
            <div style={{ marginTop: 8, fontSize: 12, color: '#999' }}>
              触发价格: ${maxProfitPrice.toFixed(2)}
            </div>
          </Card>
        </Col>
      </Row>
    </div>
  );
}
