# 衡序 ERP

衡序 ERP 是面向商贸分销与简单装配的试点系统。项目根目录即当前检出的 `Hengxu-ERP`。工作台使用 React 与 TypeScript，业务 API 使用 FastAPI，验收数据库使用 PostgreSQL。

## 启动样板账套

复制 `.env.example` 为 `.env`，填写数据库密码和至少 12 位的 `ERP_DEMO_PASSWORD`。在项目根目录执行：

```powershell
docker compose up --build -d --wait
docker compose exec erp python -m backend.app.seed --period 2026-11
```

打开 [工作台](http://127.0.0.1:8000) 或 [API 文档](http://127.0.0.1:8000/docs)。账套编码为 `DEMO`，样板账号共享初始化时设置的样板密码。需要其他会计月份时，修改初始化命令中的 `--period`。

| 账号 | 岗位 |
| --- | --- |
| `admin` | 系统管理员 |
| `manager` | 业务主管，审批提交的单据 |
| `buyer` / `seller` | 采购员 / 销售员 |
| `warehouse` / `limited` | 仓管员 / 仅能访问中心仓的仓管员 |
| `finance` / `cashier` | 会计 / 出纳 |
| `planner` | 生产计划员 |

可先以 `finance` 填写期初批次。录入中心仓 P100 商品 10 件、成本金额 100 元，应收 CUS01 客户 50 元，应付 SUP01 供应商 20 元，期初权益 130 元。提交后切换 `manager` 批准，再由 `finance` 确认。到财务报表查看库存、应收、应付与总账的对账差额。

采购订单、销售订单、盘点、期初与工单需要独立账号审批。其他单据提交后可按岗位业务确认。收货、发货、发票、核销与工单操作在新建时选择有效的来源单据和行，在详情页可跳转上游来源和查看下游分配。

## 本地开发与回归

后端使用名为 `hengxu` 的 Conda 环境（Python 3.13.5），前端使用 Node.js 24。在项目根目录执行：

```powershell
conda create -n hengxu python=3.13.5 pip
conda activate hengxu
python -m pip install -r backend/requirements.txt
$env:DATABASE_URL = 'sqlite:///hengxu.sqlite3'
python -m backend.app.migrate
npm ci --prefix frontend
npm run build --prefix frontend
```

首次创建样板账套时，先设置 `$env:ERP_DEMO_PASSWORD`，再运行 `python -m backend.app.seed --period 2026-11`。已有账套可以直接用 `python -m uvicorn backend.app.main:app` 启动工作台。

也可分别运行 `python -m uvicorn backend.app.main:app --reload` 和 `npm run dev --prefix frontend`，从 Vite 输出的本地地址打开前端。SQLite 用于单机开发和快速回归。会计与并发验收使用 PostgreSQL。

```powershell
conda activate hengxu
python -m pip install -r backend/requirements-test.txt
ruff check backend tests
ruff format --check backend tests
python -m pytest -q
npm run build --prefix frontend
```

将 `ERP_TEST_DATABASE_URL` 指向专用 PostgreSQL 测试库后执行同一组测试。测试会为每个场景创建并删除独立的临时模式。测试覆盖 V01–V08 的固定样本，包括采购销售分批履约、库存出库竞争、失败回滚、幂等、来源分配上限、工单差异、跨仓越权、同期间冲销和期初校验。

独立测试库可复跑 V09 性能和 V10 恢复样本：

```powershell
python -m tests.acceptance_load
python -m tests.acceptance_restore prepare v10-source-ids.json
# 备份测试库，将备份恢复到另一实例，并更新 ERP_TEST_DATABASE_URL
python -m tests.acceptance_restore verify v10-source-ids.json
```

V09 生成 10 万单据行和 50 万库存流水，用 30 个独立用户运行库存列表读取及收货确认，记录各自 P95。V10 的 `prepare` 需要空库，`verify` 会验证采购来源、凭证及装配结算，并在恢复后的库里提交新发货。执行恢复演练时记录备份时点、启动和验收所用时间。

本地验收使用 Windows、PostgreSQL 18.6 和合成数据。浏览器使用 Edge，Docker Compose 配置另行完成静态校验。

| 验证 | 本地结果 |
| --- | --- |
| V01–V08 | 8 项 PostgreSQL 回归通过，另覆盖关账与过账并发 |
| V09 | API 进程内各采样 90 次，库存读取 P95 为 0.241 秒，收货过账 P95 为 0.734 秒 |
| V10 | 在干净关停点取物理备份，恢复到独立实例并验证 V01、V05 与新发货，耗时 3.10 秒 |
| 浏览器 | 登录、采购制单、换岗审批、收货和库存流水下钻通过 |

## 样板规则与验收边界

- 人民币采用两位金额、六位基本数量，单位按物料基础单位。估值采用公司级移动加权平均，库存余额按仓库和物料记录。
- 企业计税、法定科目、期初和正式期间规则由样板财务签认。样板限制倒序补录、跨期冲销和关账重开，且按公司串行处理写命令。
- 已发货成品的成本差额按批次及发货来源分摊。样板要求成品首次完工前现存量为零，同一成品同一时点只有一个未结算工单。结算前允许发货，其他改变成品库存的动作需要先结算。领料报废保留来源，已领材料成本留在工单。
- 当前账套支持基础单位、档案、岗位、仓库、凭证、试算和子账对账。分离岗位、数据规模下的响应目标、备份恢复和会计口径的验收方式见 [可行性分析文档](开发文档/可行性分析文档.md)。
- 关闭制造可在初始化时使用 `--no-manufacturing`。同一个数据库可使用 `--company` 初始化独立样板账套。

