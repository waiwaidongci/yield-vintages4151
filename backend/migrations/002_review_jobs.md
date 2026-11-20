# 002 良率回查

新增表 `yield_review_jobs`，由 SQLAlchemy `Base.metadata.create_all` 在启动与 worker 启动时自动创建。

| 列 | 含义 |
|---|---|
| id | 主键 |
| name | 回查任务名 |
| status | queued / running / completed / failed / rejected |
| department / policy_id / date_from / date_to / penalty | 发起时选择的试制线、口径、日期窗口与切点罚分（罚分发起时填写，后续不可改） |
| request_key | 同选择条件的 SHA256，唯一索引；重试同一回查返回同一任务，不重复给结论 |
| checksum | 受理时底稿+口径快照的 SHA256，回查按受理时的底稿和口径执行 |
| snapshot | 冻结的来源记录与口径 JSON |
| result | 分段结论：winner/cut/cut_day/left/right 各班次分子分母与良率、failure_reason、best_effort |
| checkpoint | 求解断点（已扫描切点、当前最优、罚分、计数），中断后恢复续算 |
| error / created / updated / created_by | 失败原因与审计字段 |

状态语义：`rejected` 表示 30 秒预算内未枚举完全部切点、最优性未证明，拒绝发布结论但保留检查点，可在 `/reviews/{id}/resume` 续算；`failed` 为数据异常。普通核算表 `yield_runs` 与回查互不影响，worker 分别消费 `report-jobs` 与 `review-jobs` 两个 Redis 队列。

已有数据库升级：先备份；若未开启自动建表，手动按 `src/models/review.py` 的列定义建表，唯一索引建在 `request_key`。
