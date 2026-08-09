# MVP 1 设计独立审查记录

> Reviewer：Claude Code 2.1.224 / Sonnet
>
> 日期：2026-08-09
>
> 审查方式：只读，`--effort medium`，无文件修改权限
>
> 审查对象：提交 `7b6a547` 中的 MVP 1 设计规范与 Dispatch

## 第一轮结论

`MODIFY`

方向合理、范围收敛、安全模型思路正确，但只读边界尚不能在设计层证明，补齐核心问题后才能进入实施计划。

## Findings

1. **WAL + `mode=ro` 语义不完整**
   - 影响：`-wal` / `-shm` 的访问条件和锁协调未定义，可能运行失败或让“无写入”验收失真。
   - 处理：明确不使用 `immutable=1`，定义 sidecar 校验、每请求独立只读快照、`-shm` 锁区边界和失败码。
2. **Git 命令没有白名单**
   - 影响：`git status` 可能刷新 index，违反请求前后 Git 不变承诺。
   - 处理：定义精确 argv，设置 `GIT_OPTIONAL_LOCKS=0`，禁用 fsmonitor/auto maintenance，并验证 index hash/mtime。
3. **API 缺字段级 schema**
   - 影响：前后端契约与敏感字段白名单无法测试。
   - 处理：为七个端点定义 `data` 字段、子对象白名单和事件 payload 脱敏规则。
4. **SQLite 并发模型未说明**
   - 影响：跨线程复用连接可能失败或产生不一致快照。
   - 处理：`ThreadingHTTPServer` 每请求独立连接、单只读事务、2 秒 timeout、finally close。
5. **列表无上限**
   - 影响：历史增长后响应和内存不可控。
   - 处理：加入 `limit/offset` 默认值、硬上限和 `has_more`。
6. **缺少无 Origin 规则**
   - 影响：同源导航、curl 和预检行为存在歧义。
   - 处理：Host 有效时允许无 Origin 的 GET/HEAD；OPTIONS 必须精确匹配 Origin。
7. **静态文件映射不具体**
   - 影响：路径穿越控制点不可审查。
   - 处理：仅映射四个固定 URL，其他路径与危险编码全部拒绝。

## Codex 处置

七项均接受并写入设计规范。第 1、2 项属于进入实施计划前的必修项，第 3–7 项同时在设计层固定，避免把关键契约推迟到编码时猜测。修订完成后请求 Claude 做短复核。
