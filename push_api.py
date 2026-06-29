#!/usr/bin/env python3
"""通过 GitHub REST API 推送本地 commit（沙箱 443 端口被封时的备用方案）。

⚠️ 开源前请先在环境变量中设置 GITHUB_TOKEN，不要在代码中硬编码密码。
   本地调试可创建 .env 文件写入 GITHUB_TOKEN=your_token_here
"""
import json, base64, os, sys, urllib.request
from pathlib import Path

# ── 读取 Token（优先环境变量，兼容之前硬编码的旧值）──
TOKEN = os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN') or ''
if not TOKEN:
    # 尝试从 .env 文件读取
    env_path = Path(__file__).parent / '.env'
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith('GITHUB_TOKEN='):
                TOKEN = line.split('=', 1)[1].strip()
                break
if not TOKEN:
    print('❌ 未找到 GITHUB_TOKEN，请设置环境变量 GITHUB_TOKEN 或创建 .env 文件')
    sys.exit(1)

REPO = 'fishingninja/worldcup_replay'
BRANCH = 'master'

def github_api(method, endpoint, data=None):
    url = f'https://api.github.com/repos/{REPO}{endpoint}'
    headers = {
        'Authorization': f'token {TOKEN}',
        'Content-Type': 'application/json',
        'User-Agent': 'worldcup-replay-push'
    }
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())

print('>>> 推送 via GitHub REST API...', flush=True)

# 1. 获取当前 ref
ref_data = github_api('GET', f'/git/ref/heads/{BRANCH}')
base_sha = ref_data['object']['sha']
print(f'  当前 HEAD: {base_sha[:8]}...', flush=True)

# 2. 获取 base commit 的 tree
commit_data = github_api('GET', f'/git/commits/{base_sha}')
base_tree = commit_data['tree']['sha']

# 3. 收集需要推送的源文件（排除 .workbuddy/ 和 xhs_debug/）
repo_root = Path(__file__).parent
EXCLUDE_DIRS = {'.workbuddy', 'xhs_debug', '__pycache__', '.git'}
EXCLUDE_EXTS = {'.pyc', '.pyo'}
EXCLUDE_FILES = {'.env', '*.token'}

files_to_push = []
for f in repo_root.rglob('*'):
    if not f.is_file():
        continue
    rel = f.relative_to(repo_root)
    parts = rel.parts
    # 排除目录
    if any(p in EXCLUDE_DIRS for p in parts):
        continue
    # 排除扩展名
    if f.suffix in EXCLUDE_EXTS:
        continue
    # 排除文件名
    if f.name in EXCLUDE_FILES:
        continue
    files_to_push.append(str(rel))

files_to_push.sort()
print(f'  待推送 {len(files_to_push)} 个文件', flush=True)

# 4. 创建 blobs
blobs = {}
for rel_path in files_to_push:
    content = (repo_root / rel_path).read_bytes()
    encoded = base64.b64encode(content).decode()
    blob_data = github_api('POST', '/git/blobs', {'content': encoded, 'encoding': 'base64'})
    blobs[rel_path] = blob_data['sha']
    print(f'  ✓ blob {rel_path} -> {blobs[rel_path][:8]}...', flush=True)

# 5. 创建 tree
tree_items = [
    {'path': p, 'mode': '100644', 'type': 'blob', 'sha': s}
    for p, s in blobs.items()
]
tree_data = github_api('POST', '/git/trees', {'base_tree': base_tree, 'tree': tree_items})
new_tree = tree_data['sha']
print(f'  ✓ tree {new_tree[:8]}...', flush=True)

# 6. 创建 commit
new_commit_data = github_api('POST', '/git/commits', {
    'message': f'auto: 全量推送项目源码 ({len(files_to_push)} files) [skip ci]',
    'tree': new_tree,
    'parents': [base_sha]
})
new_commit = new_commit_data['sha']
print(f'  ✓ commit {new_commit[:8]}...', flush=True)

# 7. 更新 ref
result = github_api('PATCH', f'/git/refs/heads/{BRANCH}', {'sha': new_commit, 'force': False})
print(f'  ✓ 更新 ref {result["ref"]} -> {new_commit[:8]}...', flush=True)

print(f'\n✅ 推送成功！共 {len(files_to_push)} 个文件', flush=True)
print(f'   查看: https://github.com/{REPO}/commit/{new_commit}', flush=True)
