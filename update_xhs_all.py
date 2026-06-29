#!/usr/bin/env python3
"""一键更新 XHS 视频回放数据并推送到 GitHub。

流程（5步）：
1. 抓取 XHS 视频URL（同步保存实时赛程原始数据）
2. 从实时赛程生成完整赛程（小组赛+淘汰赛）→ 更新 matches.json 和 index.html
3. 生成 video_urls.json
4. 同步到 index.html
5. 推送到 GitHub

需要在本地（中国IP）运行，因为 GHA 在境外无法访问小红书。
"""
import subprocess
import sys
from pathlib import Path

PYTHON = sys.executable
STEPS = [
    ('1/5 抓取 XHS 视频URL', 'fetch_all_video_urls.py'),
    ('2/5 从实时赛程生成完整 match-data', 'generate_schedule_from_xhs.py'),
    ('3/5 生成 video_urls.json', 'generate_video_urls_json.py'),
    ('4/5 同步到 index.html', 'sync_inline_urls.py'),
    ('5/5 推送到 GitHub', 'push_api.py'),
]

failed = False
for label, script in STEPS:
    print(f'\n{"="*50}')
    print(f'>>> {label}')
    print(f'{"="*50}')
    result = subprocess.run([PYTHON, script], capture_output=False)
    if result.returncode != 0:
        print(f'❌ {label} 失败 (exit code {result.returncode})')
        if script == 'fetch_all_video_urls.py':
            print('   XHS 抓取失败，后续步骤跳过')
            failed = True
            break
        elif script == 'push_api.py':
            print('   推送失败，请检查网络')
            failed = True
            break
        else:
            print('   ⚠️ 继续执行下一步')
    else:
        print(f'✅ {label} 完成')

if not failed:
    print(f'\n{"="*50}')
    print('🎉 全部完成！线上数据已更新。')
    print(f'{"="*50}')
