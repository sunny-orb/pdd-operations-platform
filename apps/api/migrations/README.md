# 旧迁移历史

`versions/` 的四个 `20261023_*` revision 保留原文，用于追溯已退役的旧 M1 数据库；不再提供默认 Alembic 配置、运行环境或应用元数据。这些文件不属于当前升级链，不改写已执行 revision，也不恢复旧表。

现行迁移在 `../migrations_v2/`，使用 `../alembic-v2.ini`，在线执行只接受显式受控连接。设计、升级与恢复边界见[迁移方案](../../../docs/database-schema/migration-plan.md)，操作入口见[开发与运行](../../../docs/development.md)。
