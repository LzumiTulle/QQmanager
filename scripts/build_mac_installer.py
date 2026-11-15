"""Build a local, ad-hoc signed native installer. Never starts or modifies QQ."""
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile
import time
import platform
ROOT=Path(__file__).resolve().parent.parent
APP=ROOT/'dist/QQmanager Installer.app'
APP.parent.mkdir(parents=True,exist_ok=True)
# Compile and sign away from the live bundle. Never overwrite a running
# installer's executable or resources in place during development.
stage=Path(tempfile.mkdtemp(prefix='.installer-build-',dir=APP.parent))/'QQmanager Installer.app'
contents=stage/'Contents';binary=contents/'MacOS';resources=contents/'Resources/plugin'
binary.mkdir(parents=True,exist_ok=True);resources.mkdir(parents=True,exist_ok=True)
for name in ('core.cjs','main.cjs','manifest.json'):shutil.copy2(ROOT/'plugins/qqmanager-native'/name,resources/name)
info={'CFBundleIdentifier':'local.qqmanager.installer','CFBundleName':'QQmanager Installer','CFBundleDisplayName':'QQmanager 插件安装助手','CFBundleExecutable':'QQmanagerInstaller','CFBundlePackageType':'APPL','CFBundleShortVersionString':'0.1.3','CFBundleVersion':'4','LSMinimumSystemVersion':'12.0','NSHighResolutionCapable':True,'NSAppTransportSecurity':{'NSAllowsLocalNetworking':True}}
(contents/'Info.plist').write_bytes(plistlib.dumps(info))
cache=ROOT/'data/swift-module-cache';cache.mkdir(parents=True,exist_ok=True)
subprocess.run(['xcrun','swiftc','-module-cache-path',str(cache),'-target',platform.machine()+'-apple-macos12.0','-O',str(ROOT/'macos/QQmanagerInstaller/main.swift'),'-o',str(binary/'QQmanagerInstaller'),'-framework','AppKit','-framework','CryptoKit'],check=True)
subprocess.run(['codesign','--force','--sign','-','--identifier','local.qqmanager.installer',str(stage)],check=True)
subprocess.run(['codesign','--verify','--deep','--strict',str(stage)],check=True)
previous=ROOT/'data/installer-build-backups'/str(time.time_ns())/'QQmanager Installer.app'
if APP.exists():
    previous.parent.mkdir(parents=True,exist_ok=True)
    APP.rename(previous)
try:
    stage.rename(APP)
except Exception:
    if previous.exists():previous.rename(APP)
    raise
stage.parent.rmdir()
print(APP)
