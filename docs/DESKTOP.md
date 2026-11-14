# 桌面应用

## 日常使用

1. 双击 QQmanager。首次启动自动运行本机服务并打开应用窗口。
2. 在“关注与设置”配置并测试模型；在“自动接入”完成 QQ 插件配对或选择资料监听目录。
3. 平时正常使用 QQ；在 QQmanager 查看重要信息、原始记录、文件与待办。
4. 关闭窗口后后台继续运行。菜单栏 / 托盘图标可重新打开窗口，或选择“退出 QQmanager”。

菜单中的“登录系统后后台启动”默认开启，可关闭。开机启动指**当前用户登录系统后**，不在未登录时运行，不自动登录 QQ。第一次将 Mac 应用从 DMG 拖入“应用程序”后再打开；不要长期从 DMG 或临时解压目录启动。

## 数据与旧版兼容

新安装默认使用 Electron 用户数据目录下的 `data`（Mac 通常为 `~/Library/Application Support/QQmanager/data`，Windows 为 `%APPDATA%/QQmanager/data`）。可以从应用菜单打开实际数据文件夹。

已有兼容 QQmanager 服务占用 8765 时，桌面版核对健康协议并复用它，记住其数据目录，不复制或重置聊天、群选择或密钥。退出桌面版不会停止外部旧服务。若要迁移到完全由桌面版管理，先确认应用已识别原数据目录，再停用旧 LaunchAgent；下次打开桌面版会使用原目录启动内置服务。

应用自建的服务随应用退出；应用异常结束后，通过管道 EOF 停止子进程，减少残留服务占用端口。服务启动失败会显示错误，不自动更换端口造成 QQ 插件断连。端口固定为 127.0.0.1:8765。

原文、文件、模型 Key 及插件配对凭据均属于本机私有数据。当前数据库中的模型 Key 依赖操作系统用户权限保护，尚未迁移至系统凭据保险库。

## QQ 插件

Mac 菜单提供“安装 / 更新 QQ 消息插件”，打开随包附带的原生助手。QQ 必须先正常退出，用户授权 QQ 应用及其数据目录后才能安装。助手可恢复原版入口。正常使用不需要重复打开助手。

Windows 安装器只安装 QQmanager，不修改 QQ。QQ 自动接入需兼容加载器，见 [插件说明](NATIVE-PLUGIN.md)。未完成 Windows QQ 实测时，推荐先用导入和目录监听。

## 构建

依赖 Node.js 24、Python 3.11+（源码最低 3.9）；`npm ci` 安装锁定的 Electron 与 electron-builder，`pip install -r requirements-build.txt` 安装后端构建依赖。

执行 `python scripts/desktop/build_backend.py` 会把服务和网页冻结到 `build/backend`，再执行 `python scripts/desktop/smoke_backend.py` 验证真实打包程序。Mac 还需 `python scripts/build_mac_installer.py`（需要 Xcode Command Line Tools）。

- Mac：`npm run dist:mac` → `release/*.dmg` / `*.zip`
- Windows：`npm run dist:win` → `release/*.exe`（当前用户 NSIS 一键安装）

必须在目标 OS / CPU 上打包 Python 后端；GitHub Actions 分别提供 macOS arm64、macOS x64、Windows x64 构建。Windows QQ 自动接入不属于安装包 smoke test 的覆盖范围。

## 发布与签名

首次公开版本采用试用版发布。当前构建没有 Apple Developer ID、公证或 Windows 商业签名，可能出现 Gatekeeper / SmartScreen 来源提示；用户自行核对仓库和 SHA-256，不能要求关闭系统保护。正式广泛分发前，维护者应添加平台证书并调整 electron-builder 签名、公证配置。

构建只从明确的源码目录加入文件，不包括 `data`、`.venv`、QQ、容器会话或本机设置。发布前执行 `scripts/desktop/audit_release.py` 和自动化测试。依赖的许可证需随构建产物保留；Electron 自带第三方许可证文件不得删除。
