#!/usr/bin/env python3
"""
update_matches.py - 从央视体育抓取最新世界杯回放链接，更新 matches.json 和 worldcup-spoiler-free.html
用法：python update_matches.py
"""
import json
import re
import sys
import os
from datetime import datetime, timezone, timedelta

try:
    from urllib.request import urlopen, Request
    from urllib.error import URLError, HTTPError
except ImportError:
    # Python 2 fallback
    from urllib2 import urlopen, Request, URLError, HTTPError

# ── 配置 ────────────────────────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MATCHES_JSON = os.path.join(SCRIPT_DIR, 'matches.json')
HTML_FILE    = os.path.join(SCRIPT_DIR, 'worldcup-spoiler-free.html')
CCTV_SCHEDULE_URL = 'https://sports.cctv.com/2026/schedule/index.shtml'

# 北京时间时区（UTC+8）
CST = timezone(timedelta(hours=8))

# ── 已有比赛数据（作为基础，只补充新比赛） ────────────────────────────────
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
    print('[' + datetime.now().strftime('%H:%M:%S') + '] ' + msg, flush=True)


def fetch_url(url, timeout=15):
    """抓取URL内容，返回字符串（UTF-8或GBK）"""
    try:
        req = Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'text/html,application/xhtml+xml',
        })
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            # 尝试UTF-8，失败则用GBK（央视网站可能用GBK）
            for enc in ('utf-8', 'gbk', 'gb2312', 'iso-8859-1'):
                try:
                    return raw.decode(enc)
                except UnicodeDecodeError:
                    continue
            return raw.decode('utf-8', errors='replace')
    except Exception as e:
        log('抓取失败 ' + url + ': ' + str(e))
        return ''


def guess_cctv_replay_url(match_date_str, title_hint=''):
    """
    推测央视回放链接。
    CCTV的回放链接格式一般为：
      https://sports.cctv.com/YYYY/MM/DD/VIDE[随机].shtml
    这里尝试访问赛程页，从中提取真实链接。
    """
    # 从赛程页找链接
    schedule_html = fetch_url(CCTV_SCHEDULE_URL)
    if not schedule_html:
        return ''

    # 在赛程页HTML中搜索包含日期的sports.cctv.com链接
    # 格式：sports.cctv.com/2026/MM/DD/VIDE....shtml
    mm = match_date_str.split('月')[0].zfill(2)
    pattern = r'https?://sports\.cctv\.com/2026/' + re.escape(mm) + r'/\d{2}/VIDE[a-zA-Z0-9]+\.shtml'
    links = re.findall(pattern, schedule_html)

    # 去重
    seen = set()
    unique_links = []
    for link in links:
        if link not in seen:
            seen.add(link)
            unique_links.append(link)

    log('找到 ' + str(len(unique_links)) + ' 个回放链接（' + match_date_str + '）')
    return unique_links  # 返回所有找到的链接，由调用者匹配


def load_existing_matches():
    """加载已有的matches.json，如果不存在则返回BASE_MATCHES"""
    if os.path.exists(MATCHES_JSON):
        try:
            with open(MATCHES_JSON, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            log('读取matches.json失败: ' + str(e))
    return list(BASE_MATCHES)


def save_matches(matches):
    """保存matches.json"""
    with open(MATCHES_JSON, 'w', encoding='utf-8') as f:
        json.dump(matches, f, ensure_ascii=False, indent=2)
    log('已更新 matches.json（' + str(len(matches)) + ' 场比赛）')


def update_html_with_matches(matches):
    """将matches数据写入HTML文件的 <script id="match-data"> 标签内"""
    if not os.path.exists(HTML_FILE):
        log('HTML文件不存在: ' + HTML_FILE)
        return

    # 生成紧凑JSON（一行一个比赛，方便人类阅读）
    json_lines = json.dumps(matches, ensure_ascii=False, indent=2)
    # 包装成 <script> 标签内容
    new_tag = '<script id="match-data" type="application/json">\n' + json_lines + '\n</script>'

    with open(HTML_FILE, 'r', encoding='utf-8') as f:
        html = f.read()

    # 替换 <script id="match-data"> ... </script> 部分
    pattern = r'<script id="match-data" type="application/json">[\s\S]*?</script>'
    new_html = re.sub(pattern, new_tag, html)

    if new_html == html:
        log('警告：未在HTML中找到 <script id="match-data"> 标签，未更新HTML')
        return

    with open(HTML_FILE, 'w', encoding='utf-8') as f:
        f.write(new_html)
    log('已更新 HTML 文件（' + str(len(matches)) + ' 场比赛）')


def build_date_key(match):
    """用日期+两队名生成唯一key，用于去重"""
    return match.get('kickoff', '') + '|' + match.get('teamA', '') + '|' + match.get('teamB', '')


def main():
    log('=== 开始更新世界杯回放数据 ===')

    # 1. 加载已有数据
    existing = load_existing_matches()
    existing_keys = set(build_date_key(m) for m in existing)
    all_matches = list(existing)

    # 2. 抓取央视赛程页
    log('正在抓取央视赛程页...')
    schedule_html = fetch_url(CCTV_SCHEDULE_URL)
    if schedule_html:
        # 提取所有sports.cctv.com回放链接
        all_links = re.findall(r'https?://sports\.cctv\.com/2026/\d{2}/\d{2}/VIDE[a-zA-Z0-9]+\.shtml', schedule_html)
        log('赛程页中找到 ' + str(len(set(all_links))) + ' 个不重复回放链接')

        # TODO: 如果有新比赛，这里可以解析赛程页的比赛列表，自动添加新的回放链接
        # 目前央视赛程页是JS动态渲染的，静态HTML可能不含完整数据
        # 所以暂时只做：把已有比赛的 verified=false 更新为 true（如果链接在赛程页中出现）
        for m in all_matches:
            if not m.get('verified') and m.get('cctvUrl') in all_links:
                m['verified'] = True
                log('验证通过: ' + m['teamA'] + ' vs ' + m['teamB'])

    # 3. 保存结果
    # 按开球时间排序
    all_matches.sort(key=lambda x: x.get('kickoff', ''), reverse=True)
    save_matches(all_matches)
    update_html_with_matches(all_matches)

    log('=== 更新完成 ===')
    return 0


if __name__ == '__main__':
    sys.exit(main())
