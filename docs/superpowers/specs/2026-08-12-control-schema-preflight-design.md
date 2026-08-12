# 控制库 Schema 兼容性预检设计

任务：`20260812-007`

## 决策

为持久化控制库增加只读兼容性预检。非 `init` CLI 命令在构造服务前检查完整控制面表集；任一表不存在时返回 `SCHEMA_MIGRATION_REQUIRED`，消息只列出缺失表并指向 `init`，不暴露 SQL、堆栈或运行库内容。`init` 保持唯一迁移入口，并继续使用其已有的幂等、锁定和事务语义。

工作台在只读 snapshot 校验中将 `intents` 纳入依赖表；该表缺失时返回同一代码，而字段不匹配仍为既有 `SCHEMA_UNSUPPORTED`。这区分“可由已知加表迁移恢复”和“未知 schema 损坏/不兼容”。

## 备选方案

1. 自动在每次命令前运行 `init`：体验简单，但会把只读查询变成隐式写入，拒绝。
2. 仅捕获 SQLite 文本异常：对数据库版本和驱动信息脆弱，不能可靠地给出恢复路径，拒绝。
3. 统一只读预检 + 显式 `init`：不扩大写权限、可测试、与 fail-closed 协议一致，采用。

## 不变量

- 缺表时原业务命令不执行，stdout 为空，只输出单行 JSON 错误。
- `init` 不经过预检，允许创建缺失表；恢复前后不直接执行 SQL 修改。
- `SCHEMA_MIGRATION_REQUIRED` 只表示必需表缺失；已有表字段不兼容仍为 `SCHEMA_UNSUPPORTED`。
- 不新增后台迁移、浏览器写入、远程访问、Git 动作或审批消费。

## 验证

测试构造已初始化数据库后删除 `intents` 表：CLI `process-pending-intents` 返回新错误；执行 `init` 后命令返回空批次。工作台健康检查对同一缺表返回新错误，而缺列继续返回 `SCHEMA_UNSUPPORTED`。最后运行两套 Python 全量测试、差异检查、仓库健康与 Claude focused review。
