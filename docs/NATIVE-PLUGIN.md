# QQ 同客户端插件（实验版）

QQmanager 扩展运行在用户正常使用的 QQ 中，观察已经存在的消息会话，将指定群的新消息发送到本机服务。不会另外登录一个 QQ，不调用消息发送或群管理接口。

## 兼容性

- Mac QQ 7.0.3-54395 / Apple Silicon：已验证启动、账号匹配、消息监听，以及一条真实消息的入库、模型分析与引用校验。长期稳定性、不同消息类型和全部群覆盖仍需测试。
- 其他 Mac QQ 版本：未验证，更新 QQ 后需要重新核对。
- Windows：共享插件代码与 manifest，尚未完成 Windows QQ 实机验证。需要用户已有的兼容 LiteLoaderQQNT 环境；桌面安装器不会自动安装 QQ 补丁或加载器。

插件 0.1.5 通过独立的 JavaScript 转发对象处理只读原生接口，不修改原生二进制。相关设计依据见 [上游只读导出适配记录](https://github.com/NapNeko/NapCatQQ/commit/e8fbda4be614cd5a9deacb9c563b2447b04cd3b8)。QQmanager 不加载 NapCat 登录流程。

## Mac 安装与恢复

1. 在 QQmanager“自动接入”填写日常 QQ 账号与目标群号，保存接收范围。
2. 保存 QQ 未发送的内容，正常退出 QQ。
3. 从 QQmanager 应用菜单打开“安装 / 更新 QQ 消息插件”。
4. 选择 `/Applications/QQ.app`，再选择 `~/Library/Containers/com.tencent.qq/Data/Documents`。系统目录或应用管理权限需要用户授权；不要求关闭系统保护。
5. 助手先备份入口，然后把扩展写入所选数据目录的 `QQmanager-native` 子目录，切换 QQ 的 JavaScript 启动入口。
6. 正常打开 QQ，在“自动接入”核对监听、账号及入库状态，再用新消息验证。

发生异常时，退出 QQ，使用同一助手“恢复原版 QQ”。助手的原始入口备份位于 `~/Library/Application Support/QQmanagerInstaller/`。恢复会检查哈希，不覆盖 QQ 后续升级造成的变化；原文和模型设置保留。

原生助手 0.1.5 使用附着于主窗口的异步选择框。取消返回主界面；⌘Q 退出，重新打开可继续操作。命令行开发工具 `scripts/native/install_mac.py` 的备份与 GUI 助手独立，请用对应工具恢复。

## Windows 接入

桌面安装器内置 QQmanager 的导入、搜索、模型分析与待办功能；QQ 消息自动接入另行配置。

1. 确认 Windows QQ 已安装兼容且正常工作的 LiteLoaderQQNT。
2. 在 QQmanager 接入页先保存 QQ 账号。菜单选择“安装 / 更新 QQ 消息插件”，选择包含 plugins 和 data 的 LiteLoaderQQNT 用户目录，即可复制插件并配对（0.4.0 起）。不需要安装 Python。
3. 源码用户也可选择手动安装插件并使用以下配对脚本：

```powershell
python scripts/native/pair_windows.py --plugin-data "C:\YourLoader\data\qqmanager_native" --account "YourQQNumber" --groups "Group1,Group2"
```

4. 正常重启 QQ 并检查收集状态。没有兼容加载器时，先使用导入与文件夹监听。

加载器文档：[LiteLoaderQQNT 安装说明](https://liteloaderqqnt.github.io/guide/install.html)。上游维护与 QQ 文件校验受版本影响，本项目不分发校验补丁、注入 DLL 或 QQ 程序。

## 数据边界

- 只收集绑定账号、选定群、插件启用后收到的新消息；不补齐历史或离线消息。
- 文本、所选群图片和文件可索引；单附件最大 20 MB。图片在本机 OCR，语音与视频暂不识别，不自动访问消息中的网页链接。
- 插件仅持有本机配对 Token，不持有模型 Key。模型分析受 QQmanager 中的开关、账号与群范围控制。
- 队列上限 2,000 条，定期落盘；服务恢复后重试，服务端去重。意外退出可能丢失尚未落盘的消息；超出队列上限会报告丢弃计数，不承诺无损。
- 撤销群或更换绑定账号后，不再发送对应待发事件。

## 验证标准

区分“插件心跳”“消息监听”“账号匹配”“原文入库”“模型分析完成”。一个阶段成功不代表全部完成。发布兼容性结论时应单独记录：收发消息、未打开的群、最小化、静音群、重启恢复和正常聊天是否通过；只使用合成内容或匿名统计，不公开私人聊天。

## 群列表选择

群目录在保留 QQ 原有调用和监听器的前提下，观察 `onGroupListUpdate`，并允许用户通过本机服务请求只读 `getGroupList(true)` 刷新。只同步群号与群名称，不复制成员列表或未选群内容。接口依据：[上游群列表实现](https://github.com/NapNeko/NapCatQQ/blob/main/packages/napcat-core/apis/group.ts)。元数据按绑定账号隔离，只保存在本机；仍需用户明确勾选群才开始收集。最多同步 1,000 个群。

## 附件与旧记录补取（插件 0.1.5）

详情可预览原图、查看提取正文或下载原文件。DOCX、文本及文字型 PDF 可提取正文；扫描 PDF、语音和视频暂不识别。Mac 使用系统 Vision，Windows 使用系统 OCR（依赖已安装的识别语言）。识别文字带来源标注，不覆盖原始消息。已启用模型分析时，提取文字和必要上下文会发往用户配置的模型；原图二进制不发给模型。

旧版只存了占位文字。更新后插件尝试读取所选群最近 100 条记录，仅匹配数据库里已有消息的指纹来补取附件，不保存未匹配的历史内容。失败可在详情中“重新获取附件”。过期、撤回或超出最近记录范围的附件可能无法补齐。附件获取复用当前 QQ 会话，不发送聊天消息。

实现参考：[上游消息服务接口](https://github.com/NapNeko/NapCatQQ/blob/main/packages/napcat-core/services/NodeIKernelMsgService.ts)。
