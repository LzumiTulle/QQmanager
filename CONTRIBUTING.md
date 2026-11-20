# 贡献指南

欢迎提交 Issue 与 Pull Request。请附操作系统、QQmanager 版本、复现步骤、预期与实际行为；只使用合成数据，勿上传真实聊天和密钥。

核心目录：`qqmanager/` Python 服务，`web/` 界面，`src-tauri/` Tauri 桌面壳，`plugins/qqmanager-native/` 实验性 QQ 插件，`macos/` 原生安装助手。

提交前运行 Python 与 Node 测试；涉及打包时运行冻结服务 smoke test。涉及 QQ 兼容性时区分模拟测试、真实启动、真实消息、后台覆盖，不把其中一项等同于全部通过。

macOS 和 Windows 维护者共同更新[开发交接存档](docs/QQmanager-Windows-Guide-zh-CN.txt)。修改共享流程或平台交互时，在同一个提交或 PR 记录设计依据、平台差异与各端验证结果。事项归属、数据和状态转换共用一套逻辑，快捷键、系统托盘或菜单栏等入口遵循各平台习惯。

新依赖需要注明用途与许可证。保持日志和错误提示不含凭据。提交代码意味着同意按项目 MIT 许可证贡献。
