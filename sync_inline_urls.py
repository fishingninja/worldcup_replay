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

    # 4. 合并逻辑：新数据优先（XHS 视频 URL 约 10 分钟过期，新数据更新鲜）
    # 保护机制：如果新数据全部为空（可能抓取失败），则完全跳过，保留现有数据
    non_empty_new = sum(1 for e in new_data if e.get('video_urls'))
    if non_empty_new == 0:
        print(f'⚠️ 新数据全部为空，跳过同步（保留现有 {len(existing_data)} 条数据）')
        return

    merged = {}
    updated = 0
    new_added = 0
    empty_kept = 0

    for entry in new_data:
        nid = entry.get('note_id')
        new_urls = entry.get('video_urls', [])
        if new_urls and len(new_urls) > 0:
            merged[nid] = entry
            updated += 1
        elif nid in {e.get('note_id') for e in existing_data}:
            # 新数据为空，保留现有数据（无论现有是否为空）
            for old in existing_data:
                if old.get('note_id') == nid:
                    merged[nid] = old
                    empty_kept += 1
                    break
        else:
            merged[nid] = entry
            new_added += 1

    merged_list = list(merged.values())
    print(f'合并结果: 更新 {updated} 条, 保留空 {empty_kept} 条, 新增 {new_added} 条, '
          f'总计 {len(merged_list)} 条, 新数据非空 {non_empty_new}/{len(new_data)}')

    # 5. 构造新的内嵌数据并替换
    new_inline = json.dumps(merged_list, ensure_ascii=False, separators=(',', ':'))
    new_html = html[:match.start(1)] + new_inline + html[match.end(1):]

    with open(INDEX_PATH, 'w', encoding='utf-8') as f:
        f.write(new_html)

    print(f'✅ 已合并 {len(merged_list)} 条内嵌视频 URL 到 {INDEX_PATH}')


if __name__ == '__main__':
    main()
