#!/usr/bin/env python3
"""
update_matches.py - 从央视体育抓取最新世界杯回放链接，更新 matches.json 和 index.html
用法：python update_matches.py
"""

import json
import re
import sys
import os
import time
from datetime import datetime, timezone, timedelta

try:
    from urllib.request import urlopen, Request
    from urllib.error import URLError, HTTPError
    from urllib.parse import quote
except ImportError:
    from urllib2 import urlopen, Request, URLError, HTTPError
    from urllib import quote

# ── 配置 ────────────────────────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MATCHES_JSON = os.path.join(SCRIPT_DIR, 'matches.json')
HTML_FILE    = os.path.join(SCRIPT_DIR, 'index.html')
CCTV_SCHEDULE_URL = 'https://sports.cctv.com/2026/schedule/index.shtml'

# 北京时间时区（UTC+8）
CST = timezone(timedelta(hours=8))

# ── 已有比赛数据（基础数据，只补充新比赛） ────────────────────────────────
BASE_MATCHES = [
    {"date":"6月15日","kickoff":"2026-06-15T01:00:00+08:00","teamA":"🇩🇪 德国","teamB":"🇨🇼 库拉索","cctvUrl":"https://sports.cctv.com/2026/06/15/VIDE6jG8Ys7H2mQbPxA7xM260615.shtml","miguUrl":"","verified":True},
    {"date":"6月14日","kickoff":"2026-06-14T12:00:00+08:00","teamA":"🇦🇺 澳大利亚","teamB":"🇹🇷 土耳其","cctvUrl":"https://sports.cctv.com/2026/06/14/VIDEPqw4902JL00BxqBqhNhQ260614.shtml","miguUrl":"","verified":False},
    {"date":"6月14日","kickoff":"2026-06-14T09:00:00+08:00","teamA":"🇭🇹 海地","teamB":"🏴󠁧󠁭󠁯󠁰󠁿 苏格兰","cctvUrl":"https://sports.cctv.com/2026/06/14/VIDEl4MKG9oQahuI30GQm9ZE260614.shtml","miguUrl":"","verified":False},
    {"date":"6月14日","kickoff":"2026-06-14T06:00:00+08:00","teamA":"🇧🇷 巴西","teamB":"🇲🇦 摩洛哥","cctvUrl":"https://sports.cctv.com/2026/06/14/VIDEd5TLQj9bzFTDbQM5cjI2260614.shtml","miguUrl":"https://www.miguvideo.com/p/detail/965306581","verified":True},
    {"date":"6月14日","kickoff":"2026-06-14T03:00:00+08:00","teamA":"🇶🇦 卡塔尔","teamB":"🇨🇭 瑞士","cctvUrl":"https://sports.cctv.com/2026/06/14/VIDE65YyIeBEzTpIrdro7HTj260614.shtml","miguUrl":"","verified":False},
    {"date":"6月13日","kickoff":"2026-06-13T12:00:00+08:00","teamA":"🇺🇸 美国","teamB":"🇵🇾 巴拉圭","cctvUrl":"https://sports.cctv.com/2026/06/13/VIDEmmuqQcFu5vHN6bQFCshI260613.shtml","miguUrl":"","verified":True},
    {"date":"6月13日","kickoff":"2026-06-13T06:00:00+08:00","teamA":"🇨🇦 加拿大","teamB":"🇧🇦 波黑","cctvUrl":"https://sports.cctv.com/2026/06/13/VIDEILwRhaFlRNrec13HXu0b260613.shtml","miguUrl":"","verified":True},
    {"date":"6月12日","kickoff":"2026-06-12T09:00:00+08:00","teamA":"🇰🇷 韩国","teamB":"🇨🇿 捷克","cctvUrl":"https://sports.cctv.com/2026/06/12/VIDEw0mPt3PccqSZ1GkIPKUS260612.shtml","miguUrl":"","verified":True},
    {"date":"6月11日","kickoff":"2026-06-11T12:00:00+08:00","teamA":"🇲🇽 墨西哥","teamB":"🇿🇦 南非","cctvUrl":"https://sports.cctv.com/2026/06/12/VIDEqZhlibbbqE64UwKAcj1g260612.shtml","miguUrl":"","verified":True},
]

# ── 工具函数 ─────────────────────────────────────────────────────────────────

def log(msg):
    """打印带时间戳的日志"""
    print('[' + datetime.now().strftime('%H:%M:%S') + '] ' + msg, flush=True)


def fetch_url(url, timeout=15, encoding=None):
    """
    抓取URL内容，返回字符串。
    优先使用指定的 encoding，否则自动检测（UTF-8 → GBK）。
    """
    try:
        req = Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9',
        })
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if encoding:
                return raw.decode(encoding, errors='replace')
            for enc in ('utf-8', 'gbk', 'gb2312', 'gb18030', 'iso-8859-1'):
                try:
                    return raw.decode(enc)
                except UnicodeDecodeError:
                    continue
            return raw.decode('utf-8', errors='replace')
    except Exception as e:
        log('抓取失败 ' + url + ': ' + str(e))
        return ''


def verify_cctv_url(url):
    """
    验证央视回放链接是否有效。
    返回 True 如果链接可访问且包含视频播放器。
    """
    try:
        req = Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        })
        with urlopen(req, timeout=10) as resp:
            if resp.status != 200:
                return False
            # 读取前 4096 字节判断是否有播放器
            raw = resp.read(4096)
            text = raw.decode('utf-8', errors='replace')
            # 央视播放器关键词
            if any(kw in text for kw in ['player', 'video', 'CNTV', 'cctv']):
                return True
            return False
    except Exception:
        return False


def search_cctv_replay(team_a, team_b, match_date):
    """
    通过央视搜索查找回放链接。
    尝试多种搜索策略，返回找到的链接列表。
    """
    results = []
    
    # 策略1：搜索 "A vs B 完整回放"
    keywords = [
        team_a.replace('🇺🇸 ', '').replace('🇵🇾 ', '') + 'vs' + team_b.replace('🇺🇸 ', '').replace('🇵🇾 ', '') + '完整回放',
        team_a.replace('🇺🇸 ', '').replace('🇵🇾 ', '') + ' ' + team_b.replace('🇺🇸 ', '').replace('🇵🇾 ', '') + '世界杯',
    ]
    
    for keyword in keywords:
        try:
            # 尝试央视搜索接口（如果存在 JSON API）
            search_url = 'https://search.cctv.com/search.php?qtext=' + quote(keyword) + '&type=video'
            html = fetch_url(search_url, timeout=10)
            if not html:
                continue
            
            # 从页面 HTML 中提取 VIDE 链接
            links = re.findall(r'https?://sports\.cctv\.com/2026/\d{2}/\d{2}/VIDE[a-zA-Z0-9]+\.shtml', html)
            for link in links:
                if link not in results:
                    results.append(link)
        except Exception as e:
            log('搜索失败 (' + keyword + '): ' + str(e))
    
    return results


def guess_cctv_url_by_date(match_date, team_a, team_b):
    """
    根据比赛日期推测央视回放链接。
    CCTV 链接格式：sports.cctv.com/YYYY/MM/DD/VIDE[ID].shtml
    如果已知同日期其他比赛的链接，可以推测 ID 规律。
    """
    # 从已有比赛中找同日期的链接，尝试推测规律
    # 目前 CCTV 的 VIDE ID 是随机的，无法推测
    # 但可以通过搜索来找到
    return search_cctv_replay(team_a, team_b, match_date)


def load_existing_matches():
    """加载已有的 matches.json，如果不存在则返回 BASE_MATCHES"""
    if os.path.exists(MATCHES_JSON):
        try:
            with open(MATCHES_JSON, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            log('读取 matches.json 失败: ' + str(e))
    return list(BASE_MATCHES))


def save_matches(matches):
    """保存 matches.json"""
    with open(MATCHES_JSON, 'w', encoding='utf-8') as f:
        json.dump(matches, f, ensure_ascii=False, indent=2)
    log('已更新 matches.json（' + str(len(matches)) + ' 场比赛）')


def update_html_with_matches(matches):
    """将 matches 数据写入 HTML 文件的 <script id="match-data"> 标签内"""
    if not os.path.exists(HTML_FILE):
        log('HTML 文件不存在: ' + HTML_FILE)
        return

    # 生成紧凑 JSON（一行一个比赛，方便人类阅读）
    json_lines = json.dumps(matches, ensure_ascii=False, indent=2)
    new_tag = '<script id="match-data" type="application/json">\n' + json_lines + '\n</script>'

    with open(HTML_FILE, 'r', encoding='utf-8') as f:
        html = f.read()

    # 替换 <script id="match-data"> ... </script> 部分
    pattern = r'<script id="match-data" type="application/json">[\s\S]*?</script>'
    new_html = re.sub(pattern, new_tag, html)

    if new_html == html:
        log('警告：未在 HTML 中找到 <script id="match-data"> 标签，未更新 HTML')
        return

    with open(HTML_FILE, 'w', encoding='utf-8') as f:
        f.write(new_html)
    log('已更新 HTML 文件（' + str(len(matches)) + ' 场比赛）')


def build_match_key(match):
    """用日期+两队名生成唯一 key，用于去重"""
    return match.get('kickoff', '') + '|' + match.get('teamA', '') + '|' + match.get('teamB', '')


def is_match_finished(kickoff_str):
    """判断比赛是否已结束（开球时间 + 2.5小时 < 当前时间）"""
    try:
        # 解析 kickoff 时间
        dt = datetime.fromisoformat(kickoff_str.replace('+08:00', ''))
        dt = dt.replace(tzinfo=CST)
        end_time = dt + timedelta(hours=2.5)
        now = datetime.now(CST)
        return end_time < now
    except Exception:
        return False


def main():
    log('=== 开始更新世界杯回放数据 ===')

    # 1. 加载已有数据
    existing = load_existing_matches()
    existing_keys = set(build_match_key(m) for m in existing)
    all_matches = list(existing)

    # 2. 尝试从央视赛程页抓取新比赛
    log('正在抓取央视赛程页...')
    schedule_html = fetch_url(CCTV_SCHEDULE_URL, timeout=20)
    
    if schedule_html:
        # 提取所有 sports.cctv.com 回放链接
        all_links = re.findall(r'https?://sports\.cctv\.com/2026/\d{2}/\d{2}/VIDE[a-zA-Z0-9]+\.shtml', schedule_html)
        unique_links = list(set(all_links))
        log('赛程页中找到 ' + str(len(unique_links)) + ' 个不重复回放链接')
        
        # 验证已有比赛的链接是否有效
        for m in all_matches:
            if not m.get('verified') and m.get('cctvUrl') in unique_links:
                m['verified'] = True
                log('验证通过: ' + m['teamA'] + ' vs ' + m['teamB'])
    else:
        log('央视赛程页抓取失败，将尝试其他数据源...')
    
    # 3. TODO: 从其他数据源（如 FIFA 官网、体育新闻站）获取新比赛列表
    # 目前暂时手动添加新比赛到 BASE_MATCHES
    
    # 4. 对于未验证的比赛，尝试验证链接是否有效
    for m in all_matches:
        if not m.get('verified') and m.get('cctvUrl'):
            if verify_cctv_url(m['cctvUrl']):
                m['verified'] = True
                log('链接验证通过: ' + m['teamA'] + ' vs ' + m['teamB'])
    
    # 5. 保存结果
    # 按开球时间倒序排序
    all_matches.sort(key=lambda x: x.get('kickoff', ''), reverse=True)
    save_matches(all_matches)
    update_html_with_matches(all_matches)

    log('=== 更新完成 ===')
    
    # 统计信息
    finished = [m for m in all_matches if is_match_finished(m.get('kickoff', ''))]
    verified = [m for m in all_matches if m.get('verified')]
    log('总比赛数: ' + str(len(all_matches)))
    log('已完赛: ' + str(len(finished)))
    log('已验证链接: ' + str(len(verified)))
    
    return 0


if __name__ == '__main__':
    sys.exit(main())
