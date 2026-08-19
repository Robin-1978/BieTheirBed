# Knoa 产品 Bundle 部署与更新

> 状态：Phase 1 产品交付基线
>
> 日期：2026-08-19
>
> 适用平台：Windows x86_64/aarch64、Linux x86_64/aarch64

## 1. 结论

正式产品部署不再执行 `git clone`、系统 Python、pip、uv 或手工 venv。发布系统为每个 OS、架构和 role
生成一个签名、自包含 Bundle：

```text
knoa-<role>-<version>-<os>-<arch>.zip
├── release-manifest.json
├── runtime/                 # 目标平台嵌入式 Python Runtime
├── app/                     # 已按 uv.lock 物化的 Knoa 和依赖
├── bin/                     # Hub、Node、离线健康检查入口
├── install/                 # 该平台服务安装资产
└── service/WinSW.exe        # 仅 Windows
```

安装者只需要三个受控输入：产品 Bundle、产品 Trust Store、对应平台的原生 `knoa-update`。Windows 的
WinSW 已进入签名 Bundle，不要求用户另外下载；Linux 使用系统自带 systemd。

## 2. 数据与版本目录

### Windows

```text
C:\Program Files\Knoa\
├── bin\knoa-update.exe
└── releases\
    ├── state.json
    └── versions\<release_id>\

C:\ProgramData\Knoa\
├── HostedHub\              # Hub 数据库、APK、签名身份
├── Node\                   # Node identity、工作状态
├── Workspace\              # Node 工作目录
├── Config\                 # 非 Secret 配置
├── Secrets\                # Hub bootstrap/release publish Secret
├── Services\               # 两个独立 WinSW service
└── Logs\
```

### Linux

```text
/opt/knoa/
├── bin/knoa-update
└── releases/
    ├── state.json
    └── versions/<release_id>/

/var/lib/knoa/
├── hub/
├── node/
└── workspace/

/etc/knoa/
├── hub.env
├── node.yaml
└── secrets/
```

版本目录不可变。HubRoot、NodeRoot、Workspace、Secret、日志和 APK 均不随二进制版本切换。Linux 服务使用
专用 `knoa` 系统账户；Windows 服务使用受 ACL 保护的 LocalSystem WinSW service。桌面会话能力后续由登录
用户 Companion 提供，不让系统服务进入交互桌面。

## 3. Windows 安装或更新

从管理员 PowerShell 执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Install-KnoaBundle.ps1 `
  -Role node `
  -BundlePath .\knoa-node-0.3.0-windows-x86_64.zip `
  -TrustStorePath .\release-trust.json `
  -UpdaterPath .\knoa-update.exe
```

Hub 机器使用 `-Role hub`，同机 Hub + Node 使用 `-Role all`。同一命令既是首次安装命令，也是更新命令。
安装器会：

1. 验证签名、key trust domain、OS/arch/role、SPI、inventory、大小和 SHA-256；
2. 解包到不可变版本目录并运行 `knoa-health.cmd`；
3. 停止选定的 WinSW service；
4. 原子切换活动版本并更新稳定 updater/service wrapper；
5. 启动 Hub/Node，并检查 `9529/health`、`9531/health`；
6. 真实服务健康失败时恢复 previous；首次安装失败时清空活动指针。

Hub 与 Node 始终是两个 service：`KnoaHostedHub`、`KnoaNode`。安装 `all` 不会把它们合并为一个进程。
产品 role 是主机级安装合同：Hub-only 主机持续使用 `hub`，Node-only 主机持续使用 `node`，同机部署从首次安装
就使用 `all`。安装器拒绝原地改变 role，避免一个 role-specific Bundle 破坏同机另一个 service；role 变化必须
走显式迁移流程。

## 4. Linux 安装或更新

```bash
sudo ./install-knoa-bundle.sh \
  --role node \
  --bundle ./knoa-node-0.3.0-linux-x86_64.zip \
  --trust-store ./release-trust.json \
  --updater ./knoa-update
```

Hub 和 `all` 只需改变 `--role`。安装器创建 `knoa-hub.service` 和/或 `knoa-node.service`，启动后检查真实
HTTP health；失败时使用 `knoa-update reject` 恢复 previous，再重启旧服务。

## 5. 原生 updater 命令合同

```text
knoa-update install   验证、stage、离线健康检查、激活
knoa-update current   输出当前不可变版本目录
knoa-update run       从 state.json 解析当前版本并启动签名 entrypoint
knoa-update rollback  管理员主动切到 previous
knoa-update reject    当前版本真实服务健康失败，恢复 previous/清空首次安装
```

service 永远调用稳定的 `knoa-update run`，不把某个版本目录硬编码进 WinSW XML 或 systemd unit。因此更新只需
切换一个小型原子 state pointer；正在运行的旧进程继续使用旧的不可变文件，直到 service restart。

## 6. 发布构建

发布 CI 在目标 OS 上先按 `uv.lock` 物化 application tree，再执行一条产品构建命令。版本默认读取 Knoa
版本源，不要求发布者手填：

```bash
python scripts/build_product_release.py \
  --role node \
  --target-os linux \
  --target-arch x86_64 \
  --runtime /release-input/python-runtime \
  --application /release-input/application \
  --output-directory /release-output \
  --signing-key /secure/product-release-key.pem \
  --key-id knoa-product-2026
```

Windows 额外传入发布系统审核固定的 WinSW：

```text
--winsw C:\release-input\WinSW-x64.exe
```

用户机器不参与依赖解析。product signing private key 不进入仓库、Bundle、Hub 或 Node。

## 7. 首次下载信任

ZIP 内的安装脚本虽然也被 Manifest 签名，但运行它之前仍存在 bootstrap 边界。正式发布必须通过代码签名的
安装器/下载页交付以下三项，并固定产品 Trust Store 指纹：

- `knoa-update` 原生二进制；
- product Trust Store；
- Bundle 和安装入口。

Updater 验证成功之后，Bundle 内所有 runtime、application、WinSW 和 service 模板才可进入产品目录。不能从
未校验 ZIP 直接复制 WinSW 或执行应用代码。

## 8. 源码部署的定位

`deploy/windows/Install-Knoa.ps1` 与 `deploy/linux/install-knoa.sh` 保留为开发迁移入口，不再是正式产品路径。
产品文档和用户 UI 只展示 Bundle 安装/更新。待干净虚拟机矩阵完成后，旧源码产品路径应删除或移入
`dev/`，不能长期形成两套产品交付模型。
