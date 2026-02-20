/**
 * Header Component
 * Application header with title and navigation
 */
import { Layout, Typography, Space } from 'antd';
import { LineChartOutlined, CalculatorOutlined, ExperimentOutlined, DatabaseOutlined, BugOutlined, CheckCircleOutlined, RocketOutlined, CrownOutlined, SwapOutlined, DollarOutlined, CloudServerOutlined } from '@ant-design/icons';
import { Link } from 'react-router-dom';

const { Header: AntHeader } = Layout;
const { Title } = Typography;

export default function Header() {
  return (
    <AntHeader
      style={{
        background: '#001529',
        padding: '0 24px',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center' }}>
        <LineChartOutlined style={{ fontSize: 24, color: '#1890ff', marginRight: 12 }} />
        <Title level={3} style={{ color: 'white', margin: 0, fontSize: 20 }}>
          期权风险监控系统
        </Title>
      </div>
      <Space size="large">
        <Link to="/iv-calculator" style={{ color: '#ffffffb3', fontSize: 14, display: 'flex', alignItems: 'center', gap: 6 }}>
          <CalculatorOutlined />
          隐含波动率计算器
        </Link>
        <Link to="/backtest" style={{ color: '#ffffffb3', fontSize: 14, display: 'flex', alignItems: 'center', gap: 6 }}>
          <ExperimentOutlined />
          策略回测
        </Link>
        <Link to="/real-backtest" style={{ color: '#ffffffb3', fontSize: 14, display: 'flex', alignItems: 'center', gap: 6 }}>
          <DatabaseOutlined />
          真实数据回测
        </Link>
        <Link to="/deribit-debug" style={{ color: '#ffffffb3', fontSize: 14, display: 'flex', alignItems: 'center', gap: 6 }}>
          <BugOutlined />
          数据调试
        </Link>
        <Link to="/backtest-verify" style={{ color: '#ffffffb3', fontSize: 14, display: 'flex', alignItems: 'center', gap: 6 }}>
          <CheckCircleOutlined />
          回测验证
        </Link>
        <Link to="/leaps" style={{ color: '#52c41a', fontSize: 14, display: 'flex', alignItems: 'center', gap: 6 }}>
          <RocketOutlined />
          LEAPS策略
        </Link>
        <Link to="/leaps-ultimate" style={{ color: '#faad14', fontSize: 14, display: 'flex', alignItems: 'center', gap: 6 }}>
          <CrownOutlined />
          LEAPS终极
        </Link>
        <Link to="/leaps-ultimate-v2" style={{ color: '#722ed1', fontSize: 14, display: 'flex', alignItems: 'center', gap: 6 }}>
          <SwapOutlined />
          终极2.0
        </Link>
        <Link to="/us-leaps" style={{ color: '#52c41a', fontSize: 14, display: 'flex', alignItems: 'center', gap: 6, fontWeight: 600 }}>
          <DollarOutlined />
          美股LEAPS
        </Link>
        <Link to="/data-center" style={{ color: '#13c2c2', fontSize: 14, display: 'flex', alignItems: 'center', gap: 6, fontWeight: 600 }}>
          <CloudServerOutlined />
          数据中心
        </Link>
      </Space>
    </AntHeader>
  );
}
