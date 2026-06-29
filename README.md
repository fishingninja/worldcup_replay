# WorldCup Replay — 2026世界杯防剧透回放工具

> 为不方便熬夜看球的球迷，守护每一场未知的乐趣。

---

## 一、项目定位

**核心痛点**：世界杯比赛多在深夜/凌晨开球，国内球迷难以实时观看。第二天补看时，社交媒体、新闻标题极易剧透比分，破坏观赛体验。

**解决方案**：提供一个**零剧透的静态 HTML 入口页**，聚合：
- **央视体育 (sports.cctv.com)** 官方回放链接
- **小红书 (xiaohongshu.com)** 高清视频回放（浏览器直接播放，无需 App）

页面本身不显示任何比分，且只对「开球时间+2.5小时」之后的比赛才展示链接，从机制上避免剧透。

**目标用户**：2026世界杯期间，希望第二天安全补看比赛、不看比分的国内球迷。

---

## 二、功能特性

| 特性 | 说明 |
|------|------|
| 🚫 零剧透 | 页面不显示任何比分；JS动态判断比赛是否结束，未结束不显示 |
| 📺 双源回放 | 央视官方 + 小红书高清视频，浏览器直接播放 |
| 🔄 自动更新 | 每天自动抓取最新回放链接 |
| 📅 最近两天 | 只显示最近两天的已完赛比赛，保持页面清爽 |
| 🌙 深色主题 | 护眼暗色设计，适合夜晚补看比赛前使用 |
| 📂 纯静态 | 无任何后端依赖，双击 `index.html` 即可使用 |
| 🧩 全阶段覆盖 | 小组赛 → 1/16决赛 → 1/8决赛 → ... → 决赛 |

---

## 三、项目结构

```
worldcup_replay/
├── 📄 前端（用户可直接打开）
│   └── index.html              # 主页面（防剧透回放入口，内含 match-data + 视频URL缓存）
│
├── 📊 数据文件（自动生成）
│   ├── matches.json            # 完整赛程数据（含小组赛+淘汰赛）
│   └── video_urls.json         # 小红书视频 URL 缓存
│
├── 🐍 Python 脚本
│   ├── 📦 主流程
│   │   └── update_xhs_all.py   # 一键更新入口（5步流程）
│   │
│   ├── 🔍 赛程与回放抓取
│   │   ├── fetch_all_video_urls.py   # Playwright 抓取 XHS 视频URL + 保存实时赛程
│   │   ├── fetch_video_with_token.py # 备用：用 xsec_token 单独拉取视频URL
│   │   ├── update_replay_links.py    # Playwright 抓取央视回放链接
│   │   └── update_matches.py         # 从央视赛程页更新 match-data
│   │
│   ├── 🧬 赛程生成
│   │   ├── generate_schedule_from_xhs.py  # [推荐] 从 XHS 实时日历数据生成完整赛程
│   │   └── generate_full_schedule.py      # [旧版] 硬编码小组赛赛程（淘汰赛请用上方脚本）
│   │
│   ├── 🔄 数据处理与同步
│   │   ├── generate_video_urls_json.py # 从 all_video_urls.json 生成精简版
│   │   ├── sync_inline_urls.py         # 合并 video_urls.json → index.html
│   │   └── push_api.py                 # 通过 GitHub REST API 推送（避免 443 被封）
│   │
│   └── 🗑️ 旧版脚本（可安全删除）
│       └── sync_inline_urls.py         # 独立运行
│
├── ⚙️ CI/CD
│   └── .github/workflows/update.yml    # GitHub Actions 定时抓取（境外无法抓 XHS）
│
├── 📝 文档
│   └── README.md                 # 本文件
│
├── 📄 配置文件
│   └── .gitignore                # Git 忽略规则
```

---

## 四、快速开始

### 4.1 本地使用（纯前端）

```bash
# 1. 克隆仓库
git clone https://github.com/fishingninja/worldcup_replay.git

# 2. 直接用浏览器打开 index.html
# ✅ 无需任何后端 / 无需 HTTP 服务器
```

> **提示**：`matches.json` 和 `video_urls.json` 会随每次自动更新推送到仓库，clone 后即可看到最新数据。

### 4.2 本地运行数据更新（需要中国 IP）

小红书在国内有 IP 限制，GHA（境外服务器）无法正常抓取。如需获取最新视频 URL，需要在本地运行：

```bash
# 1. 安装依赖
pip install playwright requests
python -m playwright install chromium

# 2. 一键更新（推荐）
python update_xhs_all.py

# 或分步执行：
# python fetch_all_video_urls.py
# python generate_schedule_from_xhs.py
# python generate_video_urls_json.py
# python sync_inline_urls.py
# python push_api.py
```

**依赖说明**：
- Python 3.8+
- `playwright` — 浏览器自动化（抓取 XHS 和央视数据）
- `requests` — HTTP 请求（央视赛程页）

### 4.3 GitHub Actions 自动更新

GHA 每天 UTC 23:00/01:00/04:00/06:00（北京时间 07:00/09:00/12:00/14:00）自动运行：
- ✅ 央视回放链接抓取（Playwright）
- ❌ 小红书视频抓取（境外 IP 受限，会静默跳过）
- ✅ 数据同步和推送到仓库

> 小红书视频需要在中国 IP 下运行 `update_xhs_all.py`（见 4.2）

---

## 五、技术要点

### 5.1 防剧透机制（三重保障）

**机制一：页面不显示比分**
- 比赛列表只显示队名和开球时间，绝不显示比分
- 页眉标语、按钮文字均不含任何比赛结果信息

**机制二：时间门槛过滤（JS运行时判断）**
```javascript
const isFinished = (kickoffISO) =>
  (new Date(kickoffISO).getTime() + 2.5 * 60 * 60 * 1000) < Date.now();
```
- 未结束的比赛不会出现在页面上

**机制三：只显示最近两天**
```javascript
const isWithinTwoDays = (kickoffISO) =>
  (Date.now() - new Date(kickoffISO).getTime()) <= 2 * 24 * 60 * 60 * 1000;
```

### 5.2 数据管道架构

```
XHS 日历 API (实时赛程)                          央视赛程页
        │                                            │
        ▼                                            ▼
 generate_schedule_from_xhs.py           update_replay_links.py
  生成完整赛程（含淘汰赛）                     抓取央视回放链接
        │                                            │
        ▼                                            ▼
  matches.json  ─── 同步 ───▶  index.html ◀─── matches.json
        │                     (match-data)            ▲
        ▼                                            │
 fetch_all_video_urls.py                             │
  抓取 XHS 视频 URL                                  │
        │                                            │
        ▼                                            │
 generate_video_urls_json.py                         │
        │                                            │
        ▼                                            │
  sync_inline_urls.py  ──── 合并 ───▶  index.html ───┘
                                   (video-url-data)
```

### 5.3 数据嵌入方案

比赛数据和视频URL都直接嵌入 `index.html` 的 `<script>` 标签中：

```html
<script id="match-data" type="application/json">[...]</script>    <!-- 赛程+回放链接 -->
<script id="video-url-data" type="application/json">[...]</script> <!-- 视频URL缓存 -->
```

**优势**：纯 `file://` 打开即可运行，无需 HTTP 服务器。

### 5.4 增量抓取策略

`fetch_all_video_urls.py` 使用增量模式：
1. 读取已有的 `all_video_urls.json`，记录已抓取的 `note_id`
2. 只对缺少视频URL的新比赛执行 Playwright 抓取
3. 跳过已抓取成功的比赛，节省网络请求和运行时间

---

## 六、数据字段说明

### 赛程字段 (`match-data` / `matches.json`)

| 字段 | 类型 | 说明 |
|------|------|------|
| `date` | string | 显示用日期，如 `"6月29日"` |
| `kickoff` | string | 开球时间（ISO 8601，北京时间），如 `"2026-06-29T03:00:00+08:00"` |
| `teamA` | string | 主队名（含国旗emoji），如 `"🇿🇦 南非"` |
| `teamB` | string | 客队名（含国旗emoji），如 `"🇨🇦 加拿大"` |
| `group` | string | 阶段/组别，如 `"A组"` / `"1/16决赛"` |
| `cctvUrl` | string | 央视体育回放链接（可选） |
| `miguUrl` | string | 咪咕视频回放链接（可选） |
| `verified` | bool | 链接是否已人工验证可用 |
| `xhsNoteId` | string | 小红书笔记ID（可选，用于匹配视频URL） |

### 视频URL字段 (`video-url-data` / `video_urls.json`)

| 字段 | 类型 | 说明 |
|------|------|------|
| `note_id` | string | 小红书笔记 ID |
| `video_urls` | string[] | 视频 CDN URL 列表（最多2个不同域名） |
| `updated_at` | string | 抓取时间戳 |

---

## 七、贡献指南

本项目的核心价值是**零剧透**。任何功能新增都不得：
- 在页面上显示比分、胜负结果
- 在页面上显示任何可能剧透比赛进程的信息（如进球时间、红黄牌等）

### 开发注意事项

1. **Token 安全**：GitHub Token 通过环境变量 `GITHUB_TOKEN` 传入，切勿硬编码在代码中
2. **Playwright**：本地抓取需要安装 Chromium 浏览器
3. **GitHub Actions**：在 `Settings → Secrets and variables → Actions` 中设置 `GITHUB_TOKEN`

欢迎提交 Issue 和 Pull Request！

---

## 八、许可

MIT License — 自由使用、修改和分发。
