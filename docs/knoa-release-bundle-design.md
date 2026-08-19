# Knoa 签名 Release Bundle 与更新设计

> 状态：Phase 1 代码闭环已实现，待干净 VM 发布验收
>
> 日期：2026-08-19
>
> 范围：Hub、Node、`all` 协调安装，以及可选 Agent Runtime Extension

## 1. 结论

终端用户安装和更新不再依赖 Git、系统 Python、pip 或手工 venv。CI/发布机生成自包含 ZIP：

```text
knoa-<role>-<version>-<os>-<arch>.zip
├── release-manifest.json       # Ed25519 signed
├── runtime/                    # embedded supported Python Runtime
├── app/                        # installed Knoa application + dependencies
├── bin/                        # role launchers / health command
├── console/                    # corresponding embedded Console assets
└── metadata/                   # optional license/build provenance
```

Windows、Linux 和 Runtime Extension 使用同一 ZIP 容器。Linux executable 位由签名 Manifest 表达，解包后
只按已验证 Manifest 恢复，不信任 ZIP 自身的 mode。统一格式减少 path traversal、symlink、archive bomb、
staging 与回滚代码。

## 2. Release 类型不是部署角色

```text
release_kind=product
  role=hub | node | all

release_kind=runtime_extension
  role=null
  extension={extension_id, runtime_kind, publisher, entrypoint, ceiling}
```

Runtime Extension 是 Node 内部受管 Worker 的交付物，不是第四种服务角色。安装 Extension 后仍只有一个
`node_host` 长期服务；Node Host 启动、监管和停止扩展 Worker。

## 3. Agent 的默认与扩展路径

| 用户意图 | 产品对象 | 是否安装代码 |
| --- | --- | --- |
| 使用默认小诺 | `NodeAgent(knoa)` | 否，Node Bundle 内置 |
| 启用自动审批 Reviewer | `NodeAgent(reviewer_agent)` | 否，与 Knoa Runtime 共用 Worker |
| 启用 Codex | `NodeAgent(codex)` | 否，内置 Adapter；外部 Codex 可用性单独 preflight |
| 新建销售/运维/研究 Agent | 新增 `NodeAgent(kind=knoa)` | 否，只发布 Node 配置 |
| 接入全新执行循环 | `NodeAgent` 引用已安装 runtime kind | 是，目标 Node 导入 Runtime Extension |

产品不重新引入 `AgentProfile`、`AgentDefinition` 或 Workspace Agent sharing。

## 4. 签名与信任域

Manifest 对以下内容签名：

- release ID、版本、类型、role、OS/arch；
- Agent Runtime SPI 兼容范围；
- 每个 artifact 的相对路径、kind、size、SHA-256 和 executable 语义；
- Runtime Extension 的 publisher、runtime kind、entrypoint 与 native capability ceiling。

Trust Store 不是“可信 key 列表”这么简单。每个 key 必须声明：

```text
key_id
public_key
allowed_release_kinds
allowed_extension_ids
```

因此第三方 Extension key 不能签 Knoa 产品更新，也不能越权签另一个 extension ID。产品 root key 默认只允许
`release_kind=product`。Trust Store 更新本身必须随已信任产品 Release 或显式本机管理员操作完成。

## 5. 安全解包

Updater 在私有 incoming staging 中执行：

1. 限制文件数量、单文件大小和总展开大小；
2. 拒绝绝对路径、`..`、Windows drive/反斜线、重复路径、加密 entry 和 symlink；
3. 解包为不可执行的 staging 文件；
4. 解析并验证 Manifest 签名及 key trust domain；
5. 要求实际文件 inventory 与 Manifest 完全相等；
6. 流式复算 size 与 SHA-256；
7. 校验 release kind、role、OS/arch 与 Agent Runtime SPI；
8. 只按 Manifest 恢复 Linux executable 位。

任何一步失败都不能改变 active version pointer。

## 6. 原子激活与回退

```text
InstallRoot/
├── versions/<release_id>/
├── state.json              # small atomic pointer
└── .incoming.*/            # bounded temporary staging
```

激活顺序：

```text
verify -> stage immutable version -> atomically switch pointer
       -> start candidate / health check
       -> success: retain previous as rollback target
       -> failure: atomically restore previous pointer and service
```

Updater 不写 Hub/Node 领域数据库。不可逆 schema migration 必须先备份并设置 rollback cutoff，不能通过二进制
pointer 假装可降级。

## 7. 构建职责

`scripts/build_release_bundle.py` 只接受已经物化的 payload：

- 发布机负责提供目标平台的受支持嵌入式 Python Runtime；
- application 目录已安装 Knoa 与锁定依赖，不在用户机器执行 pip；
- launcher/health command 属于对应 role；
- Console 资产在 Phase 2 后随宿主 Bundle 一起构建；
- signing private key 只存在于受保护发布环境，不进入仓库或 Bundle。

Python 依赖由根目录 `uv.lock` 固定；CI 只面向产品支持的 Windows/Linux 解算，并以 `uv sync --locked` 验证。
`scripts/materialize_release_payload.py` 将目标平台的嵌入式 Runtime 与已安装 application tree 组合成 role-specific
payload，自动生成 `knoa-hub`、`knoa-node` 和 `knoa-health` 启动器。`scripts/build_product_release.py` 再完成
签名与确定性 ZIP，版本默认从 Knoa Platform 版本源读取，发布者不手填版本号。

`knoa-health` 是候选 Bundle 的离线自检，验证 Hub import 或 Node Config/ManagedConfig/Agent SPI/Gateway schema；
服务切换后仍必须检查 Hub `/health` 和 Node Gateway `/health`，不能用离线自检替代真实进程健康。

当前代码已实现 Manifest/schema、Ed25519 签名、key trust domain、安全 inventory 校验、确定性 ZIP、安全解包、
Python 原子版本 pointer/health callback/自动回退内核，以及不依赖系统 Python 的 Rust native updater。Python
与 Rust 共同验证同一份 Python 生成签名 fixture；fixture 强制按 binary checkout，避免 Windows CRLF 改写已签名
artifact。native updater 提供 `install/current/run/rollback/reject`，Windows WinSW 和 Linux systemd 都通过稳定
`run` 入口解析活动版本，不硬编码版本目录。

产品 payload 已包含平台安装资产；Windows Bundle 必须嵌入 WinSW，Linux Bundle 包含 systemd unit。两个安装器
均在离线候选健康检查后启动真实 Hub/Node，并检查 `/health`。真实健康失败时 `reject` 恢复 previous；首次安装
失败则清空 active pointer。详细目录和命令见 [产品 Bundle 部署与更新](./knoa-product-bundle-deployment.md)。

Phase 1 剩余发布门不是继续增加另一套安装逻辑，而是用正式嵌入式 Runtime、代码签名 bootstrap 和六种
OS/role 干净 VM 组合完成安装、更新、回退和卸载验收。

## 8. 不变量

1. 无签名 Release 不可安装；
2. key trust domain 不能跨 product/extension；
3. 错误 OS/arch/role 不可激活；
4. Bundle 不允许未声明文件或 symlink；
5. active pointer 只在完整验证后切换；
6. health 失败恢复上一版本；
7. HubRoot、NodeRoot、Secret、APK 与 Workspace 数据不在版本目录；
8. Runtime Extension 不获得 Node identity、Hub enrollment、Console 或公网入口。
