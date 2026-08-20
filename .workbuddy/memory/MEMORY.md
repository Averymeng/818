# 诊断台项目 · 长期笔记

## 项目定位
小红书投流周报智能体「诊断台」（GitHub: Averymeng/818，分支 main）。输入：客户一周投放数据，输出：八章节诊断报告（整体结论 / Top3 异动 / 拆解 / 案例 / 建议 / 行动 / 观察清单 / 数据质量）。

## 工作流铁律（2026-08-19 用户明确，部分已更新）
- **每完成一项任务（无论大小），必须 git 提交（本地，必要时 push 远端）**。
- **每个生成物都要入库**：设计 demo、脚本、文档等产出物一律 commit，不留未跟踪文件。
- **仓库整洁 / 文件夹条理**：不要散落文件污染根目录；我的新增产物统一归类（如前端 demo 放 `诊断台/demo/`）。运行时产物（`__pycache__/`、`*.db`、`reports/`、`*.log`）靠 `.gitignore` 屏蔽，不进源码库。
- **交接文档不再自动更新**（2026-08-19 用户指令，覆盖原"同步更新交接文档"项）；除非用户明确要求才改 `诊断台/交接文档_诊断台.html`。回复里也不反复提交接文档。
- **演示/选型阶段纪律（2026-08-19 用户强调「记住不要让我提醒」）**：①每次改完一处**立刻 git 提交当前版本**（含新 demo 等任何产物），绝不被动等用户催；②**不确定的视觉/交互方案，必须分开多做几个 demo 让用户选（一 demo 一想法）**，不要只做一种闷头猜；③**每次只改用户点名的地方，其余一律不动**（核心原则，曾因越界改无关页面被严肃批评）。

## 当前里程碑状态（v2 首轮，2026-08-19 提交 7296e82，已 push）
- 三层评测体系首轮达标：硬断言 **126/126** 通过（含 E24 横切 75 条）；端到端 B（judge 三档）13 份全产出、无中位分<2（D1 2.62/D2 2.77/D3 2.77）；MVP 门槛方案 A（24 中 23 必过，E18 例外）达成。
- 唯一报告修复：E14 误报（评测脚本扫了 judge_calibration.json 的 ROI/GMV 文本），已改 run_eval.py 扫描守卫。
- v2 报告：`诊断台/eval/eval_report_v2.md`；评测集设计稿：`诊断台/eval/评测集设计稿_v2.md`（24 case）。

## 关键架构约定（勿随意改动）
- `agent/system_prompt.md` 第 7 节 = 字段级红线（周值/日值、命名口径、后链路禁词、正常周禁派活、owner 删除、日期锚定生成日、因果推断标注、持平措辞）——只能加、不能删，是反复试错沉淀。
- P0=急需处理(当天止损) / P1=本周完成(优化)；正常周(P0/P1都不出)；观察清单=观察非行动(无 P2)；枕水类=主动停投(非缺数据)。
- 评测三层：L1–L8 业务链路落库断言 / 端到端 A 报告硬断言 / 端到端 B judge 三档(1/2/3，3票中位数)。
- API key：取 `~/.zshrc` 最后一行真 key（3 行，前两行是占位符）。

## 用户偏好：节省模型积分（2026-08-20 明确「以后都这样」）
- 验证/检查类任务**默认走零成本路径**：跑 `python3 eval/run_eval.py`（默认零成本模式，122 硬断言）+ `python3 eval/ci_check.py`（横切扫描），再用 curl 核验 `/api/customers` `/api/daily` `/api/base` 等 JSON 接口，必要时对前端做 dry_run；**不要一上来就真跑 LLM 生成**。
- 模型选择：积分有限时优先用 Hy3（若限时免费 0.00×），否则选 MiniMax-M3（0.25×），避开 Kimi-K3（1.62×）；报告类真实生成仅在用户明确要验收内容时才触发。
- 用户明确「先回答/先改好不要执行」的回合，遵守不打实际命令、不改文件；确认后再动手。

## 本地开发服务器持久化说明（2026-08-20）
- `诊断台/api_server.py` 是本地开发服务器（`http://127.0.0.1:8000`），**不是常驻后台服务**。`run_in_background` 启动的任务会在后台运行，但可能被环境回收，导致一段时间后浏览器出现 `ERR_CONNECTION_REFUSED`。
- 浏览器若走代理，访问 `127.0.0.1` 也可能返回 502；可尝试在代理设置中添加 bypass `127.0.0.1, localhost`，或直接用无代理 curl 验证：
  `curl --noproxy '*' http://127.0.0.1:8000/`
- 手动重启命令：`cd /Users/zerzerrr/WorkBuddy/2026-08-18-14-34-17/诊断台 && python3 api_server.py`
- 长期稳定方案：将 Flask 后端部署到 Python 运行环境的主机，而不是依赖本地 `http.server`。

## 非阻塞待办 / 下一阶段（2026-08-19 重排）
- CI 自动回归 **已完成**（ci_check.py + GitHub Actions，替代原 A/B 手动门槛；`git log` 见 df56310）。
- **Phase 1 = 参考案例库(RAG) + 独立 badcase 库 = 已完成（2026-08-19 交付）**：`search_cases` 工具(code/agent/tools.py) 原本已实现（按行业/赛道/品类/signature 召回 diag_case，自动过滤 badcase），本次把 `diag_case` 净化为纯 referenceable=1 参考案例（eval/seed_cases.py 移除 badcase 行）；**新建 `diag_badcase` 表**(db/schema.sql) 物理独立，`data/seed_badcase.py` 把评测期 3 缺陷(周值日值混淆/依据不可核验/观察清单漏项)首批入库。注：原 system_prompt 用 referenceable=0 逻辑隔离，用户要的是物理分表，现已达成。
- **Phase 2 数据层 = 定时刷新 + 用户手动录入**：②手动录入后端**已完成**(2026-08-19 交付 `data/ingest.py`)：JSON/CSV 录入客户+plan/note/daily_metric，**字段不变只增行**，daily_metric 标 `source='upload'`(schema 已有该列，无需加字段)，报告不区分来源；①自动端**每天8点定时重灌未做**(无 scheduler 代码，待 Phase 2 收尾)；录入入口 UI 归 Phase 3 前端。
- **Phase 3 前端页面 = 已实现（待验收，2026-08-19 交付）**：零依赖后端 `诊断台/api_server.py`（标准库 http.server，托管前端 + JSON API：customers/weeks/report/compare/cases/ingest）接现有 SQLite / tools / orchestrator / ingest；前端 `诊断台/web/`（index.html/app.js/style.css）三模块——①数据总览（组合 KPI + 掉量/增量归因 + 客户监控）②周度复盘（客户卡片网格 → 八章节报告；改参数重算滑块 metric/spend 阈值；对比多周勾选；复制/新开报告链接）③手动录入（JSON 编辑器接 ingest 后端，source='upload' 落库）。前端范围=用户已定**交互式**（非纯展示）；RAG=用户已定**维持 SQL 字段匹配**，不做向量检索。`orchestrator.run()` 新增可选参数(metric_threshold/spend_threshold/cur_start 等)向后兼容，评测 122/122 硬断言仍通过（零成本模式）。
- **Phase 4 上线 + badcase 闭环**：未开始。
- 整体项目进度 plan 见 `诊断台/项目进度plan.html`。
