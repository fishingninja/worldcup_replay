#!/usr/bin/env python3
"""将 video_urls.json 合并到 index.html 内嵌 <script id="video-url-data"> 数据。
关键逻辑：保护手动填入的非空 video_urls，不被 GHA 的空数据覆盖。
"""
import json
import re
from pathlib import Path

VIDEO_URLS_PATH = Path('video_urls.json')
INDEX_PATH = Path('index.html')


def main():
    if not VIDEO_URLS_PATH.exists():
        print(f'⚠️  {VIDEO_URLS_PATH} 不存在，跳过同步')
        return

    if not INDEX_PATH.exists():
        print(f'❌ {INDEX_PATH} 不存在！')
        exit(1)

    # 1. 读取 video_urls.json（GHA 生成的新数据，大多为空）
    with open(VIDEO_URLS_PATH, encoding='utf-8') as f:
        new_data = json.load(f)
    print(f'读取 {VIDEO_URLS_PATH}: {len(new_data)} 条')

    # 2. 读取 index.html
    with open(INDEX_PATH, encoding='utf-8') as f:
        html = f.read()

    # 3. 提取现有的内嵌数据
    match = re.search(
        r'<script id="video-url-data" type="application/json">\n?(\[[\s\S]*?\])\n?\s*</script>',
        html
    )
    if not match:
        print('❌ 未找到 <script id="video-url-data"> 标签！')
        exit(1)

    existing_data = json.loads(match.group(1))
    print(f'现有内嵌数据: {len(existing_data)} 条')

    # 4. 合并逻辑：以现有非空 URL 为准，只有现有为空时才用新数据
    preserved = 0
    updated = 0
    merged = {}
    for entry in existing_data:
        nid = entry.get('note_id')
        merged[nid] = entry

    for entry in new_data:
        nid = entry.get('note_id')
        if nid in merged:
            existing_urls = merged[nid].get('video_urls', [])
            new_urls = entry.get('video_urls', [])
            if existing_urls and len(existing_urls) > 0:
                preserved += 1
            elif new_urls and len(new_urls) > 0:
                merged[nid] = entry
                updated += 1
        elif entry.get('video_urls') and len(entry.get('video_urls', [])) > 0:
            merged[nid] = entry
            updated += 1

    merged_list = list(merged.values())
    print(f'合并结果: 保留 {preserved} 条现有非空, 新增/更新 {updated} 条')

    # 5. 构造新的内嵌数据并替换
    new_inline = json.dumps(merged_list, ensure_ascii=False, separators=(',', ':'))
    new_html = html[:match.start(1)] + new_inline + html[match.end(1):]

    with open(INDEX_PATH, 'w', encoding='utf-8') as f:
        f.write(new_html)

    print(f'✅ 已合并 {len(merged_list)} 条内嵌视频 URL 到 {INDEX_PATH}')


if __name__ == '__main__':
    main()
