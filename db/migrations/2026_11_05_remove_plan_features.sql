-- db/migrations/2026_11_05_remove_plan_features.sql
--
-- 下线「交易计划」与「持仓组合」功能，清空相关数据，**保留表结构**。
--
-- 为什么用迁移而不是手工 DELETE：
--   谱系库（CI 的 hs300.db）也会执行这个迁移一次（_applied_migrations 记账），
--   否则 CI restore 回来的旧库会一直带着这些行，删了也白删。
--
-- 为什么保留表结构：
--   用户要求「保留表结构」，迁移回滚 / 将来恢复功能都只需要改代码，
--   不必重建表。表为空对读路径无影响（各查询本来就是「有行才显示」）。
--
-- ⚠️ 只清「计划/持仓」这三张表。**不动**：
--   - daily_prices / daily_picks（行情与选股，站点还在用）
--   - pick_outcomes（选股结果追踪，/api/stats 还在用）
--   - cb_*（可转债功能还在）
--
-- 顺序：先 trade_events（引用 open_positions 的事件），再 open_positions，再 trade_plan。
DELETE FROM trade_events;
DELETE FROM open_positions;
DELETE FROM trade_plan;
