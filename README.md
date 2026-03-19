# Novel-Crawler # 小说爬虫与在线阅读系统
这是一个基于 Flask 的轻量级网络小说爬虫 + 本地在线阅读系统。 只需提供小说目录页或任意章节页的 URL，系统即可自动抓取整本小说，保存为 .txt 文件，并生成 HTML 阅读版本。 内置小说库管理，支持重命名、删除和在线阅读

## ✨ 功能特点

- 🕷️ **智能爬虫**  
  - 自动识别目录页（提取所有章节链接）或章节页（通过“下一章”链接连续抓取）  
  - 支持分页章节（如“第1章 共3页”）自动合并内容  
  - 随机 User-Agent + 重试机制 + 请求延迟，降低被封风险  
  - 实时日志输出，任务状态持久化

- 📚 **小说库管理**  
  - 列出所有已下载小说（显示文件名、修改时间、大小、章节数估算）  
  - 支持**重命名**（自定义显示名称）和**删除**  
  - 元数据保存在 `library_meta.json`，改名后不影响原文件

- 📖 **在线阅读器**  
  - 动态解析 `.txt` 文件，按 `=====...` 分隔符分章  
  - 目录导航、上下章切换，响应式设计  
  - 支持直接阅读爬虫生成的 HTML 版本（兼容旧版）

- 🧵 **多任务并发**  
  - 每个爬虫任务在独立后台线程运行，不阻塞 Web 服务  
  - 任务状态和日志实时更新（轮询 API）

- ⚙️ **生产部署友好**  
  - 提供 Gunicorn 和 uWSGI 配置文件，一键部署  
  - 使用文件锁（`fcntl`）保证多进程下任务/元数据安全（仅限 Linux）

---

## 🛠️ 技术栈

- **后端**：Python 3.12+, Flask, Requests, BeautifulSoup4  
- **前端**：HTML, CSS, JavaScript (原生, 无框架)  
- **部署**：Gunicorn / uWSGI (可选)

---

## 📦 安装指南

### 1. 克隆仓库

```bash
git clone https://github.com/yourusername/novel-crawler-reader.git
cd novel-crawler-reader
```

### 2. 创建虚拟环境（推荐）

```bash
python3 -m venv venv
source venv/bin/activate   # Linux/Mac
# 或 .\venv\Scripts\activate (Windows)
```

### 3. 安装依赖

```bash
pip install -r requirements.txt
```

> `requirements.txt` 内容：
> ```
> flask==2.3.3
> werkzeug==2.3.8
> requests>=2.25.0
> beautifulsoup4>=4.9.0
> ```

### 4. 配置（可选）

- **输出目录**：默认在项目根目录下的 `novels/`，可修改 `webapp.py` 中的 `OUTPUT_DIR` 变量。
- **端口**：开发环境默认 `5000`，生产环境可修改 `gunicorn_conf.py` 或 `uwsgi.ini`。

---

## 🚀 运行方式

### 开发调试模式

```bash
python webapp.py
```
访问 `http://127.0.0.1:5000`

### 生产部署（Gunicorn）

```bash
gunicorn -c gunicorn_conf.py webapp:app
```

### 生产部署（uWSGI）

```bash
uwsgi --ini uwsgi.ini
```

> 注意：生产环境请使用普通用户运行（修改 `uid` 和 `gid`），避免 root 运行带来的安全风险。

---

## 📖 使用方法

1. **打开首页**：访问 `http://your-server:5000/`
2. **提交爬虫任务**：
   - 输入小说起始 URL（目录页或章节页均可）
   - 可选：指定小说名称（若留空则自动从页面 `<title>` 提取）
   - 点击“开始抓取”
3. **查看任务日志**：跳转至日志页面，实时显示抓取进度。任务完成后会显示生成的 HTML 文件路径。
4. **进入小说库**：点击导航栏“小说库”，查看所有已下载小说。
   - 可点击“阅读”在线浏览（动态解析 TXT）
   - 可点击“重命名”修改显示名称（不影响文件名）
   - 可点击“删除”移除小说及元数据
5. **在线阅读**：
   - 进入阅读页面后，左侧/顶部有目录列表，点击章节跳转
   - 每章底部有“上一章/下一章”导航

---

## 📁 文件结构

```
.
├── webapp.py                 # Flask 主应用
├── crawler.py                 # 爬虫核心逻辑
├── gunicorn_conf.py           # Gunicorn 配置文件
├── uwsgi.ini                  # uWSGI 配置文件
├── requirements.txt           # Python 依赖
├── tasks.json                 # 任务状态存储（自动生成）
├── library_meta.json          # 小说自定义名称元数据（自动生成）
├── novels/                    # 下载的小说存放目录
│   ├── 小说名.txt
│   └── 小说名.html
└── templates/                 # HTML 模板
    ├── index.html
    ├── logs.html
    ├── library.html
    └── reader_txt.html
```

---

## ⚠️ 注意事项

1. **尊重目标网站**  
   请确保您的抓取行为符合目标网站的 `robots.txt` 及相关法律法规。建议设置合理延迟，避免对服务器造成压力。

2. **反爬机制**  
   部分网站可能对频繁访问进行封锁。本程序已内置随机 User-Agent、重试机制和延迟（2~5 秒），但仍需根据实际情况调整 `crawler.py` 中的 `USER_AGENTS` 和 `base_delay`。

3. **选择器适配**  
   默认的标题/内容/下一页选择器适用于常见小说网站。若目标网站结构特殊，可能需要修改 `crawler.py` 中的 `COMMON_TITLE_SELECTORS`、`COMMON_CONTENT_SELECTORS` 等配置。

4. **并发与锁**  
   `tasks.json` 和 `library_meta.json` 使用 `fcntl` 文件锁保证多进程安全，**仅支持 Linux**。若在 Windows 下运行，需替换为其他锁机制（如 `portalocker`）或避免多进程并发写入。

5. **编码问题**  
   爬虫默认以 UTF-8 解码，若网站编码不同可能乱码。可修改 `crawler.py` 中 `fetch_html` 里的 `resp.encoding` 或使用 `chardet` 自动检测。

---

## 🤝 贡献指南

欢迎提交 Issue 或 Pull Request。  
在修改前请确保代码风格与现有保持一致（尽量使用函数式，避免类滥用）。

---

## 📄 许可证

MIT License

---

## 📸 截图

*（这里可以放几张项目截图，例如首页、日志页、小说库、阅读器界面）*

---

**Happy Reading!** 📚
