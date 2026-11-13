-- 2026-11-12 晚：IMTask 独立库物理隔离（用户拍板：独立 im_task 库、原表名无前缀，
-- 知识库机器人独占 qsm 库互不干扰）。
-- ⛔ 红线：本脚本只新建 im_task 库并赋权，绝不触碰 qsm 库及其它任何既有库表。
-- ⚠️ 执行位置（2026-11-12 实测）：MySQL 实例监听公网 EIP 转发的 28619 端口
--   （文档保留网段示例：203.0.113.10:28619；真实地址见内部部署记录，不进版本库），
--   与 IMTask 部署机（SSH 20401）**不是同一台机器**——在部署机上找不到 28619 监听，
--   qulity-mysql-1 容器（quality 项目）不是目标实例，勿在其上执行。
--   待用户拿到该实例的 MySQL root 权限后执行（或提供实例机 SSH）：
--   mysql -uroot -p < create_imtask_db.sql
CREATE DATABASE IF NOT EXISTS im_task DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
GRANT ALL PRIVILEGES ON im_task.* TO 'qsm'@'%';
FLUSH PRIVILEGES;
SHOW DATABASES;
SHOW GRANTS FOR 'qsm'@'%';
