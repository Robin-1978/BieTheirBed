# Knoa 跨平台 Runtime Phase 0 基线

> 状态：Phase 0 可执行基线
>
> 日期：2026-08-19
>
> 目标：在 Rust/Python 进程拆分和 repository writer 切换前，固定当前可运行合同与数据形状

## 1. 当前验证结果

使用仓库声明的 Python 3.12 虚拟环境运行：

```text
.venv/bin/pytest -q
909 passed, 1 warning
```

系统 Python 或未安装项目依赖的解释器不构成有效基线。CI 与本地证据必须记录实际 Runtime 版本，避免把环境
收集失败误判为产品回归。

## 2. 已冻结合同

| 合同 | 权威资产 | 检查方式 |
| --- | --- | --- |
| Agent Host/Worker IPC v1 | `protocol/knoa/agent/runtime/v1/agent_runtime.proto` | Protobuf descriptor digest + textproto encode/decode |
| Runtime IPC descriptor | `descriptor.sha256` | `scripts/check_protocol_contracts.py` |
| Gateway API | Pydantic OpenAPI exporter | runtime baseline SHA-256 |
| ManagedConfig v2 | Pydantic schema | runtime baseline SHA-256 |
| Agent Runtime SPI | `knoa_agent_contracts` schemas | runtime baseline SHA-256 |
| App/Node Relay transcript | session/pairing hello transcript | checked JSON fixture |
| Node/Node resource transcript | resource hello transcript | checked JSON fixture |
| SQLite writer schemas | Node authority、Gateway、Self-hosted Hub、Hosted control | normalized `sqlite_master` fixture |
| Release role | `deploy/release/roles.json` | Windows/Linux `hub/node/all` CI matrix |
| Relay performance | `protocol/baseline/relay-performance-v1.json` | in-process broker budget check |

当前 Agent Runtime descriptor：

```text
sha256:b53042d4c13b7463e03ac133349024a9940da5d37bb1c629a0160bf0b06079a2
```

## 3. Release Role 不变量

```text
hub  -> service: hub
        embedded UI: Hub Console

node -> service: node_host
        managed worker: agent_runtime
        embedded UI: Node Console

all  -> services: hub + node_host
        managed worker: agent_runtime
        embedded UI: Hub Console + Node Console
```

Console 和 Agent Runtime 不得出现在 `services` 集合。Windows/Linux 使用同一个 role contract，安装器只实现
平台差异，不重新定义产品角色。

## 4. 基线更新规则

合同变化不能直接覆盖 fixture：

1. 说明变化是兼容扩展、显式迁移还是破坏性新版本；
2. 对数据库变化定义 writer gate、binary/data rollback 与 rollback cutoff；
3. 对协议变化保留字段编号，不复用已发布编号；
4. 运行对应 `--update` 命令；
5. 审查生成 diff，确认不包含 Secret、机器路径或生产身份；
6. Windows/Linux CI 通过后才能切换实现。

更新命令：

```text
.venv/bin/python scripts/check_protocol_contracts.py --update
.venv/bin/python scripts/capture_runtime_baseline.py --update
```

## 5. Relay 性能预算

`scripts/benchmark_relay.py --check` 固定 RelayBroker 自身的首帧、持续转发、长会话和 Node 重连开销。
它排除公网 RTT、TLS、Cloudflare 和具体机器网络，因此是实现回归门，不冒充生产网络容量结论。ICE、真实 WSS 和
跨地域指标在 Phase 5 的 transport E2E 中单独记录。

## 6. 尚未由 Phase 0 宣称完成的事项

- 当前 fixture 是迁移输入，不代表 Rust repository 已实现；
- CI role matrix 验证角色合同，不代表 Release Bundle 已可安装；
- ICE/STUN、streaming Relay 和 Desktop Companion 仍属于后续 Phase；
- 真实 Relay 长连接环境仍需在 Phase 5 补充公网首字节、吞吐、背压和重连数据。
