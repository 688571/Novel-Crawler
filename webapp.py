#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import threading
import json
import fcntl
import tempfile
import hashlib
import re
from flask import Flask, render_template, request, jsonify, redirect, url_for, abort

# ==================== 导入爬虫模块 ====================
from crawler import run_crawler

# ==================== 基础配置 ====================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__,
            template_folder=os.path.join(BASE_DIR, 'templates'))
# 限制上传文件最大为 3MB
app.config['MAX_CONTENT_LENGTH'] = 3 * 1024 * 1024
print("模板文件夹路径:", app.template_folder)

OUTPUT_DIR = os.path.join(BASE_DIR, 'novels')
os.makedirs(OUTPUT_DIR, exist_ok=True)

TASKS_FILE = os.path.join(BASE_DIR, 'tasks.json')
TASKS_LOCK_FILE = TASKS_FILE + '.lock'   # 锁文件
META_FILE = os.path.join(BASE_DIR, 'library_meta.json')
META_LOCK_FILE = META_FILE + '.lock'     # 锁文件

# ==================== 错误处理 ====================
@app.errorhandler(413)
def too_large(e):
    return jsonify({'error': '文件大小超过 3MB 限制'}), 413

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

# ==================== 上传辅助函数 ====================
def get_unique_filepath(filepath):
    """如果文件已存在，自动添加序号 (1), (2)..."""
    if not os.path.exists(filepath):
        return filepath
    base, ext = os.path.splitext(filepath)
    counter = 1
    while True:
        new_path = f"{base} ({counter}){ext}"
        if not os.path.exists(new_path):
            return new_path
        counter += 1

def safe_read_txt(content_bytes):
    """尝试多种编码，返回统一 UTF-8 字符串"""
    # 优先尝试 UTF-8
    try:
        return content_bytes.decode('utf-8')
    except UnicodeDecodeError:
        pass
    # 尝试 GBK（常见中文编码）
    try:
        return content_bytes.decode('gbk')
    except UnicodeDecodeError:
        pass
    # 降级为 latin-1（不会出错，但可能乱码）
    return content_bytes.decode('latin-1')

# ==================== 小说库功能 ====================
def get_novel_list():
    """扫描 novels 目录，返回所有 txt 文件的基本信息（使用元数据中的自定义名称）"""
    novels = []
    if not os.path.exists(OUTPUT_DIR):
        return novels

    meta = read_meta()   # 加载元数据

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
    """读取 TXT 文件，按分隔线解析章节，若没有分隔线则视为单章"""
    with open(txt_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # 检查是否包含分隔线
    if '====' not in content:
        # 无分隔线，整个文件作为一章
        paragraphs = content.split('\n\n')
        body_html = ''.join(f'<p>{p}</p>' for p in paragraphs if p.strip())
        return [{
            'title': '全文',
            'content': body_html
        }]

    # 有分隔线，正常拆分
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
    """启动爬虫（后台线程）"""
    start_url = request.form.get('url')
    novel_name_input = request.form.get('novel_name', '').strip()
    # 增量模式：默认为增量，前端可加参数；这里先设定为增量，也可以从表单获取
    incremental = request.form.get('incremental', 'true').lower() == 'true'

    if not start_url:
        return jsonify({'error': 'URL不能为空'}), 400

    task_id = str(int(time.time()))
    task = {
        'status': 'running',
        'log': [],
        'start_url': start_url,
        'novel_name': None,
        'safe_name': None,
        'html_path': None
    }
    save_task(task_id, task)

    # 准备已有章节哈希
    existing_hashes = set()
    if incremental and novel_name_input:
        safe_name = sanitize_filename(novel_name_input)
        existing_hashes = get_existing_chapter_hashes(safe_name + ".txt")

    def task_worker(task_id, start_url, novel_name_input, existing_hashes, incremental):
        log_message(task_id, f"任务 {task_id} 启动，起始URL: {start_url}")
        if novel_name_input:
            log_message(task_id, f"用户指定小说名: {novel_name_input}")
        if incremental:
            log_message(task_id, f"增量模式启用，已有章节数: {len(existing_hashes)}")
        else:
            log_message(task_id, "未选择增量模式，将重新下载全部章节")
        try:
            # 调用爬虫，传入已有哈希
            novel_name, txt_path, html_path = run_crawler(
                start_url=start_url,
                output_dir=OUTPUT_DIR,
                log_callback=lambda msg: log_message(task_id, msg),
                override_name=novel_name_input if novel_name_input else None,
                existing_chapter_hashes=existing_hashes if incremental else None
                # 如果 incremental 为 False，传 None 表示重新下载全部
            )
            safe_name = os.path.splitext(os.path.basename(html_path))[0]
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

    thread = threading.Thread(target=task_worker, args=(task_id, start_url, novel_name_input, existing_hashes, incremental))
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
    """删除小说文件及其元数据"""
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
        meta = read_meta()
        if filename in meta:
            del meta[filename]
            write_meta(meta)
        return jsonify(success=True)
    except Exception as e:
        return jsonify(success=False, error=str(e)), 500

@app.route('/upload', methods=['POST'])
def upload_txt():
    """上传 TXT 文件，大小限制 3MB，自动编码转换，保存到 novels 目录"""
    if 'file' not in request.files:
        return jsonify({'error': '未选择文件'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': '文件名为空'}), 400

    # 检查扩展名
    if not file.filename.lower().endswith('.txt'):
        return jsonify({'error': '只支持 .txt 文件'}), 400

    # 检查文件大小（前端已限制，后端再确认）
    file.seek(0, 2)
    size = file.tell()
    file.seek(0)
    if size > 3 * 1024 * 1024:
        return jsonify({'error': '文件大小不能超过 3MB'}), 400

    # 读取二进制内容并转码
    content_bytes = file.read()
    content = safe_read_txt(content_bytes)

    # 生成安全的文件名
    original_name = os.path.splitext(file.filename)[0]
    safe_name = sanitize_filename(original_name)
    target_filename = safe_name + '.txt'
    target_path = os.path.join(OUTPUT_DIR, target_filename)

    # 处理重名
    final_path = get_unique_filepath(target_path)
    final_filename = os.path.basename(final_path)

    # 写入文件（UTF-8 编码）
    with open(final_path, 'w', encoding='utf-8') as f:
        f.write(content)

    # 获取用户自定义书名（可选参数）
    custom_name = request.form.get('novel_name', '').strip()
    if not custom_name:
        custom_name = original_name

    # 更新元数据
    meta = read_meta()
    meta[final_filename] = {
        'custom_name': custom_name,
        'chapters': [],          # 上传的 txt 没有章节 URL，留空
        'last_modified': time.strftime('%Y-%m-%d %H:%M:%S'),
        'uploaded': True
    }
    write_meta(meta)

    return jsonify({
        'success': True,
        'filename': final_filename,
        'name': custom_name
    })

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

@app.route('/console')
def console():
    """爬虫控制台"""
    return render_template('index.html')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True, threaded=True)
