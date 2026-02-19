# 期权风险监控系统 - 设计文档

## 1. 系统架构

### 1.1 整体架构
系统采用前后端分离的架构：
- **前端**：React单页应用（SPA）
- **后端**：Python FastAPI REST API服务
- **数据库**：SQLite（开发）/ PostgreSQL（生产）
- **通信**：HTTP/JSON RESTful API

```
┌─────────────────┐         ┌─────────────────┐         ┌─────────────┐
│   React前端     │ ◄─────► │  FastAPI后端    │ ◄─────► │   数据库    │
│   (UI层)        │  HTTP   │  (业务逻辑层)   │   ORM   │  (持久层)   │
└─────────────────┘         └─────────────────┘         └─────────────┘
                                     │
                                     ▼
                            ┌─────────────────┐
                            │ Black-Scholes   │
                            │   定价引擎      │
                            └─────────────────┘
```

### 1.2 技术栈详细说明

**前端：**
- React 18+
- Recharts（图表可视化）
- Ant Design（UI组件库）
- Axios（HTTP客户端）
- Day.js（日期处理）

**后端：**
- Python 3.10+
- FastAPI（Web框架）
- SQLAlchemy（ORM）
- Pydantic（数据验证）
- NumPy/SciPy（数值计算）

**数据库：**
- SQLite（开发环境）
- PostgreSQL（生产环境，预留）

## 2. 数据模型

### 2.1 数据库表结构

#### Position（持仓表）
```sql
CREATE TABLE positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    underlying_symbol VARCHAR(20) NOT NULL,      -- 标的代码
    option_type VARCHAR(4) NOT NULL,             -- 'PUT' 或 'CALL'
    strike_price DECIMAL(10, 2) NOT NULL,        -- 行权价
    expiration_date DATE NOT NULL,               -- 到期日
    quantity INTEGER NOT NULL,                   -- 合约数量（负数=卖出）
    entry_price DECIMAL(10, 4) NOT NULL,         -- 开仓价格（权利金）
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

#### MarketPrice（市场价格表）
```sql
CREATE TABLE market_prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    underlying_symbol VARCHAR(20) NOT NULL UNIQUE,
    current_price DECIMAL(10, 2) NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

#### VolatilityScenario（波动率情景表）
```sql
CREATE TABLE volatility_scenarios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name VARCHAR(50) NOT NULL,                   -- 情景名称
    underlying_symbol VARCHAR(20) NOT NULL,
    implied_volatility DECIMAL(5, 4) NOT NULL,   -- 隐含波动率（如0.25表示25%）
    is_default BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 2.2 数据传输对象（DTO）

#### PositionDTO
```python
{
    "id": int,
    "underlying_symbol": str,
    "option_type": "PUT" | "CALL",
    "strike_price": float,
    "expiration_date": str,  # ISO格式: "2024-12-31"
    "quantity": int,
    "entry_price": float
}
```

#### PnLCalculationRequest
```python
{
    "underlying_symbol": str,
    "current_price": float,
    "implied_volatility": float,
    "price_range_percent": float,  # 默认0.5（50%）
    "target_date": str | null      # 可选，计算特定日期的盈亏
}
```

#### PnLCalculationResponse
```python
{
    "underlying_symbol": str,
    "current_price": float,
    "price_points": [
        {
            "price": float,
            "total_pnl": float,
            "position_values": [
                {
                    "position_id": int,
                    "current_value": float,
                    "pnl": float
                }
            ]
        }
    ],
    "max_loss": {
        "amount": float,
        "at_price": float
    },
    "max_profit": {
        "amount": float,
        "at_price": float
    }
}
```

## 3. API设计

### 3.1 持仓管理API

#### 创建持仓
```
POST /api/positions
Content-Type: application/json

Request Body: PositionDTO (without id)
Response: PositionDTO (with id)
Status: 201 Created
```

#### 获取所有持仓
```
GET /api/positions?underlying_symbol={symbol}
Response: PositionDTO[]
Status: 200 OK
```

#### 获取单个持仓
```
GET /api/positions/{id}
Response: PositionDTO
Status: 200 OK | 404 Not Found
```

#### 更新持仓
```
PUT /api/positions/{id}
Content-Type: application/json

Request Body: PositionDTO
Response: PositionDTO
Status: 200 OK | 404 Not Found
```

#### 删除持仓
```
DELETE /api/positions/{id}
Response: {"message": "Position deleted"}
Status: 200 OK | 404 Not Found
```

### 3.2 市场价格API

#### 更新市场价格
```
POST /api/market-prices
Content-Type: application/json

Request Body: {
    "underlying_symbol": str,
    "current_price": float
}
Response: {"underlying_symbol": str, "current_price": float}
Status: 200 OK
```

#### 获取市场价格
```
GET /api/market-prices/{symbol}
Response: {"underlying_symbol": str, "current_price": float}
Status: 200 OK | 404 Not Found
```

### 3.3 盈亏计算API

#### 计算组合盈亏
```
POST /api/calculate-pnl
Content-Type: application/json

Request Body: PnLCalculationRequest
Response: PnLCalculationResponse
Status: 200 OK
```

### 3.4 波动率情景API

#### 创建波动率情景
```
POST /api/volatility-scenarios
Content-Type: application/json

Request Body: {
    "name": str,
    "underlying_symbol": str,
    "implied_volatility": float,
    "is_default": bool
}
Response: VolatilityScenarioDTO
Status: 201 Created
```

#### 获取波动率情景
```
GET /api/volatility-scenarios?underlying_symbol={symbol}
Response: VolatilityScenarioDTO[]
Status: 200 OK
```

## 4. Black-Scholes定价引擎

### 4.1 核心计算函数

#### black_scholes_price()
计算期权理论价格
```python
def black_scholes_price(
    S: float,      # 标的当前价格
    K: float,      # 行权价
    T: float,      # 距到期时间（年）
    r: float,      # 无风险利率
    sigma: float,  # 隐含波动率
    option_type: str  # 'call' 或 'put'
) -> float:
    """返回期权理论价格"""
```

**公式：**
- d1 = [ln(S/K) + (r + σ²/2)T] / (σ√T)
- d2 = d1 - σ√T
- Call价格 = S·N(d1) - K·e^(-rT)·N(d2)
- Put价格 = K·e^(-rT)·N(-d2) - S·N(-d1)

其中N(x)是标准正态分布的累积分布函数。

#### calculate_position_value()
计算单个持仓的当前价值
```python
def calculate_position_value(
    position: Position,
    current_price: float,
    implied_volatility: float,
    risk_free_rate: float = 0.05,
    target_date: datetime | None = None
) -> dict:
    """
    返回：{
        "current_value": float,  # 当前持仓价值
        "pnl": float,           # 盈亏
        "entry_cost": float     # 开仓成本
    }
    """
```

#### calculate_portfolio_pnl()
计算整个组合在不同价格点的盈亏
```python
def calculate_portfolio_pnl(
    positions: list[Position],
    price_points: list[float],
    implied_volatility: float,
    risk_free_rate: float = 0.05,
    target_date: datetime | None = None
) -> list[dict]:
    """
    返回每个价格点的组合盈亏
    """
```

### 4.2 价格点生成策略

为了生成平滑的盈亏曲线，在当前价格±50%范围内生成价格点：

```python
def generate_price_points(
    current_price: float,
    range_percent: float = 0.5,
    num_points: int = 100
) -> list[float]:
    """
    生成价格点数组
    例如：current_price=100, range_percent=0.5
    返回：[50.0, 51.0, ..., 100.0, ..., 150.0]
    """
    min_price = current_price * (1 - range_percent)
    max_price = current_price * (1 + range_percent)
    return np.linspace(min_price, max_price, num_points).tolist()
```

### 4.3 最大亏损计算

```python
def find_max_loss(pnl_data: list[dict]) -> dict:
    """
    从盈亏数据中找出最大亏损点
    返回：{
        "amount": float,
        "at_price": float
    }
    """
    min_pnl = min(pnl_data, key=lambda x: x['total_pnl'])
    return {
        "amount": min_pnl['total_pnl'],
        "at_price": min_pnl['price']
    }
```

## 5. 前端设计

### 5.1 页面结构

```
App
├── Header（顶部导航栏）
├── MainLayout
│   ├── PositionPanel（持仓管理面板）
│   │   ├── PositionForm（添加/编辑持仓表单）
│   │   └── PositionList（持仓列表）
│   ├── ControlPanel（控制面板）
│   │   ├── PriceInput（价格输入）
│   │   └── VolatilityInput（波动率输入）
│   └── AnalysisPanel（分析面板）
│       ├── PnLChart（盈亏曲线图）
│       ├── PnLTable（盈亏数据表）
│       └── RiskMetrics（风险指标卡片）
└── Footer
```

### 5.2 核心组件设计

#### PositionForm组件
```jsx
<Form onSubmit={handleSubmit}>
  <Input name="underlying_symbol" label="标的代码" />
  <Select name="option_type" options={['PUT', 'CALL']} />
  <InputNumber name="strike_price" label="行权价" />
  <DatePicker name="expiration_date" label="到期日" />
  <InputNumber name="quantity" label="合约数量" />
  <InputNumber name="entry_price" label="开仓价格" />
  <Button type="submit">添加持仓</Button>
</Form>
```

#### PnLChart组件
使用Recharts绘制盈亏曲线：
```jsx
<LineChart data={pnlData}>
  <XAxis dataKey="price" label="标的价格" />
  <YAxis label="盈亏" />
  <Line 
    type="monotone" 
    dataKey="total_pnl" 
    stroke="#8884d8" 
  />
  <ReferenceLine 
    x={currentPrice} 
    stroke="green" 
    label="当前价格" 
  />
  <ReferenceLine 
    x={maxLossPrice} 
    stroke="red" 
    label="最大亏损点" 
  />
  <Tooltip />
</LineChart>
```

#### RiskMetrics组件
显示关键风险指标：
```jsx
<Card>
  <Statistic 
    title="当前盈亏" 
    value={currentPnL} 
    valueStyle={{color: currentPnL >= 0 ? 'green' : 'red'}}
  />
  <Statistic 
    title="最大潜在亏损" 
    value={maxLoss.amount} 
    suffix={`@ ${maxLoss.at_price}`}
    valueStyle={{color: 'red'}}
  />
  <Statistic 
    title="最大潜在盈利" 
    value={maxProfit.amount} 
    suffix={`@ ${maxProfit.at_price}`}
    valueStyle={{color: 'green'}}
  />
</Card>
```

### 5.3 状态管理

使用React Context + useReducer管理全局状态：

```javascript
const AppContext = {
  positions: Position[],
  marketPrices: Map<string, number>,
  volatilityScenarios: Map<string, number>,
  pnlData: PnLCalculationResponse | null,
  loading: boolean,
  error: string | null
}

const actions = {
  ADD_POSITION,
  UPDATE_POSITION,
  DELETE_POSITION,
  SET_MARKET_PRICE,
  SET_VOLATILITY,
  SET_PNL_DATA,
  SET_LOADING,
  SET_ERROR
}
```

### 5.4 数据流

1. 用户添加持仓 → POST /api/positions → 更新本地状态
2. 用户输入价格 → POST /api/market-prices → 触发盈亏计算
3. 用户调整波动率 → 触发盈亏计算
4. 盈亏计算 → POST /api/calculate-pnl → 更新图表和指标

## 6. 正确性属性（Correctness Properties）

### 6.1 期权定价正确性

**Property 6.1.1: Put-Call Parity**
- **描述**：对于相同标的、相同行权价、相同到期日的欧式看涨和看跌期权，必须满足平价关系
- **公式**：C - P = S - K·e^(-rT)
- **测试策略**：生成随机的S、K、T、r、σ参数，验证计算出的Call和Put价格满足平价关系（允许小误差）

**Property 6.1.2: 价格单调性**
- **描述**：
  - Put期权价格随标的价格上升而下降
  - Put期权价格随行权价上升而上升
  - 期权价格随波动率上升而上升
- **测试策略**：固定其他参数，单独增加某个参数，验证价格变化方向

**Property 6.1.3: 价格边界**
- **描述**：
  - Put期权价格 ≥ max(0, K·e^(-rT) - S)
  - Put期权价格 ≤ K·e^(-rT)
- **测试策略**：生成随机参数，验证计算价格在理论边界内

### 6.2 盈亏计算正确性

**Property 6.2.1: 盈亏守恒**
- **描述**：组合总盈亏 = Σ(单个持仓盈亏)
- **测试策略**：计算组合盈亏和各持仓盈亏之和，验证相等

**Property 6.2.2: 到期日盈亏**
- **描述**：在到期日（T=0），Put期权价值 = max(0, K - S)
- **测试策略**：设置target_date为到期日，验证期权价值符合到期收益公式

**Property 6.2.3: 卖出持仓符号**
- **描述**：卖出持仓（quantity < 0）的盈亏符号与买入持仓相反
- **测试策略**：创建相同参数但数量相反的两个持仓，验证盈亏符号相反且绝对值相等

### 6.3 数据持久化正确性

**Property 6.3.1: CRUD操作幂等性**
- **描述**：
  - 创建后读取的数据与创建时的数据一致
  - 更新后读取的数据与更新时的数据一致
  - 删除后无法读取该数据
- **测试策略**：执行CRUD操作序列，验证数据状态符合预期

**Property 6.3.2: 数据完整性**
- **描述**：所有必填字段不能为空，数值字段在合理范围内
- **测试策略**：尝试创建无效数据，验证系统拒绝并返回错误

### 6.4 API正确性

**Property 6.4.1: 响应格式一致性**
- **描述**：所有API响应必须符合定义的DTO格式
- **测试策略**：调用各API端点，验证响应JSON结构符合schema

**Property 6.4.2: 错误处理一致性**
- **描述**：无效请求返回4xx状态码，服务器错误返回5xx状态码
- **测试策略**：发送各种无效请求，验证返回正确的HTTP状态码和错误信息

## 7. 测试策略

### 7.1 Property-Based Testing框架
- **Python后端**：使用 `hypothesis` 库
- **JavaScript前端**：使用 `fast-check` 库

### 7.2 测试用例生成策略

#### 期权参数生成器
```python
from hypothesis import strategies as st

option_params = st.fixed_dictionaries({
    'S': st.floats(min_value=1.0, max_value=1000.0),
    'K': st.floats(min_value=1.0, max_value=1000.0),
    'T': st.floats(min_value=0.01, max_value=5.0),
    'r': st.floats(min_value=0.0, max_value=0.2),
    'sigma': st.floats(min_value=0.01, max_value=2.0)
})
```

#### 持仓数据生成器
```python
position_data = st.fixed_dictionaries({
    'underlying_symbol': st.text(min_size=1, max_size=10, alphabet=st.characters(whitelist_categories=('Lu',))),
    'option_type': st.sampled_from(['PUT', 'CALL']),
    'strike_price': st.floats(min_value=1.0, max_value=1000.0),
    'expiration_date': st.dates(min_value=date.today(), max_value=date.today() + timedelta(days=730)),
    'quantity': st.integers(min_value=-100, max_value=100).filter(lambda x: x != 0),
    'entry_price': st.floats(min_value=0.01, max_value=100.0)
})
```

### 7.3 单元测试覆盖

- Black-Scholes计算函数
- 盈亏计算函数
- 数据库CRUD操作
- API端点

### 7.4 集成测试

- 前后端API集成
- 数据库事务完整性
- 端到端用户流程

## 8. 部署架构

### 8.1 开发环境
```
localhost:3000  →  React开发服务器
localhost:8000  →  FastAPI开发服务器
SQLite文件数据库
```

### 8.2 生产环境（预留）
```
Nginx  →  静态文件（React构建产物）
       →  反向代理 → FastAPI (Gunicorn/Uvicorn)
                   → PostgreSQL数据库
```

## 9. 扩展接口设计

### 9.1 实时数据API接口（预留）
```python
class MarketDataProvider(ABC):
    @abstractmethod
    async def get_current_price(self, symbol: str) -> float:
        pass
    
    @abstractmethod
    async def get_implied_volatility(self, symbol: str) -> float:
        pass

# 未来实现
class InteractiveBrokersProvider(MarketDataProvider):
    pass

class YahooFinanceProvider(MarketDataProvider):
    pass
```

### 9.2 Greeks计算接口（预留）
```python
def calculate_greeks(
    S: float, K: float, T: float, r: float, sigma: float, option_type: str
) -> dict:
    """
    返回：{
        "delta": float,
        "gamma": float,
        "theta": float,
        "vega": float,
        "rho": float
    }
    """
    pass
```

## 10. 性能优化

### 10.1 计算优化
- 使用NumPy向量化计算批量价格点
- 缓存重复计算结果（相同参数的期权价格）

### 10.2 前端优化
- 使用React.memo避免不必要的重渲染
- 图表数据采样（大量数据点时）
- 防抖输入框（避免频繁触发计算）

### 10.3 数据库优化
- 为常用查询字段添加索引（underlying_symbol, expiration_date）
- 使用连接池管理数据库连接

## 11. 安全考虑

### 11.1 输入验证
- 所有API输入使用Pydantic模型验证
- 防止SQL注入（使用ORM参数化查询）
- 限制数值输入范围，防止计算溢出

### 11.2 错误处理
- 不暴露内部错误详情给前端
- 记录详细错误日志用于调试
- 优雅处理计算异常（如除零、对数负数等）

## 12. 开发里程碑

### Phase 1: 核心功能（MVP）
- 数据库模型和API
- Black-Scholes定价引擎
- 基本的持仓管理UI
- 盈亏曲线图

### Phase 2: 增强功能
- 波动率情景管理
- 最大亏损警告
- 多到期日支持
- 数据导入导出

### Phase 3: 扩展功能（未来）
- 实时数据API集成
- Greeks指标
- 多用户支持
- 移动端适配
