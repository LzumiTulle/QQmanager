import AppKit
import Foundation
import CryptoKit

let originalMain = "./application.asar/app_launcher/index.js"
let extensionMain = "./app_launcher/qqmanager-bootstrap.cjs"
func digest(_ data: Data) -> String { SHA256.hash(data: data).map { String(format:"%02x",$0) }.joined() }
func json(_ object: Any) throws -> Data { try JSONSerialization.data(withJSONObject: object,options:[.prettyPrinted,.sortedKeys]) }
func object(_ data: Data) throws -> [String:Any] {
    guard let value=try JSONSerialization.jsonObject(with:data) as? [String:Any] else {throw Failure("配置格式无效")};return value
}
struct Failure: Error, LocalizedError {
    let message:String
    init(_ message:String){self.message=message}
    var errorDescription:String? {message}
}
func literal(_ string:String)->String {
    let data=try! JSONSerialization.data(withJSONObject:[string]);let value=String(data:data,encoding:.utf8)!;return String(value.dropFirst().dropLast())
}

final class Installer: NSObject, NSApplicationDelegate {
    var window:NSWindow!
    let output=NSTextView()
    var buttons:[NSButton]=[]
    var activePanel:NSOpenPanel?
    var operationInProgress=false
    let backupDir=FileManager.default.urls(for:.applicationSupportDirectory,in:.userDomainMask)[0].appendingPathComponent("QQmanagerInstaller")
    func applicationDidFinishLaunching(_ notification:Notification){
        let menu=NSMenu(),appItem=NSMenuItem(),appMenu=NSMenu()
        appMenu.addItem(withTitle:"退出 QQmanager 安装助手",action:#selector(NSApplication.terminate(_:)),keyEquivalent:"q")
        appItem.submenu=appMenu;menu.addItem(appItem);NSApp.mainMenu=menu
        window=NSWindow(contentRect:NSRect(x:0,y:0,width:660,height:490),styleMask:[.titled,.closable,.miniaturizable],backing:.buffered,defer:false)
        window.title="QQmanager 插件安装助手 · 0.1.4 实验版";window.center();window.isReleasedWhenClosed=false
        let root=NSStackView();root.orientation = .vertical;root.alignment = .leading;root.spacing=18;root.edgeInsets=NSEdgeInsets(top:26,left:26,bottom:24,right:26)
        root.translatesAutoresizingMaskIntoConstraints=false;window.contentView!.addSubview(root)
        NSLayoutConstraint.activate([root.topAnchor.constraint(equalTo:window.contentView!.topAnchor),root.bottomAnchor.constraint(equalTo:window.contentView!.bottomAnchor),root.leadingAnchor.constraint(equalTo:window.contentView!.leadingAnchor),root.trailingAnchor.constraint(equalTo:window.contentView!.trailingAnchor)])
        let title=NSTextField(labelWithString:"正常使用 QQ，重要信息自动留下")
        title.font = .boldSystemFont(ofSize:23);root.addArrangedSubview(title)
        let intro=NSTextField(wrappingLabelWithString:"首次安装：选择 QQ 应用 → 授权 QQ 数据目录 → 安装。\n插件随你正常使用的 QQ 加载，不创建另一份登录。当前为实验版，后台收集和聊天兼容性仍需验证。")
        intro.font = .systemFont(ofSize:14);root.addArrangedSubview(intro)
        let actions=NSStackView();actions.orientation = .horizontal;actions.spacing=12
        for (label,action) in [("安装 / 更新插件",#selector(install)),("恢复原版 QQ",#selector(restore)),("检查安装状态",#selector(check))] {
            let button=NSButton(title:label,target:self,action:action);button.bezelStyle = .rounded;actions.addArrangedSubview(button);buttons.append(button)
        };root.addArrangedSubview(actions)
        let scroll=NSScrollView();scroll.hasVerticalScroller=true;scroll.borderType = .bezelBorder
        output.isEditable=false;output.font = .systemFont(ofSize:13);output.textContainerInset=NSSize(width:12,height:12)
        output.isVerticallyResizable=true;output.isHorizontallyResizable=false;output.autoresizingMask = [.width];output.textContainer?.widthTracksTextView=true
        scroll.documentView=output;root.addArrangedSubview(scroll)
        scroll.heightAnchor.constraint(greaterThanOrEqualToConstant:195).isActive=true
        scroll.widthAnchor.constraint(equalTo:root.widthAnchor,constant:-52).isActive=true
        output.string="请选择操作。已安装的用户可更新插件，或点击“检查安装状态”。\n\n安装前请保存待发送内容并自行退出 QQ，安装完成后再正常打开。\n目录选择框会附着在本窗口上；点击“取消”可返回。退出助手可按 ⌘Q。\n\n本工具会先备份原始入口，恢复时检查文件是否被 QQ 更新，避免覆盖新版文件。"
        window.makeKeyAndOrderFront(nil);NSApp.activate(ignoringOtherApps:true)
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender:NSApplication)->Bool {true}
    func applicationShouldHandleReopen(_ sender:NSApplication,hasVisibleWindows flag:Bool)->Bool {
        window.deminiaturize(nil);window.makeKeyAndOrderFront(nil)
        activePanel?.makeKeyAndOrderFront(nil);sender.activate(ignoringOtherApps:true);return true
    }
    func applicationShouldTerminate(_ sender:NSApplication)->NSApplication.TerminateReply {
        activePanel?.cancel(nil);return .terminateNow
    }
    func log(_ message:String){output.string=message}
    func busy(_ value:Bool){operationInProgress=value;buttons.forEach{$0.isEnabled = !value}}
    @MainActor func choose(_ panel:NSOpenPanel) async->URL? {
        activePanel=panel
        window.makeKeyAndOrderFront(nil)
        let response:NSApplication.ModalResponse=await withCheckedContinuation { continuation in
            panel.beginSheetModal(for:window) { reply in continuation.resume(returning:reply) }
        }
        activePanel=nil
        guard response == .OK else {log("已取消选择，未执行本次安装、更新或恢复。可以重新选择操作。");return nil}
        return panel.url
    }
    @MainActor func chooseQQ() async->URL? {
        let panel=NSOpenPanel();panel.title="选择要安装插件的 QQ.app";panel.message="仅修改所选 QQ 的启动入口，可恢复；不会替换 QQ 二进制。"
        // QQ.app can be classified as a directory after its resource entry changes.
        // Validate its bundle ID below instead of relying on LaunchServices type filtering.
        panel.canChooseFiles=true;panel.canChooseDirectories=true;panel.treatsFilePackagesAsDirectories=false;panel.allowsMultipleSelection=false;panel.directoryURL=URL(fileURLWithPath:"/Applications")
        guard let url=await choose(panel) else{return nil}
        guard let bundle=Bundle(url:url),bundle.bundleIdentifier=="com.tencent.qq" else {log("请选择腾讯 QQ.app。");return nil};return url
    }
    @MainActor func chooseData() async->URL? {
        let panel=NSOpenPanel();panel.title="授权 QQ 的 Documents 数据目录";panel.message="选择 com.tencent.qq/Data/Documents。插件文件与本机配对信息仅写入其 QQmanager-native 子目录。"
        panel.canChooseFiles=false;panel.canChooseDirectories=true;panel.canCreateDirectories=false;panel.allowsMultipleSelection=false
        panel.directoryURL=FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Containers/com.tencent.qq/Data/Documents")
        guard let url=await choose(panel) else{return nil}
        guard url.standardizedFileURL.path.hasSuffix("/Library/Containers/com.tencent.qq/Data/Documents") else {log("请选择 QQ 沙盒中的 Documents 目录，避免把插件配置写入错误位置。");return nil};return url
    }
    func paths(_ app:URL)->(URL,URL){
        let root=app.appendingPathComponent("Contents/Resources/app")
        return (root.appendingPathComponent("package.json"),root.appendingPathComponent("app_launcher/qqmanager-bootstrap.cjs"))
    }
    func backupPath(_ app:URL)->URL {backupDir.appendingPathComponent(digest(Data(app.standardizedFileURL.path.utf8))+".json")}
    func hasQQRunning()->Bool {NSRunningApplication.runningApplications(withBundleIdentifier:"com.tencent.qq").contains{!$0.isTerminated}}
    func call(_ route:String,_ body:[String:Any]?,_ csrf:String="") async throws->[String:Any] {
        var request=URLRequest(url:URL(string:"http://127.0.0.1:8765"+route)!);request.timeoutInterval=8
        if let body=body {request.httpMethod="POST";request.httpBody=try json(body);request.setValue("application/json",forHTTPHeaderField:"Content-Type");request.setValue(csrf,forHTTPHeaderField:"X-CSRF-Token")}
        let (data,response)=try await URLSession.shared.data(for:request)
        guard (response as? HTTPURLResponse)?.statusCode==200 else {throw Failure("QQmanager 本地服务未就绪或配对失败，请先启动软件并保存账号与群选择。")}
        return try object(data)
    }
    func writePrivate(_ data:Data,_ url:URL)throws {try data.write(to:url,options:.atomic);try FileManager.default.setAttributes([.posixPermissions:0o600],ofItemAtPath:url.path)}
    @objc func install(){
        guard !operationInProgress else{return}
        if hasQQRunning(){log("请先保存待发送内容，并在 QQ 菜单中选择“退出 QQ”。\n安装助手不会自动退出你的 QQ。退出后再点击“安装插件”。");return}
        busy(true)
        Task { @MainActor in
            defer {busy(false)}
            guard let app=await chooseQQ(),let documents=await chooseData() else{return}
            let appScope=app.startAccessingSecurityScopedResource(),dataScope=documents.startAccessingSecurityScopedResource()
            defer {if appScope{app.stopAccessingSecurityScopedResource()};if dataScope{documents.stopAccessingSecurityScopedResource()}}
            log("正在核对原版入口、备份并安装…")
            do {
                if hasQQRunning(){throw Failure("QQ 已重新运行，请退出后再安装。")}
                let (package,bootstrap)=paths(app);let original=try Data(contentsOf:package);var settings=try object(original)
                let updating=settings["main"] as? String==extensionMain
                if updating {
                    let backup=try object(Data(contentsOf:backupPath(app)))
                    guard digest(original)==backup["installed_sha256"] as? String,
                          digest(try Data(contentsOf:bootstrap))==backup["bootstrap_sha256"] as? String else {throw Failure("QQ 入口在安装后发生变化，停止更新以保留当前文件。")}
                } else {
                    guard settings["main"] as? String==originalMain else {throw Failure("当前 QQ 入口不是原版。为避免覆盖其他插件，本次已停止。")}
                    guard !FileManager.default.fileExists(atPath:bootstrap.path) else {throw Failure("QQ 中已有同名启动文件，请先检查安装状态。")}
                }
                let session=try await call("/api/session",nil)
                guard let csrf=session["csrf"] as? String else {throw Failure("本地服务会话无效")}
                let config=try await call("/api/native/pair",[:],csrf)
                guard let token=config["token"] as? String,token.count>=32 else {throw Failure("本地配对信息无效")}
                let profile=documents.appendingPathComponent("QQmanager-native",isDirectory:true)
                guard try Data(contentsOf:package)==original,!hasQQRunning() else {throw Failure("QQ 文件或运行状态发生变化，请退出 QQ 后重试。")}
                try FileManager.default.createDirectory(at:profile,withIntermediateDirectories:true,attributes:[.posixPermissions:0o700])
                for name in ["core.cjs","main.cjs","manifest.json"] {
                    guard let resource=Bundle.main.resourceURL?.appendingPathComponent("plugin").appendingPathComponent(name) else {throw Failure("安装包资源缺失")}
                    try writePrivate(Data(contentsOf:resource),profile.appendingPathComponent(name))
                }
                try writePrivate(json(config),profile.appendingPathComponent("config.json"))
                if updating {
                    log("插件已更新至 0.1.4。请正常打开 QQ。\n\n本次仅更新 QQmanager-native 中的扩展文件和本机配对配置；原始 QQ 入口备份保持不变。\n请在 QQmanager“自动接入”验证账号匹配、消息监听和真实消息入库。")
                    return
                }
                let loader="""
                'use strict';
                const path=require('node:path'),fs=require('node:fs');
                function report(phase,error){try{fs.writeFileSync(\(literal(profile.appendingPathComponent("bootstrap-status.json").path)),JSON.stringify({phase,error_name:error?.name||'',error_code:error?.code||'',at:Date.now()}),{mode:0o600});}catch(_){}}
                try{require(\(literal(profile.appendingPathComponent("main.cjs").path))).start(\(literal(profile.appendingPathComponent("config.json").path)));report('plugin_started');}catch(e){report('plugin_error',e);}
                require(path.join(process.resourcesPath,'app',\(literal(originalMain))));
                report('qq_entry_loaded');
                setImmediate(()=>{try{if(global.launcher?.installPathPkgJson)global.launcher.installPathPkgJson.main=\(literal(originalMain));}catch(_){}});
                """
                let boot=Data(loader.utf8);settings["main"]=extensionMain;let patched=try json(settings)
                let mode=(try FileManager.default.attributesOfItem(atPath:package.path)[.posixPermissions] as? NSNumber)?.intValue ?? 0o644
                let backup:[String:Any] = ["package":package.path,"original":original.base64EncodedString(),"original_sha256":digest(original),"installed_sha256":digest(patched),"bootstrap_sha256":digest(boot),"mode":mode]
                try FileManager.default.createDirectory(at:backupDir,withIntermediateDirectories:true,attributes:[.posixPermissions:0o700])
                try writePrivate(json(backup),backupPath(app))
                // Recheck after network/authorization to avoid overwriting an update.
                guard try Data(contentsOf:package)==original,!hasQQRunning() else {throw Failure("QQ 文件或运行状态发生变化，请重新检查后安装。")}
                try boot.write(to:bootstrap,options:.atomic)
                do {try patched.write(to:package,options:.atomic)}catch {try? FileManager.default.removeItem(at:bootstrap);throw error}
                try? FileManager.default.setAttributes([.posixPermissions:mode],ofItemAtPath:package.path)
                log("安装完成。请正常打开 QQ。\n\n本次操作：\n• 备份原始 package.json\n• 新增 app_launcher/qqmanager-bootstrap.cjs\n• 仅切换 package.json 的启动入口\n• 在已授权的 QQ 数据目录保存扩展及本机配对信息\n\n下一步：在 QQmanager“自动接入”查看插件心跳、账号匹配和消息接收。正常聊天及后台收集尚需实测。\n\n如异常，退出 QQ 后点击“恢复原版 QQ”。")
            } catch {
                log("未完成安装：\(error.localizedDescription)\n\n如果系统拒绝访问，请在系统提示中自行授权指定 QQ 目录或应用管理权限；不需要关闭系统保护。\n本工具没有自动启动或退出 QQ。可点击“检查安装状态”确认入口。")
            }
        }
    }
    @objc func restore(){
        guard !operationInProgress else{return}
        if hasQQRunning(){log("请先退出 QQ，再恢复原版。已保存的聊天和模型配置不会删除。");return}
        busy(true)
        Task { @MainActor in
        defer {busy(false)}
        guard let app=await chooseQQ() else{return};let scope=app.startAccessingSecurityScopedResource();defer{if scope{app.stopAccessingSecurityScopedResource()}}
        do {
            let (package,bootstrap)=paths(app);let backup=try object(Data(contentsOf:backupPath(app)));let current=try Data(contentsOf:package)
            if digest(current)==backup["original_sha256"] as? String{log("当前已经是备份时的原版 QQ 入口。");return}
            guard digest(current)==backup["installed_sha256"] as? String,let encoded=backup["original"] as? String,let original=Data(base64Encoded:encoded) else {throw Failure("QQ 文件在安装后有其他变化，停止覆盖。需要按当前版本重新检查。")}
            try original.write(to:package,options:.atomic)
            if let mode=backup["mode"] as? Int {try? FileManager.default.setAttributes([.posixPermissions:mode],ofItemAtPath:package.path)}
            if let data=try? Data(contentsOf:bootstrap),digest(data)==backup["bootstrap_sha256"] as? String{try FileManager.default.removeItem(at:bootstrap)}
            log("已恢复原版启动入口。请正常打开 QQ。\n插件数据和 QQmanager 的原文、待办、模型配置均保留。")
        }catch{log("恢复未完成：\(error.localizedDescription)")}
        }
    }
    @objc func check(){
        guard !operationInProgress else{return}
        busy(true)
        Task { @MainActor in
        defer {busy(false)}
        guard let app=await chooseQQ() else{return};let scope=app.startAccessingSecurityScopedResource();defer{if scope{app.stopAccessingSecurityScopedResource()}}
        do {let (package,_)=paths(app);let settings=try object(Data(contentsOf:package));let main=settings["main"] as? String ?? "未知"
            log("QQ 版本：\(settings["version"] ?? "未知")\n启动入口：\(main)\n\n\(main==extensionMain ? "已安装 QQmanager 入口；这不代表真实消息兼容性已通过。" : main==originalMain ? "当前是原版 QQ 入口。" : "当前使用其他入口，本工具不会覆盖。")")
        }catch{log("检查失败：\(error.localizedDescription)")}
        }
    }
}
let app=NSApplication.shared
let delegate=Installer();app.delegate=delegate;app.setActivationPolicy(.regular);app.run()
