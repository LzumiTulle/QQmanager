# 轻量化与移动端边界

当前收集链路是：正常登录的 QQ → QQmanager 客户端插件 → 本机数据服务 → 页面与模型分析。插件使用当前 QQ 的消息、群列表及附件服务，不启动第二份 QQ 登录，也不需要 NapCat 程序。OneBot 是内部兼容消息格式，使用该格式不等于运行 NapCat。

## 本次 REA 验证

2026-11-17 使用官方 [morluto/rea](https://github.com/morluto/rea) 的 npm 包 `rea-agents@6.9.0`，对 Mac QQ 7.0.3-54395 的应用资源做只读静态检查。REA 只装在临时研究目录，没有注册到用户的代理配置，也没有加入 QQmanager 的产品依赖。

`inspect-artifact` 完成 ASAR 文件目录清点。`analyze-javascript-application` 报告 189 个相关文件为非 UTF-8、解析出的 JavaScript 文件数为 0，并明确标注覆盖不完整。QQ 包自身同时声明 `isByteCodeShell: true`。因此，本次静态结果不足以恢复可独立分发的消息读取实现；不能把“未解析到接口”解释成“接口不存在”。

已有插件的消息监听、群列表、图片与文件下载代码只调用 QQ 当前进程提供的服务。参考 `plugins/qqmanager-native/core.cjs`、`main.cjs`、`media.cjs`。这些调用在当前 QQ 会话中的可用性，不证明它们能脱离 QQ 独立运行。

REA 是开发工具，可以用于后续版本差异、入口和调用关系调查。它不自动提供 QQ 登录、历史数据库解密或移动端数据权限。需要另行验证的历史读取与独立收集器不能作为已完成功能宣传；原始逆向证据和 QQ 二进制不进入公开仓库或安装包。

## 体积与推荐方向

0.4.0 已将 Electron 替换为 Tauri：Mac 使用系统 WebKit，Windows 使用系统 WebView2，安装程序可补装缺失的 WebView2 运行时。Mac arm64 实测应用磁盘占用约 29 MB、DMG 约 10 MB；旧 Electron 应用约 274 MB。客户端插件约 32 KB，消息服务和数据目录继续复用。不同架构及构建环境的体积可能不同。

1. 保持轻量收集插件和数据服务分离，继续复用用户正常登录的 QQ。
2. 系统 WebView 外壳保留托盘、通知、后台自动启动和本机文件下载。Windows 安装与启动由 CI 检查；实际 QQ 插件仍需在用户所用 QQ / LiteLoader 版本上验证。
3. 手机优先作为查看、搜索、确认调课的客户端。若要连接电脑，需要单独实现配对、加密通信、权限和离线数据策略；当前版本不开放远程监听。
4. 手机独立收集不是桌面插件的直接移植。普通应用受到系统沙箱限制，不能直接读取 QQ 的私有数据库；用户主动分享文件可以另做导入入口，但不等于完整聊天收集。

系统资料：[Android 应用沙箱](https://source.android.com/docs/security/app-sandbox)、[Apple 文件系统与 iOS 沙箱](https://developer.apple.com/library/archive/documentation/FileManagement/Conceptual/FileSystemProgrammingGuide/FileSystemDetails/FileSystemDetails.html)。
