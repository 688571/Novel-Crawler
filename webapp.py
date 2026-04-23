# webapp.py
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import requests
import threading
import json
import fcntl
import tempfile
import hashlib
import re
from flask import Flask, render_template, request, jsonify, redirect, url_for, abort
from crawler import run_crawler, generate_html_from_txt, parse_author_page
# ==================== 基础配置 ====================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__,
            template_folder=os.path.join(BASE_DIR, 'templates'))
print("模板文件夹路径:", app.template_folder)

OUTPUT_DIR = os.path.join(BASE_DIR, 'novels')
os.makedirs(OUTPUT_DIR, exist_ok=True)

TASKS_FILE = os.path.join(BASE_DIR, 'tasks.json')
TASKS_LOCK_FILE = TASKS_FILE + '.lock'
META_FILE = os.path.join(BASE_DIR, 'library_meta.json')
META_LOCK_FILE = META_FILE + '.lock'

# ==================== 任务存储（文件版，原子写入+锁）====================
def read_tasks():
    """安全读取任务字典（共享锁）"""
    with open(TASKS_LOCK_FILE, 'w') as lock_f:
        fcntl.flock(lock_f, fcntl.LOCK_SH)
        try:
            with open(TASKS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            data = {}
        finally:
            fcntl.flock(lock_f, fcntl.LOCK_UN)
    return data

def write_tasks(tasks):
    """原子写入任务字典（独占锁+临时文件替换）"""
    with open(TASKS_LOCK_FILE, 'w') as lock_f:
        fcntl.flock(lock_f, fcntl.LOCK_EX)
        fd, temp_path = tempfile.mkstemp(dir=os.path.dirname(TASKS_FILE), prefix='tasks_')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as temp_f:
                json.dump(tasks, temp_f, ensure_ascii=False, indent=2)
            os.replace(temp_path, TASKS_FILE)
        except Exception:
            os.unlink(temp_path)
            raise
        finally:
            fcntl.flock(lock_f, fcntl.LOCK_UN)

def get_task(task_id):
    tasks = read_tasks()
    return tasks.get(task_id)

def save_task(task_id, task):
    tasks = read_tasks()
    tasks[task_id] = task
    write_tasks(tasks)

def append_task_log(task_id, msg):
    tasks = read_tasks()
    if task_id in tasks:
        tasks[task_id]['log'].append(f"[{time.strftime('%H:%M:%S')}] {msg}")
        write_tasks(tasks)

def log_message(task_id, msg):
    append_task_log(task_id, msg)

# ==================== 小说元数据读写（原子写入+锁）====================
def read_meta():
    """安全读取元数据（共享锁）"""
    with open(META_LOCK_FILE, 'w') as lock_f:
        fcntl.flock(lock_f, fcntl.LOCK_SH)
        try:
            with open(META_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}
        finally:
            fcntl.flock(lock_f, fcntl.LOCK_UN)

def write_meta(meta):
    """原子写入元数据（独占锁+临时文件替换）"""
    with open(META_LOCK_FILE, 'w') as lock_f:
        fcntl.flock(lock_f, fcntl.LOCK_EX)
        fd, temp_path = tempfile.mkstemp(dir=os.path.dirname(META_FILE), prefix='meta_')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as temp_f:
                json.dump(meta, temp_f, ensure_ascii=False, indent=2)
            os.replace(temp_path, META_FILE)
        except Exception:
            os.unlink(temp_path)
            raise
        finally:
            fcntl.flock(lock_f, fcntl.LOCK_UN)

# ==================== 章节哈希处理 ====================
def get_chapter_hash(url):
    """返回URL的MD5哈希值，作为章节唯一标识"""
    return hashlib.md5(url.encode('utf-8')).hexdigest()

def get_existing_chapter_hashes(filename):
    """从元数据中读取已下载章节的URL哈希集合"""
    meta = read_meta()
    file_meta = meta.get(filename, {})
    return set(file_meta.get('chapters', []))

def add_chapter_hashes(filename, new_hashes):
    """将新下载的章节哈希追加到元数据中"""
    meta = read_meta()
    if filename not in meta:
        meta[filename] = {}
    existing = set(meta[filename].get('chapters', []))
    existing.update(new_hashes)
    meta[filename]['chapters'] = list(existing)
    meta[filename]['last_modified'] = time.strftime('%Y-%m-%d %H:%M:%S')
    write_meta(meta)

def sanitize_filename(name):
    """清理文件名中的非法字符"""
    return re.sub(r'[\\/*?:"<>|\s]', '_', name)

# ==================== 小说库功能 ====================
def get_novel_list():
    """扫描 novels 目录，返回所有 txt 文件的基本信息（使用元数据中的自定义名称）"""
    novels = []
    if not os.path.exists(OUTPUT_DIR):
        return novels

    meta = read_meta()

    for filename in os.listdir(OUTPUT_DIR):
        if filename.endswith('.txt'):
            filepath = os.path.join(OUTPUT_DIR, filename)
            mtime = os.path.getmtime(filepath)
            size = os.path.getsize(filepath)

            file_meta = meta.get(filename, {})
            display_name = file_meta.get('custom_name', filename[:-4])

            # 粗略估算章节数（读取前4KB）
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    content = f.read(4096)
                    chapter_count = content.count('\n=') + 1
            except:
                chapter_count = 0

            novels.append({
                'filename': filename,
                'name': display_name,
                'mtime': time.strftime('%Y-%m-%d %H:%M', time.localtime(mtime)),
                'size': f'{size/1024:.1f} KB',
                'chapter_estimate': chapter_count
            })

    novels.sort(key=lambda x: x['mtime'], reverse=True)
    return novels

def read_txt_chapters(txt_path):
    """读取 TXT 文件，按分隔线解析章节，返回章节列表 [(title, content_html), ...]"""
    with open(txt_path, 'r', encoding='utf-8') as f:
        content = f.read()
    chapters_raw = re.split(r'\n={4,}\n', content)
    chapters = []
    for chap in chapters_raw:
        if not chap.strip():
            continue
        lines = chap.strip().split('\n', 1)
        title = lines[0].strip()
        body = lines[1].strip() if len(lines) > 1 else ''
        paragraphs = body.split('\n\n')
        body_html = ''.join(f'<p>{p}</p>' for p in paragraphs if p.strip())
        chapters.append({'title': title, 'content': body_html})
    return chapters

# ==================== 路由 ====================
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/start', methods=['POST'])
def start_crawl():
    start_url = request.form.get('url')
    novel_name_input = request.form.get('novel_name', '').strip()
    incremental = request.form.get('incremental', 'true').lower() == 'true'
    proxy_url = request.form.get('proxy_url', '').strip()   # 新增：代理地址

    if not start_url:
        return jsonify({'error': 'URL不能为空'}), 400

    task_id = str(int(time.time()))
    task = {
        'status': 'running',
        'log': [],
        'start_url': start_url,
        'novel_name': None,
        'safe_name': None,
        'html_path': None,
        'proxy_used': proxy_url or None   # 记录使用的代理（可选）
    }
    save_task(task_id, task)

    # 准备已有章节哈希
    existing_hashes = set()
    if incremental and novel_name_input:
        safe_name = sanitize_filename(novel_name_input)
        existing_hashes = get_existing_chapter_hashes(safe_name + ".txt")

    # 修改 task_worker 参数，增加 proxy_url
    def task_worker(task_id, start_url, novel_name_input, existing_hashes, incremental, proxy_url):
        log_message(task_id, f"任务 {task_id} 启动，起始URL: {start_url}")
        if proxy_url:
            log_message(task_id, f"使用代理：{proxy_url}")
        if novel_name_input:
            log_message(task_id, f"用户指定小说名: {novel_name_input}")
        if incremental:
            log_message(task_id, f"增量模式启用，已有章节数: {len(existing_hashes)}")
        else:
            log_message(task_id, "未选择增量模式，将重新下载全部章节")
        try:
            # 调用爬虫，传入代理地址
            novel_name, txt_path, html_path = run_crawler(
                start_url=start_url,
                output_dir=OUTPUT_DIR,
                log_callback=lambda msg: log_message(task_id, msg),
                override_name=novel_name_input if novel_name_input else None,
                existing_chapter_hashes=existing_hashes if incremental else None,
                proxy=proxy_url if proxy_url else None   # 新增参数
            )
            if html_path:
                safe_name = os.path.splitext(os.path.basename(html_path))[0]
            else:
                safe_name = sanitize_filename(novel_name) if novel_name else "unknown"
            task = get_task(task_id)
            task['status'] = 'finished'
            task['novel_name'] = novel_name
            task['safe_name'] = safe_name
            task['html_path'] = html_path
            save_task(task_id, task)
            log_message(task_id, "任务完成")
        except Exception as e:
            import traceback
            log_message(task_id, f"任务异常: {str(e)}")
            log_message(task_id, traceback.format_exc())
            task = get_task(task_id)
            task['status'] = 'error'
            save_task(task_id, task)

    # 启动线程时传入 proxy_url
    thread = threading.Thread(target=task_worker, args=(task_id, start_url, novel_name_input, existing_hashes, incremental, proxy_url))
    thread.daemon = True
    thread.start()
    return redirect(url_for('logs', task_id=task_id))

@app.route('/rename', methods=['POST'])
def rename_novel():
    """接收改名请求，更新元数据"""
    data = request.get_json()
    filename = data.get('filename')
    new_name = data.get('new_name')

    if not filename or not new_name:
        return jsonify(success=False, error='缺少参数'), 400

    if '..' in filename or '/' in filename or '\\' in filename:
        return jsonify(success=False, error='非法文件名'), 400

    filepath = os.path.join(OUTPUT_DIR, filename)
    if not os.path.exists(filepath):
        return jsonify(success=False, error='文件不存在'), 404

    meta = read_meta()
    if filename not in meta:
        meta[filename] = {}
    meta[filename]['custom_name'] = new_name.strip()
    meta[filename]['last_modified'] = time.strftime('%Y-%m-%d %H:%M:%S')
    write_meta(meta)

    return jsonify(success=True, new_name=new_name)

@app.route('/delete', methods=['POST'])
def delete_novel():
    """删除小说文件及其元数据，并删除同名 HTML"""
    data = request.get_json()
    filename = data.get('filename')

    if not filename:
        return jsonify(success=False, error='缺少文件名'), 400

    if '..' in filename or '/' in filename or '\\' in filename:
        return jsonify(success=False, error='非法文件名'), 400

    filepath = os.path.join(OUTPUT_DIR, filename)
    if not os.path.exists(filepath) or not os.path.isfile(filepath):
        return jsonify(success=False, error='文件不存在'), 404

    try:
        os.remove(filepath)

        # 删除同名 HTML 文件
        base = os.path.splitext(filename)[0]
        html_path = os.path.join(OUTPUT_DIR, base + '.html')
        if os.path.exists(html_path):
            os.remove(html_path)

        # 删除元数据
        meta = read_meta()
        if filename in meta:
            del meta[filename]
            write_meta(meta)

        return jsonify(success=True)
    except Exception as e:
        return jsonify(success=False, error=str(e)), 500
        
from werkzeug.utils import secure_filename

# 允许上传的文件扩展名
ALLOWED_EXTENSIONS = {'txt'}
MAX_CONTENT_LENGTH = 3 * 1024 * 1024  # 3MB

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/upload', methods=['POST'])
def upload_novel():
    """上传 TXT 小说文件，自动生成 HTML 并加入书架"""
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': '未找到文件'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'success': False, 'error': '文件名为空'}), 400

    if not allowed_file(file.filename):
        return jsonify({'success': False, 'error': '仅支持 .txt 文件'}), 400

    # 文件大小检查（Flask 默认限制，此处额外检查）
    file.seek(0, os.SEEK_END)
    size = file.tell()
    file.seek(0)
    if size > MAX_CONTENT_LENGTH:
        return jsonify({'success': False, 'error': '文件大小超过 3MB'}), 400

    # 获取自定义书名（如果提供）
    custom_name = request.form.get('novel_name', '').strip()
    original_basename = os.path.splitext(file.filename)[0]

    if custom_name:
        # 使用用户输入的书名作为显示名和文件名
        safe_basename = sanitize_filename(custom_name)
        display_name = custom_name
    else:
        safe_basename = sanitize_filename(original_basename)
        display_name = original_basename

    txt_filename = safe_basename + '.txt'
    txt_path = os.path.join(OUTPUT_DIR, txt_filename)

    # 如果文件已存在，可以选择覆盖（这里直接覆盖，并更新元数据）
    # 也可以返回错误，根据需求调整
    try:
        file.save(txt_path)
    except Exception as e:
        return jsonify({'success': False, 'error': f'保存文件失败：{str(e)}'}), 500

    # 生成对应的 HTML 阅读文件
    try:
        html_path = generate_html_from_txt(txt_path, display_name)
    except Exception as e:
        # 如果生成 HTML 失败，不影响小说入库，但记录日志
        print(f"生成 HTML 失败：{e}")
        html_path = None

    # 更新元数据（记录自定义名称和章节哈希，可选）
    meta = read_meta()
    if txt_filename not in meta:
        meta[txt_filename] = {}
    meta[txt_filename]['custom_name'] = display_name
    meta[txt_filename]['last_modified'] = time.strftime('%Y-%m-%d %H:%M:%S')
    # 可选：解析章节哈希（简单处理，先留空，实际可调用解析函数）
    meta[txt_filename]['chapters'] = meta[txt_filename].get('chapters', [])
    write_meta(meta)

    return jsonify({'success': True, 'name': display_name, 'filename': txt_filename})
    
@app.route('/logs/<task_id>')
def logs(task_id):
    """显示任务日志页面"""
    task = get_task(task_id)
    if not task:
        return "任务不存在", 404
    return render_template('logs.html', task_id=task_id, task=task)

@app.route('/api/logs/<task_id>')
def api_logs(task_id):
    """API获取最新日志（用于轮询）"""
    task = get_task(task_id)
    if not task:
        return jsonify({'error': 'not found'}), 404
    return jsonify({
        'status': task['status'],
        'log': task['log'],
        'novel_name': task.get('novel_name'),
        'safe_name': task.get('safe_name'),
        'html_path': task.get('html_path')
    })

@app.route('/library')
def library():
    """小说库主页，列出所有小说"""
    novels = get_novel_list()
    return render_template('library.html', novels=novels)

@app.route('/read/<filename>')
def read_novel(filename):
    """动态阅读 TXT 小说"""
    safe_name = os.path.basename(filename)
    if not safe_name.endswith('.txt'):
        safe_name += '.txt'
    filepath = os.path.join(OUTPUT_DIR, safe_name)
    if not os.path.exists(filepath):
        abort(404, description="小说文件不存在")

    try:
        chapters = read_txt_chapters(filepath)
    except Exception as e:
        abort(500, description=f"解析文件失败：{str(e)}")

    novel_name = safe_name[:-4]
    return render_template('reader_txt.html', novel_name=novel_name, chapters=chapters)

@app.route('/reader/<path:novel_name>')
def reader(novel_name):
    """兼容旧版：若 HTML 存在则渲染，否则重定向到动态阅读器"""
    html_path = os.path.join(OUTPUT_DIR, novel_name, novel_name + '.html')
    if os.path.exists(html_path):
        with open(html_path, 'r', encoding='utf-8') as f:
            html_content = f.read()
        return render_template('reader.html', novel_name=novel_name, html_content=html_content)
    else:
        return redirect(url_for('read_novel', filename=novel_name + '.txt'))
# 在 webapp.py 中新增以下路由（放在其他路由附近）

@app.route('/api/test_proxy', methods=['POST'])
def test_proxy():
    data = request.get_json()
    proxy_url = data.get('proxy_url', '').strip()
    if not proxy_url:
        return jsonify({'success': False, 'message': '代理地址不能为空'})

    proxies = {'http': proxy_url, 'https': proxy_url}
    try:
        start_time = time.time()
        # 改用 HTTP 协议测试（避免 SSL 错误）
        resp = requests.get('http://www.baidu.com', proxies=proxies, timeout=10, verify=False)
        elapsed = (time.time() - start_time) * 1000
        if resp.status_code == 200:
            return jsonify({'success': True, 'message': f'连通成功，响应时间 {elapsed:.0f} ms'})
        else:
            return jsonify({'success': False, 'message': f'代理返回异常状态码：{resp.status_code}'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'代理测试失败：{str(e)}'})
        
@app.route('/author')
def author_form():
    """作者页输入表单"""
    return render_template('author_form.html')

@app.route('/author/parse', methods=['POST'])
def author_parse():
    """解析作者页，返回作品列表供用户勾选"""
    author_url = request.form.get('author_url', '').strip()
    if not author_url:
        return jsonify({'error': '作者页URL不能为空'}), 400

    try:
        author_name, works = parse_author_page(author_url, log_callback=lambda msg: print(msg))
    except Exception as e:
        return render_template('author_select.html', error=str(e), author_url=author_url)

    # 将作品列表和作者名存储到 session 或临时传递，这里直接渲染页面
    return render_template('author_select.html', author_name=author_name, works=works, author_url=author_url)

@app.route('/batch_start', methods=['POST'])
def batch_start():
    """批量下载用户勾选的作品"""
    selected_urls = request.form.getlist('selected_urls')
    if not selected_urls:
        return jsonify({'error': '请至少选择一部作品'}), 400

    # 可选：获取代理设置（可从表单传递或全局配置）
    proxy_url = request.form.get('proxy_url', '').strip()

    task_id = str(int(time.time()))
    task = {
        'status': 'running',
        'log': [],
        'type': 'batch',
        'total': len(selected_urls),
        'completed': 0,
        'results': [],
        'proxy_used': proxy_url or None
    }
    save_task(task_id, task)

    def batch_worker():
        log_message(task_id, f"批量下载任务启动，共 {len(selected_urls)} 部作品")
        if proxy_url:
            log_message(task_id, f"使用代理：{proxy_url}")

        for idx, url in enumerate(selected_urls, 1):
            log_message(task_id, f"开始下载第 {idx}/{len(selected_urls)} 部：{url}")
            try:
                # 调用单本下载函数（注意 run_crawler 会创建独立文件）
                novel_name, txt_path, html_path = run_crawler(
                    start_url=url,
                    output_dir=OUTPUT_DIR,
                    log_callback=lambda msg: log_message(task_id, f"  {msg}"),
                    override_name=None,
                    existing_chapter_hashes=None,  # 批量模式默认全量下载
                    update_meta_callback=None,
                    proxy=proxy_url if proxy_url else None
                )
                task_data = get_task(task_id)
                task_data['results'].append({
                    'url': url,
                    'novel_name': novel_name,
                    'txt_path': txt_path,
                    'html_path': html_path,
                    'status': 'success'
                })
                task_data['completed'] = idx
                save_task(task_id, task_data)
                log_message(task_id, f"第 {idx} 部完成：{novel_name}")
            except Exception as e:
                import traceback
                error_msg = f"第 {idx} 部下载失败：{str(e)}\n{traceback.format_exc()}"
                log_message(task_id, error_msg)
                task_data = get_task(task_id)
                task_data['results'].append({
                    'url': url,
                    'error': str(e),
                    'status': 'error'
                })
                task_data['completed'] = idx
                save_task(task_id, task_data)
            # 作品之间休息 3 秒
            if idx < len(selected_urls):
                time.sleep(3)

        # 任务结束
        final_task = get_task(task_id)
        final_task['status'] = 'finished'
        save_task(task_id, final_task)
        log_message(task_id, "批量下载任务全部完成")

    thread = threading.Thread(target=batch_worker)
    thread.daemon = True
    thread.start()
    return redirect(url_for('logs', task_id=task_id))

@app.route('/console')
def console():
    """爬虫控制台"""
    return render_template('index.html')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True, threaded=True)
