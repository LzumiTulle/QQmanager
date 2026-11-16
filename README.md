# QQmanager

把热闹的群聊整理成有原文依据的通知、待办和资料。支持 **macOS / Windows 桌面应用**、聊天与文件导入、全文搜索，以及 DeepSeek / OpenAI 兼容接口。

**[下载安装包](https://github.com/LzumiTulle/QQmanager/releases)** · [桌面使用与构建](docs/DESKTOP.md) · [QQ 插件说明](docs/NATIVE-PLUGIN.md) · [隐私与安全](SECURITY.md)

## 安装与使用

- **Mac**：下载对应芯片的 `.dmg`，拖入“应用程序”，双击 QQmanager。
- **Windows x64**：下载 `.exe` 安装器，双击完成当前用户安装，随后从桌面或开始菜单打开。
- 安装包内置 Python 服务，不用安装 Python、Node、Docker，也不用输入网址。
- 关闭窗口后仍在菜单栏 / 托盘运行。点击托盘图标重新打开；托盘菜单可关闭“登录系统后后台启动”或彻底退出。
- 首次启动默认启用登录后后台启动，可随时关闭。系统未登录、休眠、关机时不能实时收集。

> 当前是 **0.2.6 试用版**。公开安装包尚未配置商业代码签名和 Apple 公证，系统可能要求确认开发者来源。不要关闭系统安全保护。安装桌面应用与接入 QQ 是两个步骤：第三方 QQ 插件需要单独授权，不能承诺所有 QQ 版本一键兼容。

## 界面

打开即进入紧凑的信息列表，默认最新优先。每条显示重点、类型、来源群和时间，点击后在右侧查看原文、引用依据并添加待办。左侧分别进入文件、待办、记录、接入和设置；浅色 / 深色主题选择会保存在本机。模型与接入的详细配置按需展开。

## 功能

- 导入 TXT / JSON / CSV 聊天记录，保留发送者、群、时间和原文。
- 索引文本、DOCX、文字型 PDF；按文件名或正文搜索。
- 本地规则识别重要消息；可选大模型提取通知、待办、资料，保留逐字引用。
- 运势、机器人游戏、寒暄及缺少有效信息的福利播报会被过滤；原始记录仍可搜索。同群 24 小时内内容一致的重复通知合并展示，保留每次发送的原文，时间或地点变更单独保留。旧分析可在模型设置中点击“按新规则整理已有消息”重新检查。
- 自定义关注词、收藏、忽略、原文追溯。
- 确认提醒时间后建立待办，站内与桌面提醒。
- 文件夹自动监听；原文去重，模型任务持久化、重试和调用次数限制。
- 实验性 QQ 同客户端插件：复用日常 QQ 登录，将指定群的新消息传给本机服务。

### QQ 自动收集的实际边界

Mac QQ **7.0.3-54395 / Apple Silicon** 上已验证插件加载、账号匹配，以及真实群消息的“收集 → 入库 → DeepSeek 分析 → 原文引用”。尚未完成长期稳定性、所有群或所有消息类型覆盖测试。

Windows 桌面应用可独立使用导入、搜索、待办与模型配置。**Windows QQ 自动接入仍依赖用户已有的兼容 LiteLoaderQQNT 环境，未完成实机验证**；不附带 QQ 二进制、登录组件、校验补丁或 DLL。不要把“Windows 安装包构建通过”等同于“Windows QQ 插件已验证”。

插件只收集启用后、绑定账号、所选群的新消息；不保证补齐离线历史。插件 0.1.5 起可获取所选群的图片和文件（单个最多 20 MB），本机 OCR 提取图片文字，保留原图和原始消息。提取文字随消息发送到已配置的模型服务。旧记录仅尝试补取已收集消息的附件。QQ 更新可能使插件失效，可通过 Mac 插件助手恢复原始入口。

## 隐私

数据保存在本机 SQLite 和资料目录。默认不启用云端分析；启用后，会向你配置的模型服务发送指定群消息及有限上下文。模型 Key 不进入 QQ 插件。不要将数据目录、密钥、QQ 会话或真实聊天日志上传到仓库。

提醒和 AI 判断仅供辅助，提取的待办与日期需要用户确认。

## 开发

```sh
python3 -m venv .venv
# Windows: .venv\Scripts\python.exe；Mac: .venv/bin/python
.venv/bin/python -m pip install -r requirements-build.txt
npm ci
npm start
```

也可单独运行 `python run.py`，浏览器访问 `http://127.0.0.1:8765`。服务只监听本机回环地址。

```sh
python -B -m unittest discover -s tests
node --test tests/*.test.cjs
python scripts/desktop/build_backend.py
python scripts/desktop/smoke_backend.py
# macOS 额外构建插件助手
python scripts/build_mac_installer.py
npm run dist:mac
# Windows 在 Windows 上构建
npm run dist:win
```

GitHub Actions 自动在 macOS / Windows 构建并测试，提供安装包和 SHA-256 校验文件；版本标签生成草稿 Release，核验后发布。

## 开源

本项目采用 [MIT](LICENSE)。第三方依赖及 QQ 的名称、商标与软件仍遵循各自许可；本项目不是腾讯官方产品。见 [贡献指南](CONTRIBUTING.md)。
