#!/usr/bin/env python3
"""将 video_urls.json 同步到 index.html 内嵌 <script id="video-url-data"> 数据。
确保本地 file:// 测试和线上 HTTPS 都能正常加载 XHS 视频 URL。
"""

import json
import re
from pathlib import Path

VIDEO_URLS_PATH = Path('video_urls.json')
INDEX_PATH = Path('index.html')

# ── 主流程 ──

def main():
    if not VIDEO_URLS_PATH.exists():
        print(f'⚠️  {VIDEO_URLS_PATH} 不存在，跳过同步')
        return

    if not INDEX_PATH.exists():
        print(f'❌ {INDEX_PATH} 不存在！')
        exit(1)

    # 1. 读取 video_urls.json
    with open(VIDEO_URLS_PATH, encoding='utf-8') as f:
        urls = json.load(f)
    print(f'读取 {VIDEO_URLS_PATH}: {len(urls)} 场比赛')

    # 2. 构造新的内嵌数据（紧凑 JSON 数组）
    new_inline = json.dumps(urls, ensure_ascii=False, separators=(',', ':'))

    # 3. 读取 index.html
    with open(INDEX_PATH, encoding='utf-8') as f:
        html = f.read()

    # 4. 替换 video-url-data 块
    pattern = r'(<script id="video-url-data" type="application/json">)\[[\s\S]*?\]([\s\n]*</script>)'
    replacement = f'\\1{new_inline}\\2'

    new_html, count = re.subn(pattern, replacement, html)
    if count == 0:
        print('❌ 未找到 <script id="video-url-data"> 标签！')
        exit(1)

    # 5. 写回
    with open(INDEX_PATH, 'w', encoding='utf-8') as f:
        f.write(new_html)

    print(f'✅ 已同步 {len(urls)} 场比赛的内嵌视频 URL 到 {INDEX_PATH}')


if __name__ == '__main__':
    main()
