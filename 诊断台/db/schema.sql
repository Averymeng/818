-- ============================================================
-- 诊断台 · AI商业化销售复盘 Agent — 数据库 Schema V1
-- SQLite · snake_case · 所有诊断绑定 sim_version（可复现）
-- ============================================================

PRAGMA journal_mode = WAL;

-- ---------- 维度域（业务层级：行业→赛道→客户→品类） ----------
CREATE TABLE IF NOT EXISTS industry (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE            -- 教育/影像婚美/出行旅游/到综服务
);

CREATE TABLE IF NOT EXISTS sector (
    id          INTEGER PRIMARY KEY,
    industry_id INTEGER NOT NULL REFERENCES industry(id),
    name        TEXT NOT NULL            -- 赛道，如 AI培训
);

CREATE TABLE IF NOT EXISTS category (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE            -- 品类，如 AI编程开发
);

CREATE TABLE IF NOT EXISTS customer (
    id              INTEGER PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,          -- 编造自然名，如 悦颜美容SPA
    sector_id       INTEGER NOT NULL REFERENCES sector(id),
    optimize_target TEXT NOT NULL CHECK (optimize_target IN ('open','lead')),  -- 私信开口/留资
    target_cost     REAL NOT NULL,                 -- 目标优化成本（元）
    status          TEXT NOT NULL DEFAULT 'active',
    created_at      TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS customer_category (
    customer_id INTEGER NOT NULL REFERENCES customer(id),
    category_id INTEGER NOT NULL REFERENCES category(id),
    PRIMARY KEY (customer_id, category_id)
);

CREATE TABLE IF NOT EXISTS plan (
    id           INTEGER PRIMARY KEY,
    customer_id  INTEGER NOT NULL REFERENCES customer(id),
    category_id  INTEGER NOT NULL REFERENCES category(id),
    name         TEXT NOT NULL,
    placement    TEXT NOT NULL CHECK (placement IN ('feed','search')),  -- 版位：信息流/搜索（无视频内流）
    created_date TEXT NOT NULL,                   -- 计划创建日期（新投/在投由状态+创建日期派生）
    status       TEXT NOT NULL CHECK (status IN ('在投','停投')),
    daily_budget REAL NOT NULL,                   -- 计划日预算（元）
    stopped_date TEXT                             -- 停投日期（停投时有值）
);

CREATE TABLE IF NOT EXISTS note (
    id            INTEGER PRIMARY KEY,
    customer_id   INTEGER NOT NULL REFERENCES customer(id),
    category_id   INTEGER NOT NULL REFERENCES category(id),
    plan_id       INTEGER REFERENCES plan(id),
    title         TEXT NOT NULL,                  -- 素材仅保留：标题 + 素材形式
    material_form TEXT NOT NULL CHECK (material_form IN ('图文','视频')),
    created_date  TEXT NOT NULL,
    status        TEXT NOT NULL CHECK (status IN ('在投','停投')),
    stopped_date  TEXT
);

-- ---------- 事实域（粒度：日期-客户-品类-版位-计划-笔记） ----------
CREATE TABLE IF NOT EXISTS daily_metric (
    date          TEXT NOT NULL,
    customer_id   INTEGER NOT NULL REFERENCES customer(id),
    category_id   INTEGER NOT NULL REFERENCES category(id),
    placement     TEXT NOT NULL CHECK (placement IN ('feed','search')),
    plan_id       INTEGER NOT NULL REFERENCES plan(id),
    note_id       INTEGER NOT NULL REFERENCES note(id),
    spend         REAL NOT NULL,                  -- 消耗(元)
    impressions   INTEGER NOT NULL,               -- 曝光
    note_clicks   INTEGER NOT NULL,               -- 笔记点击量
    button_clicks INTEGER NOT NULL,               -- 按钮点击量
    open_msg      INTEGER NOT NULL,               -- 私信开口量
    lead_cnt      INTEGER NOT NULL,               -- 留资量
    source        TEXT NOT NULL DEFAULT 'sim' CHECK (source IN ('sim','upload')),  -- 模拟/测试用户上传
    sim_version   TEXT NOT NULL,                  -- 数据快照版本
    PRIMARY KEY (date, note_id, placement)
);
CREATE INDEX IF NOT EXISTS idx_dm_customer_date ON daily_metric(customer_id, date);
CREATE INDEX IF NOT EXISTS idx_dm_plan ON daily_metric(plan_id);

-- ---------- Agent 运行域（Tracing/复现） ----------
CREATE TABLE IF NOT EXISTS review_task (
    id           INTEGER PRIMARY KEY,
    customer_id  INTEGER NOT NULL REFERENCES customer(id),
    task_type    TEXT NOT NULL DEFAULT 'weekly',
    cur_start    TEXT NOT NULL,
    cur_end      TEXT NOT NULL,
    cmp_start    TEXT NOT NULL,                   -- 固定 = 上一自然周
    cmp_end      TEXT NOT NULL,
    trigger_type TEXT NOT NULL DEFAULT 'manual',  -- manual / from_monitor
    status       TEXT NOT NULL DEFAULT 'running', -- running/succeeded/failed
    sim_version  TEXT NOT NULL,
    total_cost   REAL,
    created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    finished_at  TEXT
);

CREATE TABLE IF NOT EXISTS agent_step (
    id             INTEGER PRIMARY KEY,
    task_id        INTEGER NOT NULL REFERENCES review_task(id),
    seq            INTEGER NOT NULL,
    name           TEXT NOT NULL,                 -- data_check/full_scan/layer_diagnosis/anomaly_rank/drill_down/case_retrieval/report_gen
    status         TEXT NOT NULL,                 -- running/done/failed/skipped
    input_summary  TEXT,
    output_summary TEXT
);

CREATE TABLE IF NOT EXISTS agent_tool_call (
    id           INTEGER PRIMARY KEY,
    task_id      INTEGER NOT NULL REFERENCES review_task(id),
    step_id      INTEGER REFERENCES agent_step(id),
    seq          INTEGER NOT NULL,
    tool_name    TEXT NOT NULL,
    params_json  TEXT NOT NULL,
    result_json  TEXT,
    status       TEXT NOT NULL,                   -- ok/error
    latency_ms   INTEGER,
    cost         REAL,                            -- token 成本（元）
    error_msg    TEXT
);

CREATE TABLE IF NOT EXISTS layer_diagnosis (
    id            INTEGER PRIMARY KEY,
    task_id       INTEGER NOT NULL REFERENCES review_task(id),
    layer         TEXT NOT NULL CHECK (layer IN ('placement','plan','note','funnel')),
    status        TEXT NOT NULL,                  -- 正常/轻微/显著/数据不足
    judgement     TEXT,
    evidence_json TEXT
);

CREATE TABLE IF NOT EXISTS anomaly (
    id                    INTEGER PRIMARY KEY,
    task_id               INTEGER NOT NULL REFERENCES review_task(id),
    direction             TEXT NOT NULL CHECK (direction IN ('positive','negative','neutral')), -- 指标变化正负号（免费信息）
    form                  TEXT,                   -- 可选提示：单指标/链路组合/指标冲突/结构变化/数据质量
    location              TEXT NOT NULL,          -- 漏斗环节 + 版位/计划/笔记（事件身份，非外加标签）
    weight_breakdown_json TEXT,                   -- 40/30/20/10 权重分明细
    impact_spend          REAL,
    impact_cost           REAL,
    magnitude             REAL,
    confidence            REAL,
    rank                  INTEGER,                 -- 权重排序即严重度：Top3=重点，其余=观察项
    is_top3               INTEGER NOT NULL DEFAULT 0,
    drill_status          TEXT,
    resolution_text       TEXT
);

CREATE TABLE IF NOT EXISTS hypothesis (
    id            INTEGER PRIMARY KEY,
    task_id       INTEGER NOT NULL REFERENCES review_task(id),
    anomaly_id    INTEGER REFERENCES anomaly(id),
    content       TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT '待验证', -- 待验证/已验证/不成立/数据不足
    evidence_json TEXT
);

-- ---------- 报告域 ----------
CREATE TABLE IF NOT EXISTS report (
    id             INTEGER PRIMARY KEY,
    task_id        INTEGER NOT NULL REFERENCES review_task(id),
    version        INTEGER NOT NULL DEFAULT 1,
    status         TEXT NOT NULL DEFAULT 'draft',  -- draft/reviewed
    schema_version TEXT NOT NULL,
    report_json    TEXT NOT NULL,                  -- 八章节结构化 JSON（HTML/Word 同源渲染）
    sim_version    TEXT NOT NULL,
    created_at     TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at     TEXT
);

CREATE TABLE IF NOT EXISTS report_review (
    id              INTEGER PRIMARY KEY,
    report_id       INTEGER NOT NULL REFERENCES report(id),
    section_key     TEXT,                          -- 预留逐条审核；全局审核时为 NULL
    action          TEXT NOT NULL CHECK (action IN ('confirm','edit','reject','insufficient')),
    content_before  TEXT,
    content_after   TEXT,
    reason          TEXT,
    reviewer        TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- ---------- 行动与回流域 ----------
CREATE TABLE IF NOT EXISTS action_item (
    id           INTEGER PRIMARY KEY,
    report_id    INTEGER NOT NULL REFERENCES report(id),
    task_id      INTEGER NOT NULL REFERENCES review_task(id),
    suggestion   TEXT NOT NULL,
    priority     TEXT,
    status       TEXT NOT NULL DEFAULT '待沟通',   -- 待沟通/已采纳/未采纳/执行中/已完成
    owner        TEXT,
    planned_date TEXT
);

CREATE TABLE IF NOT EXISTS backflow (
    id             INTEGER PRIMARY KEY,
    action_item_id INTEGER NOT NULL REFERENCES action_item(id),
    disposition    TEXT NOT NULL CHECK (disposition IN ('keep','modify','reject')),  -- 仅三项回流
    actual_action  TEXT,
    result_7d_json TEXT,                           -- {target_cost_delta, spend_delta, improved}
    recorded_at    TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- ---------- 案例与评测域 ----------
CREATE TABLE IF NOT EXISTS diag_case (
    id                 INTEGER PRIMARY KEY,
    source_report_id   INTEGER REFERENCES report(id),
    customer_id        INTEGER REFERENCES customer(id),
    industry_id        INTEGER REFERENCES industry(id),
    sector_id          INTEGER REFERENCES sector(id),
    category_id        INTEGER REFERENCES category(id),
    optimize_target    TEXT,
    anomaly_signature  TEXT,                       -- 如 "留资成本上涨+链路留资率下降"
    key_evidence_json  TEXT,
    action_taken       TEXT,
    result_after       TEXT,
    status             TEXT NOT NULL DEFAULT 'reference', -- reference/verified/benchmark/badcase
    referenceable      INTEGER NOT NULL DEFAULT 1, -- badcase 默认不可引用
    created_at         TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS case_ref_log (
    id               INTEGER PRIMARY KEY,
    task_id          INTEGER NOT NULL REFERENCES review_task(id),
    case_id          INTEGER NOT NULL REFERENCES diag_case(id),
    similarity_points TEXT,
    key_differences  TEXT,
    adopted          INTEGER
);

-- ---------- Badcase 缺陷库（与参考案例库 diag_case 物理分离） ----------
-- 用途：沉淀「踩坑→根因→修复」闭环，驱动 system_prompt 红线迭代与评测回归；
--       不进入 search_cases 检索范围（参考案例库 diag_case 才是 RAG 源）。
CREATE TABLE IF NOT EXISTS diag_badcase (
    id                 INTEGER PRIMARY KEY,
    source_report_id   INTEGER REFERENCES report(id),
    customer_id        INTEGER REFERENCES customer(id),
    title              TEXT NOT NULL,                 -- 一句话缺陷描述
    category           TEXT,                          -- 周值日值混淆/依据不可核验/观察清单漏项...
    error_output       TEXT,                          -- 错误输出 / 现象
    root_cause         TEXT,                          -- 根因
    red_line_fix       TEXT,                          -- 对应 system_prompt 红线 / 代码修复
    eval_case          TEXT,                          -- 关联评测用例（如 E24e）
    status             TEXT NOT NULL DEFAULT 'fixed', -- open / fixed / regressed
    created_at         TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS eval_case (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    scenario    TEXT NOT NULL,                     -- normal/single_anomaly/compound/conflict/data_missing/...
    sim_version TEXT NOT NULL,
    customer_id INTEGER REFERENCES customer(id),
    cur_start   TEXT, cur_end TEXT, cmp_start TEXT, cmp_end TEXT,
    expected_json TEXT
);

CREATE TABLE IF NOT EXISTS eval_run (
    id              INTEGER PRIMARY KEY,
    agent_version   TEXT NOT NULL,
    eval_set_version TEXT NOT NULL,
    scores_json     TEXT,
    passed          INTEGER,
    ran_at          TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
