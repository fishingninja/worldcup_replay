#!/usr/bin/env python3
"""从 XHS worldcup26 页面的 SSR 数据中提取赛程信息（无需登录、无需浏览器）。"""
import urllib.request, json, re, sys
from pathlib import Path

URL = 'https://www.xiaohongshu.com/worldcup26?channel_id=&channel_type=explore_feed'

def fetch_initial_state():
    """获取页面 HTML 并提取 window.__INITIAL_STATE__"""
    req = urllib.request.Request(URL, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/149.0.0.0 Safari/537.36 Edg/149.0.0.0',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    })
    resp = urllib.request.urlopen(req, timeout=30)
    html = resp.read().decode('utf-8', errors='replace')
    print(f'  页面大小: {len(html)} bytes', flush=True)

    # 找到 window.__INITIAL_STATE__
    start = html.find('window.__INITIAL_STATE__')
    if start < 0:
        print('  ❌ 未找到 window.__INITIAL_STATE__', flush=True)
        return None
    
    eq = html.find('=', start)
    # 找到 </script> 结束位置
    script_end = html.find('</script>', eq)
    if script_end < 0:
        print('  ❌ 未找到 script 结束标记', flush=True)
        return None
    
    raw = html[eq + 1:script_end]
    print(f'  原始数据大小: {len(raw)} bytes', flush=True)
    
    # SSR 数据中可能含 JS 特有语法（undefined, 尾逗号），修复为合法 JSON
    raw = re.sub(r':undefined(?=[,}])', ':null', raw)
    raw = re.sub(r',\s*\}', '}', raw)  # 尾逗号
    raw = re.sub(r',\s*\]', ']', raw)  # 尾逗号（数组）
    
    try:
        data = json.loads(raw)
        print(f'  ✅ JSON 解析成功', flush=True)
        return data
    except json.JSONDecodeError as e:
        print(f'  ❌ JSON 解析失败: {e}', flush=True)
        return None


def extract_worldcup_data(data):
    """从 INITIAL_STATE 中提取世界杯相关数据"""
    if not data:
        return None
    
    print(f'  顶层 keys: {list(data.keys())}', flush=True)
    
    # 遍历查找 worldcup/calendar 相关数据
    results = {}
    
    def search(obj, path=''):
        if isinstance(obj, dict):
            for k, v in obj.items():
                p = f'{path}.{k}' if path else k
                # 查找关键字
                if any(keyword in k.lower() for keyword in ['worldcup', 'world_cup', 'calendar', 'match', 'schedule', 'replay', 'video']):
                    if isinstance(v, (dict, list)):
                        results[p] = v
                    else:
                        results[p] = str(v)[:200]
                # 深度遍历有限深度
                if len(path.split('.')) < 5:
                    search(v, p)
        elif isinstance(obj, list) and len(path.split('.')) < 5:
            for i, item in enumerate(obj):
                search(item, f'{path}[{i}]')
    
    search(data)
    
    # 按路径排序输出
    print(f'\n  找到 {len(results)} 个相关字段:\n', flush=True)
    for path, val in sorted(results.items()):
        if isinstance(val, list):
            print(f'    {path}: [{len(val)} items]', flush=True)
        elif isinstance(val, dict):
            print(f'    {path}: ({len(val)} keys) {list(val.keys())[:8]}', flush=True)
        else:
            print(f'    {path}: {val[:120]}...' if len(str(val)) > 120 else f'    {path}: {val}', flush=True)
    
    return results


if __name__ == '__main__':
    print('>>> 从 SSR 数据提取赛程...', flush=True)
    data = fetch_initial_state()
    if data:
        results = extract_worldcup_data(data)
        # 保存完整数据供调试
        out = Path('xhs_debug/ssr_initial_state.json')
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'\n  完整 SSR 已保存: {out}', flush=True)
    else:
        sys.exit(1)
