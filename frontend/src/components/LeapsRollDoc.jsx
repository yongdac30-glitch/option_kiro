/**
 * LEAPS 换仓策略回测逻辑文档
 *
 * 以 Collapse 面板形式嵌入 USLeaps 页面，详细说明换仓策略的回测流程、
 * 核心公式、参数含义和决策逻辑。
 */
import { Typography, Collapse, Table, Tag, Divider, Alert } from 'antd';
import { BookOutlined } from '@ant-design/icons';

const { Title, Text, Paragraph } = Typography;

const formulaStyle = {
  background: '#f6f8fa', padding: '8px 12px', borderRadius: 6,
  fontFamily: 'monospace', fontSize: 13, margin: '8px 0', display: 'block',
  border: '1px solid #e8e8e8',
};

const sectionTitle = { margin: '12px 0 6px', fontSize: 15 };

export default function LeapsRollDoc() {
  return (
    <Collapse
      style={{ marginTop: 16 }}
      items={[{
        key: 'roll-doc',
        label: (
          <span>
            <BookOutlined style={{ marginRight: 8, color: '#1890ff' }} />
            <Text strong>LEAPS 换仓策略回测逻辑文档</Text>
            <Tag color="blue" style={{ marginLeft: 8 }}>点击展开</Tag>
          </span>
        ),
        children: <DocContent />,
      }]}
    />
  );
}

function DocContent() {
  const paramCols = [
    { title: '参数名', dataIndex: 'name', width: 180 },
    { title: '默认值', dataIndex: 'default', width: 100 },
    { title: '说明', dataIndex: 'desc' },
  ];
  const paramData = [
    { key: '1', name: 'enable_roll (启用换仓)', default: 'false', desc: '是否在回测中启用换仓逻辑。关闭时回测行为与原始策略完全一致。' },
    { key: '2', name: 'roll_annual_tv_pct (换仓年化TV阈值%)', default: '8.0', desc: '换仓年化成本低于此阈值时才执行换仓。值越大越容易触发换仓，值越小越保守。' },
    { key: '3', name: 'max_annual_tv_pct (开仓年化TV%)', default: '10.0', desc: '开仓时筛选合约的年化时间价值上限。与换仓阈值独立。' },
    { key: '4', name: 'num_strikes (扫描行权价数量)', default: '15', desc: '开仓和换仓扫描时，从ATM向下扫描的行权价数量。' },
    { key: '5', name: 'close_days_before (到期前N天平仓)', default: '30', desc: '持仓到期前N天强制平仓。平仓优先级高于换仓检查。' },
    { key: '6', name: 'open_interval_days (检查间隔)', default: '30', desc: '每隔N天执行一次观察（平仓/换仓/开仓检查）。' },
    { key: '7', name: 'min_expiry_months (最短到期月数)', default: '12', desc: '开仓时选择的合约到期日至少在N个月之后。' },
  ];

  const loopCols = [
    { title: '步骤', dataIndex: 'step', width: 60 },
    { title: '名称', dataIndex: 'name', width: 120 },
    { title: '条件', dataIndex: 'condition', width: 260 },
    { title: '动作', dataIndex: 'action' },
  ];
  const loopData = [
    { key: '1', step: '1', name: '到期平仓', condition: '持有仓位 且 距到期 ≤ close_days_before', action: '用BS模型计算当前期权价，卖出平仓，释放资金。' },
    { key: '1.5', step: '1.5', name: '换仓检查', condition: '持有仓位 且 未触发平仓 且 enable_roll=true', action: '调用 _check_roll()，若找到目标则执行：平仓当前 → 开仓新合约。' },
    { key: '2', step: '2', name: '开仓', condition: '无持仓 且 有可用资金', action: '寻找最优到期日，从ATM向下扫描行权价，选择年化TV%最低且满足阈值的合约买入。' },
    { key: '3', step: '3', name: '盯市', condition: '每个观察日', action: '用BS模型计算持仓市值，记录权益曲线点。' },
  ];

  return (
    <Typography style={{ padding: '0 8px' }}>
      {/* ── 一、策略概述 ── */}
      <Title level={5} style={sectionTitle}>一、策略概述</Title>
      <Paragraph>
        LEAPS 换仓策略是在基础 LEAPS CALL 持有策略上增加的优化模块。基础策略的核心思路是：
        买入深度实值（Deep ITM）的长期 CALL 期权，利用其高 Delta（接近1）来替代直接持有股票，
        同时以较低的时间价值成本获得杠杆收益。
      </Paragraph>
      <Paragraph>
        换仓（Roll）的目的是：当持仓合约的时间价值逐渐衰减后，如果市场上存在更远到期日的合约，
        且"换过去"的年化成本足够低，就主动平掉当前合约、买入更远期的合约，从而：
      </Paragraph>
      <ul>
        <li>延长持仓的有效期，避免被迫在到期前平仓后重新开仓</li>
        <li>在时间价值衰减曲线的"平坦区"换仓，降低整体持仓成本</li>
        <li>保持持续的市场敞口，减少空仓期</li>
      </ul>

      <Divider />

      {/* ── 二、参数说明 ── */}
      <Title level={5} style={sectionTitle}>二、参数说明</Title>
      <Table columns={paramCols} dataSource={paramData} size="small" pagination={false}
        style={{ marginBottom: 16 }} />

      <Divider />

      {/* ── 三、回测主循环 ── */}
      <Title level={5} style={sectionTitle}>三、回测主循环流程</Title>
      <Paragraph>
        回测按 <Text code>open_interval_days</Text> 间隔生成观察日序列。每个观察日按以下顺序执行：
      </Paragraph>
      <Table columns={loopCols} dataSource={loopData} size="small" pagination={false}
        style={{ marginBottom: 16 }} />
      <Alert type="info" showIcon style={{ marginBottom: 12 }}
        message="执行优先级：平仓 > 换仓 > 开仓。同一天不会同时触发平仓和换仓。换仓后不会再触发开仓（因为已有持仓）。" />

      <Divider />

      {/* ── 四、换仓核心逻辑 ── */}
      <Title level={5} style={sectionTitle}>四、换仓核心逻辑 (_check_roll)</Title>

      <Paragraph strong>第一步：计算当前持仓的时间价值</Paragraph>
      <Paragraph>
        用 Black-Scholes 模型计算当前持仓合约的理论价格，分解为内在价值和时间价值：
      </Paragraph>
      <Text style={formulaStyle}>
        cur_price = BS(spot, cur_strike, T_cur, r=4.5%, iv, "CALL")<br />
        cur_intrinsic = max(0, spot - cur_strike)<br />
        cur_tv = max(0, cur_price - cur_intrinsic)
      </Text>
      <Paragraph>
        如果 <Text code>cur_price ≤ 0</Text>，说明当前合约已无价值，跳过换仓检查。
      </Paragraph>

      <Paragraph strong>第二步：生成候选远期到期日</Paragraph>
      <Paragraph>
        生成所有可能的美股期权到期日（1月LEAPS + 季度月份的第三个周五），筛选出：
      </Paragraph>
      <ul>
        <li>到期日严格晚于当前持仓到期日</li>
        <li>与当前到期日间隔 ≥ 30 天（避免无意义的短期换仓）</li>
      </ul>
      <Paragraph>
        候选到期日按<Text strong>从远到近</Text>排序（优先选择最远的到期日）。
      </Paragraph>

      <Paragraph strong>第三步：逐到期日、逐行权价扫描</Paragraph>
      <Paragraph>
        对每个候选到期日，从 ATM 向下扫描 <Text code>num_strikes</Text> 个行权价。
        行权价步长根据股价自动确定：
      </Paragraph>
      <Text style={formulaStyle}>
        股价 &lt; $25 → 步长 $2.5<br />
        股价 $25~$200 → 步长 $5<br />
        股价 $200~$500 → 步长 $10<br />
        股价 &gt; $500 → 步长 $25
      </Text>
      <Paragraph>
        对每个 (到期日, 行权价) 组合，用 BS 模型计算远期合约价格，分解时间价值，
        然后计算<Text strong>年化换仓成本</Text>：
      </Paragraph>
      <Text style={formulaStyle}>
        far_price = BS(spot, far_strike, T_far, r=4.5%, iv, "CALL")<br />
        far_intrinsic = max(0, spot - far_strike)<br />
        far_tv = max(0, far_price - far_intrinsic)<br />
        tv_diff = far_tv - cur_tv<br />
        <br />
        annual_roll_cost = (tv_diff / far_strike) × (365 / far_days) × 100%
      </Text>

      <Alert type="warning" showIcon style={{ margin: '8px 0 12px' }}
        message="年化换仓成本的含义"
        description={
          <div>
            <p style={{ margin: '4px 0' }}>
              <Text code>tv_diff</Text> = 远期合约时间价值 - 当前合约时间价值，代表"换仓需要额外支付的时间价值"。
            </p>
            <p style={{ margin: '4px 0' }}>
              将这个差值除以行权价（归一化），再年化，得到一个可比较的百分比指标。
              这个值越低，说明换仓的"性价比"越高。
            </p>
            <p style={{ margin: '4px 0' }}>
              例如：年化换仓成本 = 5% 意味着每年为延长持仓付出的额外时间价值成本约为行权价的 5%。
            </p>
          </div>
        }
      />

      <Paragraph strong>第四步：选择换仓目标</Paragraph>
      <Paragraph>
        扫描顺序为"最远到期日优先 → ATM 向下"。找到第一个满足条件的合约即停止：
      </Paragraph>
      <Text style={formulaStyle}>
        annual_roll_cost &lt; roll_annual_tv_pct（默认 8%）
      </Text>
      <Paragraph>
        一旦在某个到期日找到满足条件的合约，不再继续扫描更近的到期日（因为更远的到期日通常年化成本更低）。
      </Paragraph>

      <Divider />

      {/* ── 五、换仓执行 ── */}
      <Title level={5} style={sectionTitle}>五、换仓执行流程</Title>
      <Paragraph>
        当 <Text code>_check_roll()</Text> 返回有效的换仓目标时，主循环执行以下操作：
      </Paragraph>
      <ol>
        <li>
          <Text strong>平仓当前合约</Text>：以 BS 模型计算的当前价格卖出，资金回到现金池。
          记录一条 <Tag color="#8c8c8c">CLOSE</Tag> 交易，备注标记为"换仓平仓"。
        </li>
        <li>
          <Text strong>买入新合约</Text>：以 BS 模型计算的远期价格买入。如果资金不足以买入原计划数量，
          自动减少合约数（备注显示"原计划X张,实际Y张"）。
          记录一条 <Tag color="#faad14">ROLL</Tag> 交易，备注包含新行权价、新到期日和年化换仓成本。
        </li>
        <li>
          <Text strong>更新持仓</Text>：position 更新为新合约信息，roll_count 计数器 +1。
        </li>
      </ol>
      <Alert type="info" showIcon style={{ marginBottom: 12 }}
        message="如果换仓时资金不足（平仓回收的资金不够买入新合约），则仓位变为空仓，下一个观察日会尝试重新开仓。" />

      <Divider />

      {/* ── 六、定价模型 ── */}
      <Title level={5} style={sectionTitle}>六、定价模型说明</Title>
      <Paragraph>
        回测中所有期权价格均使用 Black-Scholes 模型计算：
      </Paragraph>
      <Text style={formulaStyle}>
        C = S·N(d₁) - K·e^(-rT)·N(d₂)<br />
        d₁ = [ln(S/K) + (r + σ²/2)T] / (σ√T)<br />
        d₂ = d₁ - σ√T<br />
        <br />
        其中: S=标的价格, K=行权价, T=到期时间(年), r=无风险利率(4.5%), σ=隐含波动率(default_iv)
      </Text>
      <Paragraph>
        注意事项：
      </Paragraph>
      <ul>
        <li>回测使用固定 IV（<Text code>default_iv</Text> 参数），不反映真实的 IV 波动。建议 AAPL 用 0.25~0.35，TSLA 用 0.5~0.7。</li>
        <li>无风险利率固定为 4.5%（近似当前美国国债收益率）。</li>
        <li>合约乘数固定为 100（1张合约 = 100股）。</li>
        <li>实时扫描使用 yfinance 真实 bid/ask 报价，不依赖 BS 模型。</li>
      </ul>

      <Divider />

      {/* ── 七、扫描日志 ── */}
      <Title level={5} style={sectionTitle}>七、扫描日志解读</Title>
      <Paragraph>
        回测结果中的"扫描日志"记录了每次观察日的决策过程。换仓相关的日志包含：
      </Paragraph>
      <ul>
        <li><Tag color="gold">换仓(...)</Tag> — 成功执行换仓，显示行权价和到期日的变化</li>
        <li><Tag color="blue">持仓中(...无需换仓)</Tag> — 检查了换仓但未找到满足条件的合约</li>
        <li><Tag color="green">开仓(...)</Tag> — 无持仓时的新开仓记录</li>
      </ul>
      <Paragraph>
        展开日志可以看到"换仓扫描"表格，每行显示一个候选合约的详细信息：
        行权价、到期日、远期TV、当前TV、TV差值、年化换仓成本，以及是否被选中。
      </Paragraph>

      <Divider />

      {/* ── 八、策略对比建议 ── */}
      <Title level={5} style={sectionTitle}>八、使用建议</Title>
      <ul>
        <li>先关闭换仓跑一次回测，再开启换仓跑一次，对比两者的收益率、最大回撤和夏普比率。</li>
        <li>换仓阈值建议从 8% 开始调整。降低阈值（如 5%）会减少换仓次数但每次换仓更"划算"；提高阈值（如 12%）会增加换仓频率。</li>
        <li>高波动标的（如 TSLA）的时间价值较高，换仓成本也较高，可能需要提高阈值才能触发换仓。</li>
        <li>低波动标的（如 SPY）的时间价值较低，换仓成本也较低，默认阈值通常就能触发。</li>
        <li>检查间隔（open_interval_days）也会影响换仓频率——间隔越短，检查越频繁，越可能捕捉到换仓机会。</li>
      </ul>
    </Typography>
  );
}
