/**
 * Header Component
 * Application header with title and navigation (grouped dropdown menus)
 */
import { Layout, Typography, Space, Dropdown } from 'antd';
import {
  LineChartOutlined, CalculatorOutlined, ExperimentOutlined,
  DatabaseOutlined, BugOutlined, CheckCircleOutlined,
  RocketOutlined, CrownOutlined, SwapOutlined, DollarOutlined,
  CloudServerOutlined, FundOutlined, DownOutlined,
  BarChartOutlined, ToolOutlined,
} from '@ant-design/icons';
import { Link } from 'react-router-dom';

const { Header: AntHeader } = Layout;
const { Title } = Typography;

const linkStyle = { color: '#ffffffd9', fontSize: 13, display: 'flex', alignItems: 'center', gap: 6, padding: '4px 0' };

const backtestItems = {
  items: [
    { key: 'bt', label: <Link to="/backtest" style={linkStyle}><ExperimentOutlined /> 策略回测</Link> },
    { key: 'rbt', label: <Link to="/real-backtest" style={linkStyle}><DatabaseOutlined /> 真实数据回测</Link> },
    { key: 'btv', label: <Link to="/backtest-verify" style={linkStyle}><CheckCircleOutlined /> 回测验证</Link> },
  ],
};

const leapsItems = {
  items: [
    { key: 'leaps', label: <Link to="/leaps" style={{ ...linkStyle, color: '#52c41a' }}><RocketOutlined /> LEAPS策略</Link> },
    { key: 'lu', label: <Link to="/leaps-ultimate" style={{ ...linkStyle, color: '#faad14' }}><CrownOutlined /> LEAPS终极</Link> },
    { key: 'lu2', label: <Link to="/leaps-ultimate-v2" style={{ ...linkStyle, color: '#722ed1' }}><SwapOutlined /> 终极2.0</Link> },
    { key: 'us', label: <Link to="/us-leaps" style={{ ...linkStyle, color: '#52c41a' }}><DollarOutlined /> 美股LEAPS</Link> },
    { key: 'qqq', label: <Link to="/qqq-leaps" style={{ ...linkStyle, color: '#eb2f96' }}><FundOutlined /> QQQ策略</Link> },
  ],
};

const toolItems = {
  items: [
    { key: 'iv', label: <Link to="/iv-calculator" style={linkStyle}><CalculatorOutlined /> IV计算器</Link> },
    { key: 'debug', label: <Link to="/deribit-debug" style={linkStyle}><BugOutlined /> 数据调试</Link> },
    { key: 'dc', label: <Link to="/data-center" style={{ ...linkStyle, color: '#13c2c2' }}><CloudServerOutlined /> 数据中心</Link> },
  ],
};

export default function Header() {
  return (
    <AntHeader
      style={{
        background: '#001529',
        padding: '0 24px',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        height: 48,
        lineHeight: '48px',
      }}
    >
      <Link to="/" style={{ display: 'flex', alignItems: 'center', textDecoration: 'none' }}>
        <LineChartOutlined style={{ fontSize: 20, color: '#1890ff', marginRight: 10 }} />
        <Title level={4} style={{ color: 'white', margin: 0, fontSize: 16 }}>
          期权风险监控
        </Title>
      </Link>
      <Space size="middle">
        <Dropdown menu={backtestItems} placement="bottomRight">
          <span style={{ color: '#ffffffd9', fontSize: 13, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 4 }}>
            <BarChartOutlined /> 回测 <DownOutlined style={{ fontSize: 10 }} />
          </span>
        </Dropdown>
        <Dropdown menu={leapsItems} placement="bottomRight">
          <span style={{ color: '#52c41a', fontSize: 13, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 4 }}>
            <RocketOutlined /> LEAPS <DownOutlined style={{ fontSize: 10 }} />
          </span>
        </Dropdown>
        <Dropdown menu={toolItems} placement="bottomRight">
          <span style={{ color: '#13c2c2', fontSize: 13, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 4 }}>
            <ToolOutlined /> 工具 <DownOutlined style={{ fontSize: 10 }} />
          </span>
        </Dropdown>
      </Space>
    </AntHeader>
  );
}
