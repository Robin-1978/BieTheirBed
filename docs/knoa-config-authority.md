# Knoa 配置权威说明：YAML 与数据库控制面

> 状态：权威说明
> 更新：2026-09-02

## 1. 为什么"两套配置"

Knoa Node 的配置分两个层次，各有职责、各有权威来源。用户经常困惑"YAML 又改又不生效"，
根因是：**运行时行为以数据库控制面为准，YAML 只是首次安装的播种种子**。

| 层次 | 来源 | 管什么 | 生效方式 |
| --- | --- | --- | --- |
| 基础设施（Infrastructure） | `config/default.yaml` + `~/.knoa/config/local.yaml` + `--config` + 环境变量，启动时 `load_config()` 读取 | 端口、Gateway、飞书/钉钉、service token、trace/log、attachment、remote unlock、capability MCP 等 | 每次启动重新读取，**改动即生效（需重启）** |
| 运行时行为（Managed） | `assistant.db` 的 `config_revisions` / `config_control_state`（配置控制面） | providers、models、default_model、agents（node_agents）、approval_review、mcp_servers、skills、operational 限制 | 首次启动用 YAML 播种；之后以控制面 applied revision 为准，通过 App / Node Console / CLI 发布 |

## 2. YAML 到底什么作用

`load_config()` 把 YAML 合并成 `AppConfig`；其中 Managed 相关字段通过
`config.managed_config()`（`src/knoa_platform/config.py`）转成 `ManagedConfig`，在
**首次初始化**时写入 `config_revisions` 作为种子（`composition.py` →
`ConfigRegistry.initialize()`）。

此后每次启动，`ConfigRegistry.initialize()` 发现 DB 里已有同 schema 版本的应用 revision
就直接返回现有版本，**不会用 YAML 覆盖**。因此：

- 新建/全新安装的 Node：YAML 决定初始模型、Agent、审批等默认值；
- 已初始化的 Node：改 `local.yaml` 的这些字段 **不生效**，必须以 App/Console/CLI 发布。

## 3. 怎么改才是对的

| 想改 | 正确方式 |
| --- | --- |
| 端口、频道密钥、service token | 编辑 `~/.knoa/config/local.yaml` 后重启 `knoa-node.service` |
| 模型 / Provider / Agent / 审批模式 / MCP / Skill | 在 App 或 Node Console 里修改并"发布"，会写入控制面 revision 并热生效/重启相应组件 |
| 想用 YAML 一键覆盖全部 Managed | 暂无官方命令；可用导入/导出路径或清空 `config_revisions`+`config_control_state` 后重启以重新播种（**会丢失控制面历史，谨慎**） |

## 4. 如何核对当前实际生效的配置

运行时用的是 `assistant.db` 控制面 applied revision，而不是 local.yaml：

```sql
-- 查看当前 applied revision
SELECT desired_revision_id, applied_revision_id, apply_status
FROM config_control_state WHERE singleton=1;

-- 查看其内容（JSON）
SELECT document_json FROM config_revisions
WHERE revision_id = (SELECT applied_revision_id FROM config_control_state WHERE singleton=1);
```

`approval_review.mode`、`default_model`、`node_agents`、`mcp_servers` 等都看这里。

## 5. 常见误区

- **"我改了 local.yaml 的 approval_review 为什么不生效？"**
  审批模式在控制面里。用 App 发布的 revision（例如 `change_summary = Automatically approve
  bounded medium-risk GitLab OOM retries`）才是生效的。
- **"reviewer 为什么不是 local_qwen？"**
  运行时 reviewer 模型来自 `reviewer_agent` 的 platform model binding（控制面），
  `approval_review.model` 字段只用于 AppConfig 校验，不决定运行时 reviewer。
- **"为什么 DB 里有的模型 local.yaml 里没有？"**
  控制面只以自己 applied revision 为准；local.yaml 只是播种源，两者允许不一致，
  一切以控制面为准。
