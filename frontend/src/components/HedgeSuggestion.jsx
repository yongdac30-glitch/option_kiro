/**
 * Hedge Suggestion Component
 * Generate hedge position with user-defined parameters
 * Priority: cost budget > target max loss
 */
import { useState } from 'react';
import { Card, Button, Descriptions, Tag, Statistic, Row, Col, Empty, InputNumber, DatePicker, Space, Typography, message } from 'antd';
import { ThunderboltOutlined, ArrowDownOutlined, DollarOutlined } from '@ant-design/icons';
import dayjs from 'dayjs';
import { hedgeService } from '../services/hedgeService';
import { useAppContext } from '../context/AppContext';

const { Text } = Typography;

export default function HedgeSuggestion() {
  const { state } = useAppContext();
  const [loading, setLoading] = useState(false);
  const [suggestion, setSuggestion] = useState(null);
  const [targetMaxLoss, setTargetMaxLoss] = useState(null);
  const [maxHedgeCost, setMaxHedgeCost] = useState(null);
  const [hedgeExpDate, setHedgeExpDate] = useState(null);
  const [hedgeIV, setHedgeIV] = useState(null);

  if (!state.pnlData) return null;

  const currentMaxLoss = state.pnlData.max_loss?.amount || 0;

  const handleGenerate = async () => {
    setLoading(true);
    setSuggestion(null);
    try {
      const params = {
        underlying_symbol: state.pnlData.underlying_symbol,
        current_price: state.pnlData.current_price,
        implied_volatility:
          (state.volatilityScenarios[state.pnlData.underlying_symbol] || 25) / 100,
        portfolio_id: state.activePortfolioId,
      };

      if (targetMaxLoss != null && targetMaxLoss > 0) {
        params.target_max_loss = -targetMaxLoss;
      }
      if (maxHedgeCost != null && maxHedgeCost > 0) {
        params.max_hedge_cost = maxHedgeCost;
      }
      if (hedgeExpDate) {
        params.hedge_expiration_date = hedgeExpDate.format('YYYY-MM-DD');
      }
      if (hedgeIV != null && hedgeIV > 0) {
        params.hedge_iv = hedgeIV / 100; // convert % to decimal
      }

      const result = await hedgeService.suggest(params);
      setSuggestion(result);
    } catch (error) {
      const detail = error.response?.data?.detail;
      message.error(detail || '生成对冲建议失败');
      console.error(error);
    } finally {
      setLoading(false);
    }
  };

  return (
    <Card title="对冲建议" style={{ marginTop: 16 }}>
      <Row gutter={[16, 12]} align="middle" style={{ marginBottom: 16 }}>
        <Col>
          <Space>
            <Text>对冲预算:</Text>
            <InputNumber
              style={{ width: 140 }}
              placeholder="不限"
              value={maxHedgeCost}
              onChange={setMaxHedgeCost}
              min={0}
              precision={2}
              prefix="$"
            />
          </Space>
        </Col>
        <Col>
          <Space>
            <Text>目标最大亏损:</Text>
            <InputNumber
              style={{ width: 140 }}
              placeholder={Math.abs(currentMaxLoss).toFixed(0)}
              value={targetMaxLoss}
              onChange={setTargetMaxLoss}
              min={0}
              precision={0}
              prefix="$"
            />
          </Space>
        </Col>
        <Col>
          <Space>
            <Text>到期日:</Text>
            <DatePicker
              style={{ width: 140 }}
              value={hedgeExpDate}
              onChange={setHedgeExpDate}
              format="YYYY-MM-DD"
              placeholder="默认最近到期"
              disabledDate={(current) => current && current < dayjs().startOf('day')}
            />
          </Space>
        </Col>
        <Col>
          <Space>
            <Text>预估IV:</Text>
            <InputNumber
              style={{ width: 110 }}
              placeholder="同组合"
              value={hedgeIV}
              onChange={setHedgeIV}
              min={0.1}
              max={500}
              precision={1}
              suffix="%"
            />
          </Space>
        </Col>
        <Col>
          <Button
            type="primary"
            icon={<ThunderboltOutlined />}
            loading={loading}
            onClick={handleGenerate}
          >
            一键生成
          </Button>
        </Col>
      </Row>
      <Row style={{ marginBottom: 16 }}>
        <Col>
          <Text type="secondary">预算优先：当预算和目标亏损无法同时满足时，优先保证不超出预算</Text>
        </Col>
      </Row>

      {!suggestion ? (
        <Empty
          description="设置参数后点击生成最优对冲方案"
          image={Empty.PRESENTED_IMAGE_SIMPLE}
        />
      ) : (
        <div>
          <Row gutter={16} style={{ marginBottom: 16 }}>
            <Col xs={24} sm={6}>
              <Statistic
                title="原始最大亏损"
                value={Math.abs(suggestion.original_max_loss)}
                precision={2}
                prefix={<ArrowDownOutlined />}
                suffix="USD"
                valueStyle={{ color: '#cf1322' }}
              />
            </Col>
            <Col xs={24} sm={6}>
              <Statistic
                title="对冲后最大亏损"
                value={Math.abs(suggestion.hedged_max_loss)}
                precision={2}
                prefix={<ArrowDownOutlined />}
                suffix="USD"
                valueStyle={{ color: '#faad14' }}
              />
            </Col>
            <Col xs={24} sm={6}>
              <Statistic
                title="亏损减少"
                value={suggestion.reduction}
                precision={2}
                suffix="USD"
                valueStyle={{ color: '#52c41a' }}
              />
            </Col>
            <Col xs={24} sm={6}>
              <Statistic
                title="对冲总成本"
                value={suggestion.total_premium_cost}
                precision={4}
                prefix={<DollarOutlined />}
                suffix="USD"
                valueStyle={{ color: '#1890ff' }}
              />
            </Col>
          </Row>

          <Descriptions bordered column={{ xs: 1, sm: 2, md: 3 }} size="small">
            <Descriptions.Item label="方向">
              <Tag color="blue">买入 {suggestion.option_type}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label="数量">{suggestion.quantity}</Descriptions.Item>
            <Descriptions.Item label="行权价">${suggestion.strike_price.toLocaleString()}</Descriptions.Item>
            <Descriptions.Item label="到期日">{suggestion.expiration_date}</Descriptions.Item>
            <Descriptions.Item label="单位权利金">${suggestion.estimated_premium.toFixed(8)}</Descriptions.Item>
            <Descriptions.Item label="总权利金成本">${suggestion.total_premium_cost.toFixed(4)}</Descriptions.Item>
          </Descriptions>
        </div>
      )}
    </Card>
  );
}
