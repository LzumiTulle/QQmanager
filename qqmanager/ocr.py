"""Offline OCR; no image is uploaded to a model provider."""
import json
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent

def recognize(path):
    if sys.platform=='darwin':
        helper=ROOT/'native-tools/qqmanager-ocr'
        if not helper.exists() and not getattr(sys,'frozen',False):
            helper=ROOT/'build/native-tools/qqmanager-ocr'
            if not helper.exists():
                helper.parent.mkdir(parents=True,exist_ok=True)
                subprocess.run(['/usr/bin/xcrun','swiftc','-module-cache-path',str(ROOT/'build/swift-module-cache'),str(ROOT/'native-tools/ocr.swift'),'-O','-o',str(helper)],check=True,capture_output=True,timeout=120)
        result=subprocess.run([str(helper),str(path)],check=True,capture_output=True,timeout=45)
        return str(json.loads(result.stdout)['text'])[:1000000]
    if sys.platform=='win32':
        result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-File',str(ROOT/'native-tools/ocr.ps1'),str(path)],check=True,capture_output=True,timeout=60)
        return str(json.loads(result.stdout.decode('utf-8-sig'))['text'])[:1000000]
    raise RuntimeError('本机 OCR 暂不支持此系统')
