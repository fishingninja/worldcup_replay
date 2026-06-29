#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从 XHS 实时日历数据生成/更新完整赛程（含淘汰赛），
替代仅含小组赛的硬编码 generate_full_schedule.py。

流程：
1. 读取 xhs_debug/calendar_info_raw.json（由 fetch_all_video_urls.py 保存的实时赛程）
2. 读取现有 matches.json（保留已有回放链接、xhsNoteId 等）
3. 合并生成包含所有阶段的完整赛程（小组赛 + 1/16决赛 + 1/8决赛 + ... + 决赛）
4. 写入 matches.json
5. 同步到 index.html
"""

import json
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

# ── 球队旗帜映射（与 generate_full_schedule.py 保持一致）──
FLAGS = {
    "墨西哥": "🇲🇽", "南非": "🇿🇦", "韩国": "🇰🇷", "捷克": "🇨🇿",
    "加拿大": "🇨🇦", "波黑": "🇧🇦", "卡塔尔": "🇶🇦", "瑞士": "🇨🇭",
    "巴西": "🇧🇷", "摩洛哥": "🇲🇦", "海地": "🇭🇹",
    "苏格兰": "🏴󠁧󠁭󠁯󠁰󠁿",
    "美国": "🇺🇸", "巴拉圭": "🇵🇾", "澳大利亚": "🇦🇺", "土耳其": "🇹🇷",
    "德国": "🇩🇪", "库拉索": "🇨🇼", "科特迪瓦": "🇨🇮", "厄瓜多尔": "🇪🇨",
    "荷兰": "🇳🇱", "日本": "🇯🇵", "瑞典": "🇸🇪", "突尼斯": "🇹🇳",
    "比利时": "🇧🇪", "埃及": "🇪🇬", "伊朗": "🇮🇷", "新西兰": "🇳🇿",
    "西班牙": "🇪🇸", "佛得角": "🇨🇻", "沙特阿拉伯": "🇸🇦", "乌拉圭": "🇺🇾",
    "法国": "🇫🇷", "塞内加尔": "🇸🇳", "伊拉克": "🇮🇶", "挪威": "🇳🇴",
    "阿根廷": "🇦🇷", "阿尔及利亚": "🇩🇿", "奥地利": "🇦🇹", "约旦": "🇯🇴",
    "葡萄牙": "🇵🇹", "刚果（金）": "🇨🇩",
    "刚果民主共和国": "🇨🇩",  # 别名
    "乌兹别克斯坦": "🇺🇿", "哥伦比亚": "🇨🇴",
    "英格兰": "🏴󠁧󠁢󠁥󠁮󠁧󠁿", "克罗地亚": "🇭🇷", "加纳": "🇬🇭", "巴拿马": "🇵🇦",
}

# 旗帜 emoji 正则（与 fetch_all_video_urls.py 一致）
FLAG_EMOJI_RE = re.compile(r'[\U0001F1E6-\U0001F1FF\U0001F3F4\U000E0020-\U000E007F]+')


def strip_flags(text):
    """去掉旗帜 emoji，返回纯文本"""
    return FLAG_EMOJI_RE.sub('', text).strip()


def flag(team):
    """给队名加旗帜 emoji"""
    f = FLAGS.get(team, "")
    return f"{f} {team}" if f else team


def normalize_date_label(label):
    """去掉 date_label 的前导零，如 '06月12日' → '6月12日'"""
    return re.sub(r'0(\d)', r'\1', label)


def ts_to_kickoff(match_time):
    """将 Unix 时间戳（秒）转为 ISO 8601 北京时间字符串"""
    dt = datetime.fromtimestamp(match_time, tz=timezone(timedelta(hours=8)))
    return dt.isoformat()


def get_group_label(match):
    """获取合适的组/轮次标签：小组赛用 group_label，淘汰赛用 round_stage"""
    gl = match.get('group_label', '') or ''
    rs = match.get('round_stage', '') or ''
    # 如果 group_label 是小组（如 A组/B组...）或是纯数字，用它
    # 否则 fallback 到 round_stage
    if gl and ('组' in gl or '决赛' in gl or '赛' in gl):
        return gl
    return rs


def load_calendar(path):
    """加载 XHS 日历信息"""
    if not path.exists():
        print(f'❌ 找不到日历数据: {path}')
        print('   请先运行 fetch_all_video_urls.py')
        sys.exit(1)
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def load_existing_matches(path):
    """加载已有的 matches.json，返回按 (cleanA, cleanB) 索引的 dict"""
    if not path.exists():
        print('⚠️  无现有 matches.json，将从零构建')
        return {}
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    result = {}
    for m in data:
        a = strip_flags(m.get('teamA', ''))
        b = strip_flags(m.get('teamB', ''))
        if a and b:
            result[(a, b)] = m
    print(f'📖 读取现有 matches.json: {len(data)} 条')
    return result


def build_schedule(cal, existing):
    """从日历数据构建完整赛程"""
    days = cal.get('data', {}).get('calendar_list', [])
    if not days:
        print('❌ 日历数据为空')
        sys.exit(1)

    matches = []
    seen = set()  # 去重

    for day in days:
        date_label = normalize_date_label(day.get('date_label', ''))
        for m in day.get('matches', []):
            home = m.get('home_team_name', '').strip()
            away = m.get('away_team_name', '').strip()

            # 跳过待定比赛
            if '待定' in home or '待定' in away:
                continue

            # 去重（同一日期+同一对阵视为重复）
            dedup_key = (date_label, home, away)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

            # 从现有数据中找对应条目
            existing_entry = existing.get((home, away)) or existing.get((away, home))

            # 转换 kickoff 时间
            match_time = m.get('match_time')
            kickoff = ts_to_kickoff(match_time) if match_time else ''

            # 组别/轮次
            group = get_group_label(m)

            entry = {
                'date': date_label,
                'kickoff': kickoff,
                'teamA': flag(home),
                'teamB': flag(away),
                'cctvUrl': existing_entry.get('cctvUrl', '') if existing_entry else '',
                'miguUrl': existing_entry.get('miguUrl', '') if existing_entry else '',
                'verified': existing_entry.get('verified', False) if existing_entry else False,
                'group': group,
            }

            # 保留 xhsNoteId（如果有）
            if existing_entry and existing_entry.get('xhsNoteId'):
                entry['xhsNoteId'] = existing_entry['xhsNoteId']
            else:
                # 从日历 live_info 中取
                li = m.get('live_info', {})
                nid = li.get('replay_note_id', '')
                if nid:
                    entry['xhsNoteId'] = nid

            matches.append(entry)

    # 按 kickoff 时间排序
    matches.sort(key=lambda x: x['kickoff'])

    return matches


def sync_to_html(matches, hp):
    """同步到 index.html"""
    if not hp.exists():
        print('⚠️  index.html 不存在，跳过同步')
        return

    html = hp.read_text(encoding='utf-8')

    json_str = json.dumps(matches, ensure_ascii=False, indent=2)
    pattern = r'(<script\s+id=["\']match-data["\']\s*type=["\']application/json["\']\s*>)(.*?)(</script>)'
    replacement = r'\1\n' + json_str + r'\n\3'
    new_html = re.sub(pattern, replacement, html, flags=re.DOTALL)
    hp.write_text(new_html, encoding='utf-8')
    print('✅ [同步] index.html 已更新 match-data')


def print_summary(matches):
    """打印赛程总览"""
    print(f'\n=== 赛程总览: {len(matches)} 场比赛 ===')

    # 按阶段统计
    from collections import Counter
    stages = Counter(m.get('group', '未分组') for m in matches)
    print('阶段分布:')
    for stage, count in sorted(stages.items()):
        print(f'  {stage}: {count}场')

    # 已有回放链接统计
    has_cctv = sum(1 for m in matches if m.get('cctvUrl'))
    has_xhs = sum(1 for m in matches if m.get('xhsNoteId'))
    print(f'\n  已有央视回放: {has_cctv}/{len(matches)}')
    print(f'  已有小红书回放: {has_xhs}/{len(matches)}')

    # 按日统计
    from collections import defaultdict
    by_date = defaultdict(list)
    for m in matches:
        by_date[m['date']].append(m)
    print('\n每日安排:')
    def date_sort_key(d):
        parts = d.replace('月', '/').replace('日', '').split('/')
        m = int(parts[0]) if parts[0] else 0
        day = int(parts[1]) if len(parts) > 1 else 0
        return m * 100 + day
    for d in sorted(by_date.keys(), key=date_sort_key):
        date_matches = by_date[d]
        # 取前3个展示
        samples = [f"{m['teamA']} vs {m['teamB']}" for m in date_matches[:3]]
        suffix = f' ... 等{len(date_matches)}场' if len(date_matches) > 3 else ''
        print(f'  {d}: {", ".join(samples)}{suffix}')


def main():
    repo = Path(__file__).parent
    cal_path = repo / 'xhs_debug' / 'calendar_info_raw.json'
    matches_path = repo / 'matches.json'
    html_path = repo / 'index.html'

    print('=' * 50)
    print('从 XHS 实时数据生成完整赛程')
    print('=' * 50)

    # 1. 加载日历数据
    cal = load_calendar(cal_path)

    # 2. 加载现有比赛（保留已有链接）
    existing = load_existing_matches(matches_path)

    # 3. 构建完整赛程
    matches = build_schedule(cal, existing)

    if not matches:
        print('❌ 未生成任何比赛（所有对阵都是待定？）')
        sys.exit(1)

    # 4. 写入 matches.json
    with open(matches_path, 'w', encoding='utf-8') as f:
        json.dump(matches, f, ensure_ascii=False, indent=2)
    print(f'✅ [写入] {matches_path}')

    # 5. 同步到 index.html
    sync_to_html(matches, html_path)

    # 6. 打印总览
    print_summary(matches)

    print(f'\n🎉 完成！共 {len(matches)} 场比赛已写入赛程')


if __name__ == '__main__':
    main()
