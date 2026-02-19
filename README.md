# 期权风险监控系统 (Options Risk Monitor)

一个基于Web的期权组合风险监控系统，用于实时监控多个期权合约在不同市场价格和隐含波动率情况下的潜在盈亏情况。

## 项目结构

```
.
├── backend/                 # Python FastAPI 后端
│   ├── app/
│   │   ├── api/            # API路由
│   │   ├── core/           # 核心配置
│   │   ├── models/         # 数据库模型
│   │   ├── schemas/        # Pydantic schemas
│   │   ├── services/       # 业务逻辑
│   │   └── repositories/   # 数据访问层
│   ├── tests/              # 测试
│   └── requirements.txt    # Python依赖
│
├── frontend/               # React 前端
│   ├── src/
│   │   ├── components/    # React组件
│   │   ├── services/      # API客户端
│   │   ├── context/       # 状态管理
│   │   └── utils/         # 工具函数
│   └── package.json       # Node依赖
│
└── .kiro/specs/           # 项目规格文档
    └── options-risk-monitor/
        ├── requirements.md
        ├── design.md
        └── tasks.md
```

## 技术栈

### 后端
- Python 3.10+
- FastAPI (Web框架)
- SQLAlchemy (ORM)
- SQLite (数据库)
- NumPy/SciPy (数值计算)
- Black-Scholes模型 (期权定价)

### 前端
- React 19
- Vite (构建工具)
- Ant Design (UI组件库)
- Recharts (图表库)
- Axios (HTTP客户端)

## 快速开始

### 方式一：使用启动脚本（推荐）

1. **启动后端**：
```bash
./start-backend.sh
```

2. **启动前端**（新终端窗口）：
```bash
./start-frontend.sh
```

### 方式二：手动启动

#### 后端设置

1. 创建Python虚拟环境：
```bash
cd backend
python3 -m venv venv
source venv/bin/activate  # macOS/Linux
# 或 venv\Scripts\activate  # Windows
```

2. 安装依赖：
```bash
pip install -r requirements.txt
```

3. 启动后端服务：
```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

后端API将运行在 http://localhost:8000

#### 前端设置

1. 安装依赖：
```bash
cd frontend
npm install
```

2. 启动开发服务器：
```bash
npm run dev
```

前端应用将运行在 http://localhost:5174

## 使用指南

### 1. 添加持仓

1. 点击"添加持仓"按钮
2. 填写期权合约信息：
   - **标的代码**：例如 AAPL、TSLA
   - **期权类型**：Put 或 Call
   - **行权价**：期权的执行价格
   - **到期日**：期权到期日期
   - **合约数量**：正数表示买入，负数表示卖出（例如：-1 表示卖出1张）
   - **开仓价格**：支付或收到的权利金
3. 点击"添加持仓"保存

### 2. 计算盈亏

1. 在控制面板中：
   - 选择要分析的标的代码
   - 输入当前市场价格
   - 设置隐含波动率（百分比）
2. 点击"计算盈亏"按钮
3. 查看结果：
   - **风险指标卡片**：显示当前盈亏、最大潜在亏损和盈利
   - **盈亏曲线图**：展示在不同价格下的组合盈亏情况

### 3. 管理持仓

- **编辑**：点击持仓列表中的"编辑"按钮修改持仓信息
- **删除**：点击"删除"按钮移除持仓（需确认）
- **查看**：持仓列表显示所有合约的详细信息

## API文档

启动后端服务后，访问以下地址查看自动生成的API文档：
- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

## 核心功能说明

### Black-Scholes 定价模型

系统使用 Black-Scholes 模型计算期权理论价格：

- **Call 价格** = S·N(d1) - K·e^(-rT)·N(d2)
- **Put 价格** = K·e^(-rT)·N(-d2) - S·N(-d1)

其中：
- S = 标的当前价格
- K = 行权价
- T = 距到期时间（年）
- r = 无风险利率（默认5%）
- σ = 隐含波动率
- N(x) = 标准正态分布累积函数

### 盈亏计算

系统在当前价格±50%的范围内生成100个价格点，计算每个价格点的组合盈亏：

1. **单个持仓盈亏** = (当前期权价值 - 开仓成本) × 合约数量 × 100
2. **组合总盈亏** = Σ(所有持仓盈亏)
3. **最大亏损/盈利** = 在所有价格点中找出最小/最大盈亏值

## 功能特性

- ✅ **持仓管理**：添加、编辑、删除期权合约
- ✅ **市场价格输入**：手动输入标的资产价格
- ✅ **隐含波动率自定义**：调整波动率参数进行情景分析
- ✅ **盈亏分析**：查看不同价格档位下的盈亏情况
- ✅ **可视化图表**：盈亏曲线图直观展示风险敞口
- ✅ **风险指标**：实时显示最大潜在亏损和盈利
- ✅ **多到期日支持**：处理不同到期日的合约
- ✅ **Black-Scholes定价**：使用经典期权定价模型

## 技术亮点

- **前后端分离架构**：React + FastAPI
- **响应式设计**：支持桌面和移动设备
- **实时计算**：快速计算100个价格点的盈亏
- **数据持久化**：SQLite数据库存储持仓信息
- **类型安全**：Pydantic数据验证
- **API文档自动生成**：Swagger/ReDoc

## 开发进度

查看 `.kiro/specs/options-risk-monitor/tasks.md` 了解详细的开发任务列表和进度。

## API文档

启动后端服务后，访问以下地址查看自动生成的API文档：
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## 测试

### 后端测试
```bash
cd backend
pytest
```

### 前端测试
```bash
cd frontend
npm run test
```

## 许可证

MIT
