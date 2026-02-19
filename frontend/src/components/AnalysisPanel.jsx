/**
 * Analysis Panel Component
 * Container for P&L chart, risk metrics, and hedge suggestion
 */
import { Card, Empty } from 'antd';
import { LineChartOutlined } from '@ant-design/icons';
import PnLChart from './PnLChart';
import RiskMetrics from './RiskMetrics';
import HedgeSuggestion from './HedgeSuggestion';
import { useAppContext } from '../context/AppContext';

export default function AnalysisPanel() {
  const { state } = useAppContext();

  if (!state.pnlData) {
    return (
      <Card title="盈亏分析">
        <Empty description="暂无分析数据" image={Empty.PRESENTED_IMAGE_SIMPLE}>
          <p style={{ color: '#999' }}>
            请先添加持仓，然后在控制面板中输入价格和波动率进行计算
          </p>
        </Empty>
      </Card>
    );
  }

  return (
    <div>
      <Card title="风险指标" style={{ marginBottom: 16 }}>
        <RiskMetrics pnlData={state.pnlData} />
      </Card>

      <Card
        title="盈亏分析"
        tabList={[
          {
            key: 'chart',
            tab: (
              <span>
                <LineChartOutlined />
                图表视图
              </span>
            ),
          },
        ]}
      >
        <PnLChart pnlData={state.pnlData} />
        <HedgeSuggestion />
      </Card>
    </div>
  );
}
