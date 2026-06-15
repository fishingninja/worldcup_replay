"""从 all_video_urls.json 生成精简的 video_urls.json（给前端用）"""
import json

with open(r'E:\project\worldcup_replay\xhs_debug\all_video_urls.json', encoding='utf-8') as f:
    data = json.load(f)

output = []
for item in data:
    output.append({
        'note_id': item['note_id'],
        'video_urls': item.get('video_urls', []),
        'updated_at': '2026-06-15T20:37:00+08:00'
    })

with open(r'E:\project\worldcup_replay\video_urls.json', 'w', encoding='utf-8') as f:
    json.dump(output, f, ensure_ascii=False, indent=2)

print(f"已生成 video_urls.json，包含 {len(output)} 场比赛的视频URL")
