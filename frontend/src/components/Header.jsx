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

/* 下拉菜单是白色背景，文字用深色 */
const menuLink = (to, icon, text, color) => (
  <Link to={to} style={{ color: color || 'rgba(0,0,0,0.88)', fontSize: 13, display: 'flex', alignItems: 'center', gap: 6 }}>
    {icon} {text}
  </Link>
);

const backtestItems = {
  items: [
    { key: 'bt', label: menuLink('/backtest', <ExperimentOutlined />, '策略回测') },
    { key: 'rbt', label: menuLink('/real-backtest', <DatabaseOutlined />, '真实数据回测') },
    { key: 'btv', label: menuLink('/backtest-verify', <CheckCircleOutlined />, '回测验证') },
  ],
};

const leapsItems = {
  items: [
    { key: 'leaps', label: menuLink('/leaps', <RocketOutlined />, 'LEAPS策略', '#389e0d') },
    { key: 'lu', label: menuLink('/leaps-ultimate', <CrownOutlined />, 'LEAPS终极', '#d48806') },
    { key: 'lu2', label: menuLink('/leaps-ultimate-v2', <SwapOutlined />, '终极2.0', '#531dab') },
    { key: 'us', label: menuLink('/us-leaps', <DollarOutlined />, '美股LEAPS', '#389e0d') },
    { key: 'qqq', label: menuLink('/qqq-leaps', <FundOutlined />, 'QQQ策略', '#c41d7f') },
  ],
};

const toolItems = {
  items: [
    { key: 'iv', label: menuLink('/iv-calculator', <CalculatorOutlined />, 'IV计算器') },
    { key: 'debug', label: menuLink('/deribit-debug', <BugOutlined />, '数据调试') },
    { key: 'dc', label: menuLink('/data-center', <CloudServerOutlined />, '数据中心', '#08979c') },
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
          <span style={{ color: '#95de64', fontSize: 13, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 4 }}>
            <RocketOutlined /> LEAPS <DownOutlined style={{ fontSize: 10 }} />
          </span>
        </Dropdown>
        <Dropdown menu={toolItems} placement="bottomRight">
          <span style={{ color: '#5cdbd3', fontSize: 13, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 4 }}>
            <ToolOutlined /> 工具 <DownOutlined style={{ fontSize: 10 }} />
          </span>
        </Dropdown>
      </Space>
    </AntHeader>
  );
}
