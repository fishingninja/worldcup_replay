"""从 all_video_urls.json 生成精简的 video_urls.json（给前端用）
双URL兜底：每个note保留最多2个不同CDN域名的URL"""
import json
import re
from pathlib import Path
from datetime import datetime, timezone, timedelta

# 北京时间
tz = timezone(timedelta(hours=8))
now = datetime.now(tz).strftime('%Y-%m-%dT%H:%M:%S+08:00')

input_path = Path('xhs_debug/all_video_urls.json')
output_path = Path('video_urls.json')

if not input_path.exists():
    print(f'输入文件不存在: {input_path}')
    exit(1)

with open(input_path, encoding='utf-8') as f:
    data = json.load(f)


def extract_cdn_domain(url):
    """从URL中提取CDN域名标识"""
    m = re.search(r'sns-video-(\w+)-m\.xhscdn\.com', url)
    return m.group(1) if m else 'unknown'


output = []
for item in data:
    urls = item.get('video_urls', [])
    # 按CDN域名去重，保留最多2个不同域名的URL
    seen_domains = set()
    unique_urls = []
    for u in urls:
        domain = extract_cdn_domain(u)
        if domain not in seen_domains and len(unique_urls) < 2:
            unique_urls.append(u)
            seen_domains.add(domain)
    output.append({
        'note_id': item['note_id'],
        'video_urls': unique_urls,
        'updated_at': now
    })

with open(output_path, 'w', encoding='utf-8') as f:
    json.dump(output, f, ensure_ascii=False, indent=2)

print(f'已生成 {output_path}，包含 {len(output)} 场比赛的视频URL（每场最多2个CDN域名）')
