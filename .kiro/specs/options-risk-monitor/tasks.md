# 期权风险监控系统 - 任务列表

## 1. 项目初始化与环境搭建

- [ ] 1.1 创建项目目录结构
  - [ ] 1.1.1 创建后端目录结构（backend/）
  - [ ] 1.1.2 创建前端目录结构（frontend/）
  - [ ] 1.1.3 创建共享配置目录（config/）

- [ ] 1.2 后端环境搭建
  - [ ] 1.2.1 创建Python虚拟环境
  - [ ] 1.2.2 创建requirements.txt并安装依赖（FastAPI、SQLAlchemy、NumPy、SciPy、Pydantic、hypothesis）
  - [ ] 1.2.3 配置FastAPI项目结构

- [ ] 1.3 前端环境搭建
  - [ ] 1.3.1 使用create-react-app或Vite创建React项目
  - [ ] 1.3.2 安装前端依赖（Ant Design、Recharts、Axios、Day.js、fast-check）
  - [ ] 1.3.3 配置代理以连接后端API

- [ ] 1.4 数据库初始化
  - [ ] 1.4.1 配置SQLAlchemy连接
  - [ ] 1.4.2 创建数据库初始化脚本

## 2. 后端开发 - 数据层

- [ ] 2.1 定义数据库模型
  - [ ] 2.1.1 创建Position模型（持仓表）
  - [ ] 2.1.2 创建MarketPrice模型（市场价格表）
  - [ ] 2.1.3 创建VolatilityScenario模型（波动率情景表）
  - [ ] 2.1.4 创建数据库迁移脚本

- [ ] 2.2 创建Pydantic Schema
  - [ ] 2.2.1 创建PositionDTO schema
  - [ ] 2.2.2 创建PnLCalculationRequest schema
  - [ ] 2.2.3 创建PnLCalculationResponse schema
  - [ ] 2.2.4 创建VolatilityScenarioDTO schema

- [ ] 2.3 实现数据访问层（Repository）
  - [ ] 2.3.1 实现PositionRepository（CRUD操作）
  - [ ] 2.3.2 实现MarketPriceRepository
  - [ ] 2.3.3 实现VolatilityScenarioRepository

## 3. 后端开发 - Black-Scholes定价引擎

- [ ] 3.1 实现核心定价函数
  - [ ] 3.1.1 实现标准正态分布累积函数N(x)
  - [ ] 3.1.2 实现black_scholes_price()函数
  - [ ] 3.1.3 实现calculate_time_to_expiration()辅助函数

- [ ] 3.2 实现盈亏计算函数
  - [ ] 3.2.1 实现calculate_position_value()函数
  - [ ] 3.2.2 实现calculate_portfolio_pnl()函数
  - [ ] 3.2.3 实现generate_price_points()函数
  - [ ] 3.2.4 实现find_max_loss()和find_max_profit()函数

- [ ] 3.3 编写Property-Based Tests（定价引擎）
  - [ ] 3.3.1 测试Property 6.1.1: Put-Call Parity
    **Validates: Requirements 6.1.1**
  - [ ] 3.3.2 测试Property 6.1.2: 价格单调性
    **Validates: Requirements 6.1.2**
  - [ ] 3.3.3 测试Property 6.1.3: 价格边界
    **Validates: Requirements 6.1.3**

- [ ] 3.4 编写单元测试（定价引擎）
  - [ ] 3.4.1 测试black_scholes_price()的已知案例
  - [ ] 3.4.2 测试边界条件（T→0, σ→0等）
  - [ ] 3.4.3 测试异常处理

## 4. 后端开发 - API层

- [ ] 4.1 实现持仓管理API
  - [ ] 4.1.1 POST /api/positions（创建持仓）
  - [ ] 4.1.2 GET /api/positions（获取所有持仓）
  - [ ] 4.1.3 GET /api/positions/{id}（获取单个持仓）
  - [ ] 4.1.4 PUT /api/positions/{id}（更新持仓）
  - [ ] 4.1.5 DELETE /api/positions/{id}（删除持仓）

- [ ] 4.2 实现市场价格API
  - [ ] 4.2.1 POST /api/market-prices（更新市场价格）
  - [ ] 4.2.2 GET /api/market-prices/{symbol}（获取市场价格）

- [ ] 4.3 实现盈亏计算API
  - [ ] 4.3.1 POST /api/calculate-pnl（计算组合盈亏）

- [ ] 4.4 实现波动率情景API
  - [ ] 4.4.1 POST /api/volatility-scenarios（创建波动率情景）
  - [ ] 4.4.2 GET /api/volatility-scenarios（获取波动率情景）

- [ ] 4.5 编写Property-Based Tests（API层）
  - [ ] 4.5.1 测试Property 6.4.1: 响应格式一致性
    **Validates: Requirements 6.4.1**
  - [ ] 4.5.2 测试Property 6.4.2: 错误处理一致性
    **Validates: Requirements 6.4.2**

- [ ] 4.6 编写API集成测试
  - [ ] 4.6.1 测试持仓管理API的完整CRUD流程
  - [ ] 4.6.2 测试盈亏计算API的各种场景
  - [ ] 4.6.3 测试错误处理和边界情况

## 5. 后端开发 - 数据持久化测试

- [ ] 5.1 编写Property-Based Tests（数据层）
  - [ ] 5.1.1 测试Property 6.3.1: CRUD操作幂等性
    **Validates: Requirements 6.3.1**
  - [ ] 5.1.2 测试Property 6.3.2: 数据完整性
    **Validates: Requirements 6.3.2**

## 6. 后端开发 - 盈亏计算测试

- [ ] 6.1 编写Property-Based Tests（盈亏计算）
  - [ ] 6.1.1 测试Property 6.2.1: 盈亏守恒
    **Validates: Requirements 6.2.1**
  - [ ] 6.1.2 测试Property 6.2.2: 到期日盈亏
    **Validates: Requirements 6.2.2**
  - [ ] 6.1.3 测试Property 6.2.3: 卖出持仓符号
    **Validates: Requirements 6.2.3**

## 7. 前端开发 - 基础设施

- [ ] 7.1 创建项目结构
  - [ ] 7.1.1 创建components/目录结构
  - [ ] 7.1.2 创建services/目录（API客户端）
  - [ ] 7.1.3 创建context/目录（状态管理）
  - [ ] 7.1.4 创建utils/目录（工具函数）

- [ ] 7.2 实现API客户端
  - [ ] 7.2.1 创建axios实例配置
  - [ ] 7.2.2 实现positionService（持仓API调用）
  - [ ] 7.2.3 实现marketPriceService（价格API调用）
  - [ ] 7.2.4 实现pnlService（盈亏计算API调用）
  - [ ] 7.2.5 实现volatilityService（波动率API调用）

- [ ] 7.3 实现状态管理
  - [ ] 7.3.1 创建AppContext和reducer
  - [ ] 7.3.2 定义action types和action creators
  - [ ] 7.3.3 实现AppProvider组件

## 8. 前端开发 - 持仓管理UI

- [ ] 8.1 实现PositionForm组件
  - [ ] 8.1.1 创建表单布局和字段
  - [ ] 8.1.2 实现表单验证
  - [ ] 8.1.3 实现提交处理（创建/更新持仓）
  - [ ] 8.1.4 实现错误提示

- [ ] 8.2 实现PositionList组件
  - [ ] 8.2.1 创建持仓列表表格
  - [ ] 8.2.2 实现编辑功能
  - [ ] 8.2.3 实现删除功能（带确认对话框）
  - [ ] 8.2.4 实现按标的代码筛选

- [ ] 8.3 实现PositionPanel组件
  - [ ] 8.3.1 整合PositionForm和PositionList
  - [ ] 8.3.2 实现加载状态显示
  - [ ] 8.3.3 实现空状态提示

## 9. 前端开发 - 控制面板UI

- [ ] 9.1 实现PriceInput组件
  - [ ] 9.1.1 创建价格输入框
  - [ ] 9.1.2 实现标的代码选择器
  - [ ] 9.1.3 实现价格更新提交
  - [ ] 9.1.4 实现防抖优化

- [ ] 9.2 实现VolatilityInput组件
  - [ ] 9.2.1 创建波动率输入框（百分比格式）
  - [ ] 9.2.2 实现波动率情景选择器
  - [ ] 9.2.3 实现自定义波动率输入
  - [ ] 9.2.4 实现波动率情景保存功能

- [ ] 9.3 实现ControlPanel组件
  - [ ] 9.3.1 整合PriceInput和VolatilityInput
  - [ ] 9.3.2 添加"计算盈亏"按钮
  - [ ] 9.3.3 实现计算触发逻辑

## 10. 前端开发 - 分析面板UI

- [ ] 10.1 实现PnLChart组件
  - [ ] 10.1.1 配置Recharts LineChart
  - [ ] 10.1.2 绘制盈亏曲线
  - [ ] 10.1.3 添加当前价格参考线
  - [ ] 10.1.4 添加最大亏损点标记
  - [ ] 10.1.5 实现Tooltip显示详细信息
  - [ ] 10.1.6 实现响应式布局

- [ ] 10.2 实现PnLTable组件
  - [ ] 10.2.1 创建盈亏数据表格
  - [ ] 10.2.2 实现价格和盈亏列
  - [ ] 10.2.3 实现条件格式（盈利绿色，亏损红色）
  - [ ] 10.2.4 实现分页或虚拟滚动

- [ ] 10.3 实现RiskMetrics组件
  - [ ] 10.3.1 创建指标卡片布局
  - [ ] 10.3.2 显示当前盈亏
  - [ ] 10.3.3 显示最大潜在亏损及对应价格
  - [ ] 10.3.4 显示最大潜在盈利及对应价格
  - [ ] 10.3.5 实现风险警告提示

- [ ] 10.4 实现AnalysisPanel组件
  - [ ] 10.4.1 整合PnLChart、PnLTable和RiskMetrics
  - [ ] 10.4.2 实现布局切换（图表/表格/混合视图）
  - [ ] 10.4.3 实现加载状态和空状态

## 11. 前端开发 - 主布局和导航

- [ ] 11.1 实现Header组件
  - [ ] 11.1.1 创建顶部导航栏
  - [ ] 11.1.2 添加应用标题和Logo
  - [ ] 11.1.3 添加快捷操作按钮

- [ ] 11.2 实现MainLayout组件
  - [ ] 11.2.1 创建响应式布局
  - [ ] 11.2.2 整合所有主要面板
  - [ ] 11.2.3 实现面板折叠/展开功能

- [ ] 11.3 实现App组件
  - [ ] 11.3.1 整合AppProvider和MainLayout
  - [ ] 11.3.2 实现全局错误边界
  - [ ] 11.3.3 添加全局加载指示器

## 12. 前端测试

- [ ] 12.1 编写组件单元测试
  - [ ] 12.1.1 测试PositionForm组件
  - [ ] 12.1.2 测试PositionList组件
  - [ ] 12.1.3 测试PnLChart组件
  - [ ] 12.1.4 测试RiskMetrics组件

- [ ] 12.2 编写集成测试
  - [ ] 12.2.1 测试持仓添加到盈亏计算的完整流程
  - [ ] 12.2.2 测试价格和波动率变化触发更新
  - [ ] 12.2.3 测试错误处理流程

- [ ] 12.3* 编写Property-Based Tests（前端）
  - [ ] 12.3.1* 测试状态管理reducer的不变性
  - [ ] 12.3.2* 测试API客户端的请求格式

## 13. 性能优化

- [ ] 13.1 后端性能优化
  - [ ] 13.1.1 实现计算结果缓存
  - [ ] 13.1.2 优化数据库查询（添加索引）
  - [ ] 13.1.3 使用NumPy向量化计算

- [ ] 13.2 前端性能优化
  - [ ] 13.2.1 使用React.memo优化组件渲染
  - [ ] 13.2.2 实现输入防抖
  - [ ] 13.2.3 优化图表数据采样
  - [ ] 13.2.4 实现虚拟滚动（如果数据量大）

## 14. 文档和部署

- [ ] 14.1 编写文档
  - [ ] 14.1.1 编写README.md（项目介绍和快速开始）
  - [ ] 14.1.2 编写API文档
  - [ ] 14.1.3 编写用户使用指南

- [ ] 14.2 部署准备
  - [ ] 14.2.1 创建Docker配置文件
  - [ ] 14.2.2 配置环境变量管理
  - [ ] 14.2.3 创建生产环境构建脚本

- [ ] 14.3 测试部署
  - [ ] 14.3.1 本地Docker测试
  - [ ] 14.3.2 端到端测试
  - [ ] 14.3.3 性能测试

## 15. 扩展功能（未来版本）

- [ ] 15.1* 实时数据API集成
  - [ ] 15.1.1* 设计MarketDataProvider接口
  - [ ] 15.1.2* 实现Yahoo Finance Provider
  - [ ] 15.1.3* 实现自动价格更新

- [ ] 15.2* Greeks指标
  - [ ] 15.2.1* 实现Greeks计算函数
  - [ ] 15.2.2* 添加Greeks显示UI
  - [ ] 15.2.3* 添加Greeks图表

- [ ] 15.3* 多用户支持
  - [ ] 15.3.1* 实现用户认证
  - [ ] 15.3.2* 实现数据隔离
  - [ ] 15.3.3* 实现用户管理UI

## 任务说明

- `[ ]` = 未开始
- `[-]` = 进行中
- `[x]` = 已完成
- `*` = 可选任务（未来版本）

## 优先级建议

**Phase 1 - MVP（最小可行产品）：**
- 任务 1-6：后端核心功能
- 任务 7-11：前端核心功能
- 关键路径：数据库 → 定价引擎 → API → 前端UI

**Phase 2 - 完善：**
- 任务 12：测试
- 任务 13：性能优化
- 任务 14：文档和部署

**Phase 3 - 扩展（未来）：**
- 任务 15：扩展功能

## 估算工作量

- Phase 1 (MVP)：约 40-50 小时
- Phase 2 (完善)：约 15-20 小时
- Phase 3 (扩展)：待定

建议按照任务编号顺序执行，确保每个阶段完成后进行测试验证。
