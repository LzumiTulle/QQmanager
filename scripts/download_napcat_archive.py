"""Fetch a pinned third-party archive without loading or executing its contents."""
import concurrent.futures
import hashlib
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / 'data/napcat-install'
URL = 'https://github.com/XingYunStar/NapCatQQ/releases/download/v4.18.42/napcat-docker-v4.18.42-arm64.tar'
SIZE = 597573632
SHA256 = '0d14594844e9135c760e710754a8778d98649de2ca837fce5a61b69b7d39954d'
CHUNK = 32 * 1024 * 1024


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    partial = DEST / 'napcat-arm64.tar'
    parts = DEST / 'parts'
    parts.mkdir(exist_ok=True)
    # Reuse only complete immutable chunks already received by the initial download.
    if partial.exists():
        with partial.open('rb') as source:
            for i in range(partial.stat().st_size // CHUNK):
                path = parts / str(i)
                data = source.read(CHUNK)
                if not path.exists(): path.write_bytes(data)

    def fetch(index):
        start, end = index * CHUNK, min((index + 1) * CHUNK, SIZE) - 1
        target = parts / str(index)
        if target.exists() and target.stat().st_size == end - start + 1:
            return index
        tmp = target.with_suffix('.partial')
        cmd = ['curl', '--proxy', 'http://127.0.0.1:7890', '-fLsS', '--retry', '3', '--retry-all-errors',
               '--connect-timeout', '20', '--max-time', '600', '--range', f'{start}-{end}', '-w', '%{http_code}',
               '-o', str(tmp), URL]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        if result.stdout.strip() != '206' or tmp.stat().st_size != end-start+1:
            raise RuntimeError('下载范围不匹配，未使用收到的文件')
        tmp.replace(target)
        return index

    count = (SIZE + CHUNK - 1) // CHUNK
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        for done, future in enumerate(concurrent.futures.as_completed([pool.submit(fetch, i) for i in range(count)]), 1):
            future.result()
            print(f'归档分段完成 {done}/{count}', flush=True)
    staged = DEST / 'napcat-arm64.verified.tar'
    digest = hashlib.sha256()
    with staged.open('wb') as output:
        for i in range(count):
            with (parts / str(i)).open('rb') as source:
                while True:
                    data = source.read(1024 * 1024)
                    if not data: break
                    digest.update(data); output.write(data)
    if digest.hexdigest() != SHA256:
        raise RuntimeError('归档 SHA-256 不匹配；禁止加载或执行')
    staged.replace(partial)
    print('SHA-256 与归档发布者提供的值一致。尚未导入或执行镜像。', flush=True)


if __name__ == '__main__': main()
