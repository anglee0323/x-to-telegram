# x-to-telegram

<p align="center">
  <b>高效实时的 X (Twitter) 到 Telegram 自动同步机器人，基于 Twitter 官方 API v2 开发，支持多图、视频、引用回复及删推联动。</b>
</p>

<p align="center">
  <a href="README.md"><b>English</b></a> | <a href="README_CN.md"><b>简体中文</b></a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python 3.11+" />
  <img src="https://img.shields.io/badge/uv-supported-purple.svg" alt="支持 uv" />
  <img src="https://img.shields.io/badge/Twitter%20API-v2-1DA1F2.svg" alt="Twitter API v2" />
  <img src="https://img.shields.io/badge/Telegram-频道同步-0088cc.svg" alt="Telegram 频道同步" />
  <img src="https://img.shields.io/badge/license-MIT-green.svg" alt="MIT 协议" />
</p>

---

## 🌟 核心特性

- **Twitter 官方 API v2**：基于官方 Bearer Token 鉴权，支持长推文（Note Tweet）、多媒体附件及引用推文关联。
- **完整多媒体支持**：自动适配单图、多图相册（`sendMediaGroup`）、动图 GIF 及视频，自动提取最高码率的 MP4 资源。
- **引用推文与楼层保持**：
  - 若引用的推文已同步至 Telegram，自动以原生回复形式（`reply_to_message_id`）串联，保持上下文关系。
  - 若引用的为外部推文，自动在文末附带原推作者与跳转链接。
- **删推双向联动**：定期巡检最近 $N$ 天内（默认 7 天）已同步的推文，一旦发现原推已被删除，自动撤回/删除 Telegram 频道中对应的消息。
- **灵活的触发模式**：
  - 支持指定标签过滤（如 `#SyncToTelegram`），仅推送打标内容。
  - 支持将 `SYNC_HASHTAG=` 置空，默认自动同步**全部**最新推文。
- **链接与文本智能清洗**：自动将 Twitter 内置的 `t.co` 短链接还原为目标网页真实 URL，并自动剔除尾部用于承载媒体的冗余 t.co 链接。
- **长文本截断兜底保护**：Telegram 对媒体说明（Caption）限制最大 1024 字符；当推文过长时，自动截断附带省略号，并以评论/回复的形式补发全文，确保内容永不丢失、接口不报错。
- **极简免配运行**：遵循 PEP 723 单文件脚本标准，使用 `uv run main.py` 即可零虚拟环境启动；同时兼容传统 `pip install -r requirements.txt`。
- **多样化部署模式**：开箱即用，支持 Linux `systemd` 守护进程、`crontab` 定时任务或 GitHub Actions 自动化。

---

## 🏗 工作流程与架构

```text
       ┌─────────────────┐
       │   X (Twitter)   │
       └────────┬────────┘
                │ Twitter API v2 (Bearer Token 增量拉取)
                ▼
       ┌─────────────────┐
       │  x-to-telegram  │◄── state.json (增量位点与消息映射表)
       └────────┬────────┘
                │
     ┌──────────┴──────────┐
     ▼                     ▼
 [新发推文]             [已删推文]
     │                     │
     ▼                     ▼
Telegram 频道         Telegram 频道
(发送图文/媒体组)      (自动删除对应消息)
```

1. **读取状态**：从 `state.json` 加载上次同步位点（`last_tweet_id`）与消息映射字典（`msg_map`）。
2. **删推检查**：检查过去指定天数内（`DELETE_CHECK_DAYS`）同步的推文；若原推已被作者删除，则调用 Telegram API 撤回对应消息。
3. **拉取新推文**：通过 Twitter API v2 分页 Cursor 与 Snowflake ID 比较拉取新推文，自动过滤转推与回复。
4. **正序发布**：按时间先后正序处理，将文字、图片、视频、引用链接逐一格式化并推送至目标频道。
5. **原子化持久化**：更新同步游标与映射字典，采用临时文件替换机制安全写入 `state.json`，防止意外中断导致文件损坏。

---

## 🚀 快速上手

### 1. 前置准备

- **Python 3.11+** 及 [`uv`](https://docs.astral.sh/uv/)（强烈推荐）或标准 `pip`。
- **Twitter 开发者账号**：在 [Twitter 开发者平台](https://developer.x.com) 创建 App 并获取 Bearer Token。
- **Telegram Bot**：
  1. 打开 Telegram 私聊 [@BotFather](https://t.me/BotFather)，发送 `/newbot` 创建机器人并获取 `TELEGRAM_BOT_TOKEN`。
  2. 新建一个 Telegram 频道（Channel），将你的 Bot 添加为**管理员（Administrator）**，并授予 *发布消息 (Post Messages)* 和 *删除消息 (Delete Messages)* 权限。
  3. 获取该频道的公开发送用户名（如 `@your_channel`）或数字频道 ID（如 `-1001234567890`）。

### 2. 克隆与配置

```bash
git clone https://github.com/anglee0323/x-to-telegram.git
cd x-to-telegram

cp .env.example .env
```

编辑 `.env` 文件，填入各项凭据：

```ini
# 必填项
TWITTER_BEARER_TOKEN=your_twitter_bearer_token_here
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrSTUvwxYZ
TELEGRAM_CHANNEL_ID=@your_channel_username
TWITTER_USERNAME=your_twitter_username

# 选填项
SYNC_HASHTAG=           # 留空则默认同步全部推文；填入标签名则仅同步特定标签
DELETE_CHECK_DAYS=7
```

### 3. 运行同步

**使用 `uv` 运行（推荐，免手动安装虚拟环境）：**
```bash
uv run main.py
```

**使用传统 Python / pip 运行：**
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

---

## ⚙️ 环境变量配置说明

所有配置均可通过 `.env` 文件或系统环境变量进行设置：

| 配置项 | 必填 | 默认值 | 说明 |
| :--- | :---: | :--- | :--- |
| `TWITTER_BEARER_TOKEN` | **是** | — | Twitter / X API v2 开发者 Bearer Token |
| `TELEGRAM_BOT_TOKEN` | **是** | — | 从 @BotFather 获取的 Telegram Bot Token |
| `TELEGRAM_CHANNEL_ID` | **是** | — | 接收推文的目标频道用户名（`@xxx`）或 ID（`-100...`） |
| `TWITTER_USERNAME` | **是** | — | 要监控的 X (Twitter) 用户名（不带 `@`） |
| `SYNC_HASHTAG` | 否 | *(空)* | 仅同步包含此 Hashtag 的推文；**留空则同步所有推文** |
| `TWITTER_API_BASE` | 否 | `https://api.twitter.com/2` | Twitter API v2 基础请求地址 |
| `DELETE_CHECK_DAYS` | 否 | `7` | 删推巡检回溯天数（设为 0 可关闭删推检测） |
| `STATE_FILE` | 否 | `state.json` | 状态与映射存储文件路径 |
| `REQUEST_TIMEOUT` | 否 | `30` | 网络请求超时时间（秒） |
| `MAX_HISTORY_DAYS` | 否 | `60` | 自动清理超过 N 天的历史映射记录，避免文件膨胀 |

---

## 🛠 服务化与生产部署

### 方案 A：Linux `systemd` 守护服务（服务器部署推荐）

创建服务文件 `/etc/systemd/system/x-to-telegram.service`：

```ini
[Unit]
Description=X to Telegram Sync Service
After=network.target

[Service]
Type=simple
User=your_user
WorkingDirectory=/path/to/x-to-telegram
ExecStart=/path/to/uv run main.py
Restart=always
RestartSec=60
EnvironmentFile=/path/to/x-to-telegram/.env

[Install]
WantedBy=multi-user.target
```

启动并设置开机自启：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now x-to-telegram
sudo systemctl status x-to-telegram
```

### 方案 B：Crontab 定时任务

若希望每 5 分钟运行一次：

```bash
*/5 * * * * cd /path/to/x-to-telegram && uv run main.py >> /var/log/x-to-telegram.log 2>&1
```

### 方案 C：GitHub Actions

项目内置了 [`.github/workflows/sync.yml`](.github/workflows/sync.yml) 自动化脚本：

1. 打开 GitHub 仓库的 **Settings** → **Secrets and variables** → **Actions**。
2. 添加以下仓库密钥（Secrets）：
   - `TWITTER_BEARER_TOKEN`
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHANNEL_ID`
   - `TWITTER_USERNAME`
3. 进入 `.github/workflows/sync.yml`，取消 `schedule` 下定时 cron 表达式的注释即可每 5 分钟自动同步。

---

## 📄 开源许可

本项目遵循 [MIT License](LICENSE) 开源协议。
