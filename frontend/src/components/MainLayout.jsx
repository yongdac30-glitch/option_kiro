/**
 * Main Layout Component
 * Application main layout structure
 */
import { Layout, Row, Col } from 'antd';
import Header from './Header';
import PortfolioSelector from './PortfolioSelector';
import PositionPanel from './PositionPanel';
import ControlPanel from './ControlPanel';
import AnalysisPanel from './AnalysisPanel';

const { Content, Footer } = Layout;

export default function MainLayout() {
  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Header />

      <Content style={{ padding: '24px', background: '#f0f2f5' }}>
        <div style={{ maxWidth: 1400, margin: '0 auto' }}>
          <Row gutter={[16, 16]}>
            {/* Portfolio Selector */}
            <Col xs={24}>
              <div style={{ marginBottom: 8 }}>
                <PortfolioSelector />
              </div>
            </Col>

            {/* Position Management */}
            <Col xs={24}>
              <PositionPanel />
            </Col>

            {/* Control Panel */}
            <Col xs={24}>
              <ControlPanel />
            </Col>

            {/* Analysis Panel */}
            <Col xs={24}>
              <AnalysisPanel />
            </Col>
          </Row>
        </div>
      </Content>

      <Footer style={{ textAlign: 'center', background: '#001529', color: 'white' }}>
        期权风险监控系统 ©2024 | 基于 Black-Scholes 模型
      </Footer>
    </Layout>
  );
}
