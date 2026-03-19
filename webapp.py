#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import threading
import json
import fcntl
import re
from flask import Flask, render_template, request, jsonify, redirect, url_for, abort
from crawler import run_crawler

# ==================== 基础配置 ====================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__,
            template_folder=os.path.join(BASE_DIR, 'templates'))
print("模板文件夹路径:", app.template_folder)

OUTPUT_DIR = os.path.join(BASE_DIR, 'novels')
os.makedirs(OUTPUT_DIR, exist_ok=True)

TASKS_FILE = os.path.join(BASE_DIR, 'tasks.json')
META_FILE = os.path.join(BASE_DIR, 'library_meta.json')   # 新增元数据文件

# ==================== 任务存储（文件版）====================
def read_tasks():
    """读取任务字典（加共享锁）"""
    if not os.path.exists(TASKS_FILE):
        return {}
    with open(TASKS_FILE, 'r', encoding='utf-8') as f:
        fcntl.flock(f, fcntl.LOCK_SH)
        data = json.load(f)
        fcntl.flock(f, fcntl.LOCK_UN)
    return data

def write_tasks(tasks):
    """写入任务字典（加独占锁）"""
    with open(TASKS_FILE, 'w', encoding='utf-8') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        json.dump(tasks, f, ensure_ascii=False, indent=2)
        fcntl.flock(f, fcntl.LOCK_UN)

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

# ==================== 新增：小说元数据读写 ====================
def read_meta():
    """读取小说元数据（加共享锁）"""
    if not os.path.exists(META_FILE):
        return {}
    with open(META_FILE, 'r', encoding='utf-8') as f:
        fcntl.flock(f, fcntl.LOCK_SH)
        data = json.load(f)
        fcntl.flock(f, fcntl.LOCK_UN)
    return data

def write_meta(meta):
    """写入小说元数据（加独占锁）"""
    with open(META_FILE, 'w', encoding='utf-8') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        json.dump(meta, f, ensure_ascii=False, indent=2)
        fcntl.flock(f, fcntl.LOCK_UN)

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

            # 从元数据获取自定义名称，否则使用文件名（去掉扩展名）
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
    """启动爬虫（后台线程）"""
    start_url = request.form.get('url')
    novel_name_input = request.form.get('novel_name', '').strip()  # 获取输入的小说名
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

    def task_worker(task_id, start_url, novel_name_input):
        log_message(task_id, f"任务 {task_id} 启动，起始URL: {start_url}")
        if novel_name_input:
            log_message(task_id, f"用户指定小说名: {novel_name_input}")
        try:
            novel_name, txt_path, html_path = run_crawler(
                start_url=start_url,
                output_dir=OUTPUT_DIR,
                log_callback=lambda msg: log_message(task_id, msg),
                override_name=novel_name_input if novel_name_input else None  # 传递参数
            )
            safe_name = os.path.splitext(os.path.basename(html_path))[0]
            task = get_task(task_id)
            task['status'] = 'finished'
            task['novel_name'] = novel_name   # 这里novel_name是run_crawler返回的（可能是自动提取或覆盖后的）
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

    thread = threading.Thread(target=task_worker, args=(task_id, start_url, novel_name_input))
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

    # 安全检查：防止路径遍历
    if '..' in filename or '/' in filename or '\\' in filename:
        return jsonify(success=False, error='非法文件名'), 400

    filepath = os.path.join(OUTPUT_DIR, filename)
    if not os.path.exists(filepath):
        return jsonify(success=False, error='文件不存在'), 404

    # 读取元数据并更新
    meta = read_meta()
    if filename not in meta:
        meta[filename] = {}
    meta[filename]['custom_name'] = new_name.strip()
    meta[filename]['last_modified'] = time.strftime('%Y-%m-%d %H:%M:%S')

    write_meta(meta)

    return jsonify(success=True, new_name=new_name)

# ==================== 新增删除路由 ====================
@app.route('/delete', methods=['POST'])
def delete_novel():
    """删除小说文件及其元数据"""
    data = request.get_json()
    filename = data.get('filename')

    if not filename:
        return jsonify(success=False, error='缺少文件名'), 400

    # 安全检查：防止路径遍历
    if '..' in filename or '/' in filename or '\\' in filename:
        return jsonify(success=False, error='非法文件名'), 400

    filepath = os.path.join(OUTPUT_DIR, filename)
    if not os.path.exists(filepath) or not os.path.isfile(filepath):
        return jsonify(success=False, error='文件不存在'), 404

    try:
        # 删除文件
        os.remove(filepath)

        # 同时删除元数据中对应的条目（可选但推荐）
        meta = read_meta()
        if filename in meta:
            del meta[filename]
            write_meta(meta)

        return jsonify(success=True)
    except Exception as e:
        return jsonify(success=False, error=str(e)), 500

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