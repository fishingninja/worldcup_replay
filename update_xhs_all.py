#!/usr/bin/env python3
"""一键更新 XHS 视频回放数据并推送到 GitHub。

赛事已于 2026-07-20 结束，赛程定稿（104 场）。
默认运行「仅刷新模式」：只定期刷新时间最新的最后 N 场视频URL
（视频 CDN 签名会过期，需定期刷新），其余比赛保留已有数据，
不再从实时赛程重新发现/抓取「新比赛」。

流程（4步，默认）：
1. 刷新最后 N 场 XHS 视频URL（--last N，默认 3）
2. 生成 video_urls.json
3. 同步到 index.html
4. 推送到 GitHub

手动全量模式（--full，仅用于一次性回溯）：
1. 抓取 XHS 视频URL（同步保存实时赛程原始数据）
2. 从实时赛程生成完整 match-data
3. 生成 video_urls.json
4. 同步到 index.html
5. 推送到 GitHub

需要在本地（中国IP）运行，因为 GHA 在境外无法访问小红书。
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

# 强制使用真实 Edge 配置模式抓取：XHS cookie 为 App-Bound Encryption(v20)，
# 无法导出成明文注入无登录态 Chromium；改用真实 msedge.exe + 用户配置目录
# （通过 junction 避开默认目录的 DevTools 限制）自带登录态抓取，才能拿到
# 带有效签名的新鲜视频URL。cookies.json 已永久失效，此开关为唯一可行路径。
os.environ["XHS_USE_EDGE_PROFILE"] = "1"

PYTHON = sys.executable


def run(label, script, *args):
    print(f'\n{"="*50}')
    print(f'>>> {label}')
    print(f'{"="*50}')
    result = subprocess.run([PYTHON, script, *args], capture_output=False)
    if result.returncode != 0:
        print(f'❌ {label} 失败 (exit code {result.returncode})')
        return False
    print(f'✅ {label} 完成')
    return True


def main():
    parser = argparse.ArgumentParser(description='更新 XHS 世界杯回放数据')
    parser.add_argument('--full', action='store_true',
                        help='全量模式：按原 5 步流程抓取全部比赛并重新生成赛程（手动回溯用）')
    parser.add_argument('--last', type=int, default=3,
                        help='仅刷新模式：只刷新最后 N 场（默认 3）')
    args = parser.parse_args()

    if args.full:
        # ── 原 5 步全量流程（手动回溯）──
        steps = [
            ('1/5 抓取 XHS 视频URL', 'fetch_all_video_urls.py'),
            ('2/5 从实时赛程生成完整 match-data', 'generate_schedule_from_xhs.py'),
            ('3/5 生成 video_urls.json', 'generate_video_urls_json.py'),
            ('4/5 同步到 index.html', 'sync_inline_urls.py'),
            ('5/5 推送到 GitHub', 'push_api.py'),
        ]
        for label, script in steps:
            if not run(label, script):
                if script in ('fetch_all_video_urls.py', 'push_api.py'):
                    print('关键步骤失败，终止。')
                    sys.exit(1)
                else:
                    print('   ⚠️ 继续执行下一步')
    else:
        # ── 默认：仅刷新最后 N 场（赛事已结束）──
        print(f'\n📌 赛事已结束：仅刷新模式（最后 {args.last} 场）')
        if not run(f'1/4 刷新最后 {args.last} 场 XHS 视频URL',
                   'fetch_all_video_urls.py', '--last', str(args.last)):
            print('❌ 视频刷新失败，终止。')
            sys.exit(1)
        if not run('2/4 生成 video_urls.json', 'generate_video_urls_json.py'):
            sys.exit(1)
        if not run('3/4 同步到 index.html', 'sync_inline_urls.py'):
            sys.exit(1)
        if not run('4/4 推送到 GitHub', 'push_api.py'):
            sys.exit(1)

    print(f'\n{"="*50}')
    print('🎉 全部完成！线上数据已更新。')
    print(f'{"="*50}')


if __name__ == '__main__':
    main()
