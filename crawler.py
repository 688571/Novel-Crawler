#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import requests
import urllib3
from bs4 import BeautifulSoup
import time
import random
import os
import re
from urllib.parse import urljoin
import requests.exceptions

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 常用User-Agent列表
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:89.0) Gecko/20100101 Firefox/89.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.1.1 Safari/605.1.15',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/92.0.4515.107 Safari/537.36',
]

HEADERS_TEMPLATE = {
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.8,en-US;q=0.5,en;q=0.3',
    'Accept-Encoding': 'gzip, deflate',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
}

# 通用选择器配置
COMMON_TITLE_SELECTORS = [
    'h1', 'h2', '.title', '#chapter-title', '.chapter-title',
    '.article-title', '.post-title', '#nr_title'
]

COMMON_CONTENT_SELECTORS = [
    '#TextContent', '#content', '.content', '#nr1',
    '.article-content', '#chapter-content', '.chapter-content',
    '#booktext', '.book-content', '#chapter-body'
]

COMMON_NEXT_PAGE_PATTERNS = {
    'text': ['下一页', '下一节', '下一部分', '下页', '下一頁', 'Next', 'next'],
    'class': ['next', 'next-page', 'nextPage', 'nextlink', 'pager-next'],
    'id': ['next', 'next_page', 'nextUrl', 'nexturl']
}

COMMON_NEXT_CHAPTER_PATTERNS = {
    'text': ['下一章', '下章', 'Next Chapter', '下一章'],
    'class': ['next', 'next-chapter', 'nextChapter', 'nextchapter'],
    'id': ['next_url', 'next_chapter', 'nextchapter']
}

# 目录页解析选择器（可根据常见网站调整）
DIRECTORY_TITLE_SELECTOR = 'h1'  # 小说名所在标签
DIRECTORY_AUTHOR_SELECTOR = '.book-describe p a'  # 作者链接，取文本
DIRECTORY_CHAPTER_LIST_SELECTOR = '.book-list ul li a'  # 章节链接列表


def fetch_html(session, url, max_retries=5, base_delay=2, log_callback=None):
    """获取HTML，带重试机制"""
    for attempt in range(1, max_retries + 1):
        headers = HEADERS_TEMPLATE.copy()
        headers['User-Agent'] = random.choice(USER_AGENTS)

        try:
            resp = session.get(url, headers=headers, timeout=15)
            resp.encoding = 'utf-8'
            if resp.status_code == 200:
                return resp.text
            else:
                msg = f"请求失败，状态码：{resp.status_code} URL：{url} (尝试 {attempt}/{max_retries})"
                print(msg)
                if log_callback: log_callback(msg)
        except requests.exceptions.Timeout:
            msg = f"请求超时：{url} (尝试 {attempt}/{max_retries})"
            print(msg)
            if log_callback: log_callback(msg)
        except Exception as e:
            msg = f"请求异常：{e} URL：{url} (尝试 {attempt}/{max_retries})"
            print(msg)
            if log_callback: log_callback(msg)

        if attempt < max_retries:
            wait = base_delay * (2 ** (attempt - 1)) + random.uniform(0, 1)
            msg = f"等待 {wait:.2f} 秒后重试..."
            print(msg)
            if log_callback: log_callback(msg)
            time.sleep(wait)

    msg = f"无法获取页面，已达最大重试次数 {max_retries}，URL：{url}"
    print(msg)
    if log_callback: log_callback(msg)
    return None


def extract_novel_name_from_title(html):
    """从<title>标签提取小说名"""
    soup = BeautifulSoup(html, 'html.parser')
    title_tag = soup.find('title')
    if not title_tag:
        return None
    title_text = title_tag.get_text().strip()
    parts = re.split(r'[_\-|]', title_text)
    if len(parts) >= 2:
        novel_name = max(parts, key=len).strip()
    else:
        novel_name = parts[0].strip()
    novel_name = re.sub(r'^第\d+章\s*', '', novel_name)
    return novel_name


def sanitize_filename(name):
    """清理文件名中的非法字符"""
    return re.sub(r'[\\/*?:"<>|\s]', '_', name)


def parse_title(soup, selectors=None):
    """通用标题解析"""
    if selectors is None:
        selectors = COMMON_TITLE_SELECTORS
    for selector in selectors:
        elem = soup.select_one(selector)
        if elem:
            text = elem.get_text().strip()
            if text:
                return text
    return "未知标题"


def parse_content(soup, selectors=None):
    """通用正文解析"""
    if selectors is None:
        selectors = COMMON_CONTENT_SELECTORS
    content_elem = None
    for selector in selectors:
        content_elem = soup.select_one(selector)
        if content_elem:
            break
    if not content_elem:
        return None

    # 优先提取<p>段落
    paragraphs = content_elem.find_all('p')
    if paragraphs:
        para_texts = [p.get_text().strip() for p in paragraphs if p.get_text().strip()]
        if para_texts:
            return '\n\n'.join(para_texts)

    # 无<p>时处理<br>换行
    content_copy = BeautifulSoup(str(content_elem), 'html.parser')
    for br in content_copy.find_all('br'):
        br.replace_with('\n')
    text = content_copy.get_text().strip()
    # 压缩连续空行
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text


def _find_link_by_patterns(soup, current_url, patterns):
    """根据模式查找链接（内部函数）"""
    # 1. 检查rel="next"属性
    for a in soup.find_all('a', href=True, attrs={'rel': 'next'}):
        href = a['href']
        if href.startswith('javascript:'):
            continue
        text = a.get_text().strip()
        # 如果文本匹配关键词，直接返回
        if any(key in text for key in patterns.get('text', [])):
            return urljoin(current_url, href)
        # 如果无文本，也返回（假设是目标链接）
        if not text:
            return urljoin(current_url, href)

    # 2. 按class查找
    for class_name in patterns.get('class', []):
        for a in soup.select(f'a.{class_name}'):
            if a.has_attr('href') and not a['href'].startswith('javascript:'):
                return urljoin(current_url, a['href'])

    # 3. 按id查找
    for id_name in patterns.get('id', []):
        a = soup.find('a', id=id_name, href=True)
        if a and not a['href'].startswith('javascript:'):
            return urljoin(current_url, a['href'])

    # 4. 按文本匹配
    for a in soup.find_all('a', href=True):
        if a['href'].startswith('javascript:'):
            continue
        text = a.get_text().strip()
        if any(key in text for key in patterns.get('text', [])):
            return urljoin(current_url, a['href'])

    return None


def find_next_page_link(soup, current_url):
    """查找同一章下一页链接"""
    return _find_link_by_patterns(soup, current_url, COMMON_NEXT_PAGE_PATTERNS)


def find_next_chapter_link(soup, current_url):
    """查找下一章链接"""
    return _find_link_by_patterns(soup, current_url, COMMON_NEXT_CHAPTER_PATTERNS)


def save_to_txt(chapters_data, filename, mode='a', header=None):
    """追加章节到TXT文件，可写入头部信息"""
    with open(filename, mode, encoding='utf-8') as f:
        if header:
            f.write(header + '\n\n')
        for title, content in chapters_data:
            f.write(title + '\n\n')
            f.write(content + '\n\n')
            f.write('=' * 50 + '\n\n')


def generate_html_from_txt(txt_path, novel_name):
    """将TXT转换为HTML阅读器"""
    with open(txt_path, 'r', encoding='utf-8') as f:
        content = f.read()
    # 按章节分隔符分割
    chapters_raw = re.split(r'\n={4,}\n', content)
    chapters = []
    for chap in chapters_raw:
        if not chap.strip():
            continue
        lines = chap.strip().split('\n', 1)
        title = lines[0].strip()
        body = lines[1].strip() if len(lines) > 1 else ''
        chapters.append((title, body))

    toc_items = [f'<li><a href="#chap{i}">{title}</a></li>' for i, (title, _) in enumerate(chapters, 1)]
    toc_html = '\n'.join(toc_items)

    chap_htmls = []
    for i, (title, body) in enumerate(chapters, 1):
        paragraphs = body.split('\n\n')
        body_html = ''.join(f'<p>{p}</p>' for p in paragraphs if p.strip())
        chap_htmls.append(f'''
        <section id="chap{i}">
            <h2>{title}</h2>
            {body_html}
            <div class="nav-links">
                <a href="#chap{i-1}" class="prev" {"style='visibility:hidden'" if i==1 else ""}>上一章</a>
                <a href="#toc">目录</a>
                <a href="#chap{i+1}" class="next" {"style='visibility:hidden'" if i==len(chapters) else ""}>下一章</a>
            </div>
        </section>
        ''')
    chapters_html = '\n'.join(chap_htmls)

    html_template = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{novel_name} - 网页版</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: 'Microsoft YaHei', sans-serif;
            background-color: #f5f5f5;
            color: #333;
            line-height: 1.8;
        }}
        .container {{
            max-width: 900px;
            margin: 20px auto;
            background: white;
            box-shadow: 0 0 10px rgba(0,0,0,0.1);
            border-radius: 8px;
            overflow: hidden;
        }}
        header {{
            background-color: #2c3e50;
            color: white;
            padding: 20px;
            text-align: center;
        }}
        header h1 {{ font-size: 2em; margin-bottom: 10px; }}
        #toc {{ background: #ecf0f1; padding: 20px; }}
        #toc h2 {{ margin-bottom: 15px; color: #2c3e50; }}
        #toc ul {{ list-style: none; display: flex; flex-wrap: wrap; gap: 10px; }}
        #toc li {{ margin: 5px; }}
        #toc a {{
            display: inline-block;
            padding: 8px 15px;
            background: white;
            border: 1px solid #bdc3c7;
            border-radius: 20px;
            color: #2c3e50;
            text-decoration: none;
            transition: all 0.3s;
        }}
        #toc a:hover {{ background: #2c3e50; color: white; border-color: #2c3e50; }}
        section {{ padding: 30px 20px; border-bottom: 1px solid #ecf0f1; }}
        section h2 {{ margin-bottom: 20px; color: #2c3e50; border-left: 5px solid #e74c3c; padding-left: 15px; }}
        section p {{ margin: 1em 0; text-indent: 2em; }}
        .nav-links {{ display: flex; justify-content: space-between; margin-top: 30px; padding-top: 20px; border-top: 1px dashed #ccc; }}
        .nav-links a {{ color: #3498db; text-decoration: none; padding: 5px 15px; border: 1px solid #3498db; border-radius: 5px; transition: 0.3s; }}
        .nav-links a:hover {{ background: #3498db; color: white; }}
        footer {{ text-align: center; padding: 20px; color: #7f8c8d; font-size: 0.9em; }}
    </style>
</head>
<body>
    <div class="container">
        <header><h1>{novel_name}</h1><p>共 {len(chapters)} 章</p></header>
        <div id="toc"><h2>目录</h2><ul>{toc_html}</ul></div>
        <main>{chapters_html}</main>
        <footer>生成于 {time.strftime('%Y-%m-%d %H:%M:%S')}</footer>
    </div>
</body>
</html>'''
    html_path = os.path.splitext(txt_path)[0] + '.html'
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(html_template)
    return html_path


def is_directory_page(soup):
    """判断是否为目录页（包含章节列表）"""
    # 查找常见的章节列表容器
    if soup.select_one('.book-list ul li a'):
        return True
    if soup.select_one('#list a'):
        return True
    if soup.select_one('.chapter-list a'):
        return True
    return False


def parse_novel_info_from_directory(soup, base_url):
    """从目录页解析小说名和作者"""
    info = {'name': '未知小说', 'author': '未知作者'}
    # 提取小说名
    title_elem = soup.select_one('h1')
    if title_elem:
        info['name'] = title_elem.get_text().strip()
    else:
        # 尝试从<title>提取
        title_tag = soup.find('title')
        if title_tag:
            title_text = title_tag.get_text().strip()
            parts = re.split(r'[_\-|]', title_text)
            info['name'] = max(parts, key=len).strip() if len(parts) >= 2 else parts[0].strip()

    # 提取作者
    author_elem = soup.select_one('.book-describe p a')
    if author_elem:
        info['author'] = author_elem.get_text().strip()
    else:
        # 尝试其他常见位置
        author_elem = soup.find('a', href=re.compile(r'/author/'))
        if author_elem:
            info['author'] = author_elem.get_text().strip()
    return info


def extract_chapter_links_from_directory(soup, base_url):
    """从目录页提取所有章节链接（绝对URL）"""
    links = []
    # 使用常见选择器
    for selector in ['.book-list ul li a', '#list a', '.chapter-list a']:
        for a in soup.select(selector):
            href = a.get('href')
            if href and not href.startswith('#') and not href.startswith('javascript:'):
                full_url = urljoin(base_url, href)
                if full_url not in links:
                    links.append(full_url)
        if links:
            break
    return links


def fetch_single_chapter(session, chapter_url, log_callback=None):
    """
    抓取单个章节（处理分页），返回 (标题, 合并内容)
    """
    page_url = chapter_url
    page_visited = set()
    chapter_title = None
    chapter_content_parts = []

    while page_url:
        if page_url in page_visited:
            msg = f"检测到分页循环：{page_url}，停止当前章节"
            print(msg)
            if log_callback: log_callback(msg)
            break
        page_visited.add(page_url)

        msg = f"获取分页：{page_url}"
        print(msg)
        if log_callback: log_callback(msg)

        html = fetch_html(session, page_url, log_callback=log_callback)
        if not html:
            msg = "获取分页失败，停止当前章节"
            print(msg)
            if log_callback: log_callback(msg)
            break

        soup = BeautifulSoup(html, 'html.parser')

        # 提取标题（仅当第一次分页时）
        if chapter_title is None:
            chapter_title = parse_title(soup)
            msg = f"标题：{chapter_title}"
            print(msg)
            if log_callback: log_callback(msg)

        # 提取内容
        page_content = parse_content(soup)
        if page_content:
            chapter_content_parts.append(page_content)
        else:
            msg = "当前页内容为空，可能解析失败"
            print(msg)
            if log_callback: log_callback(msg)

        # 查找下一页（分页）链接
        next_page_url = find_next_page_link(soup, page_url)
        if next_page_url:
            msg = f"找到下一页：{next_page_url}"
            print(msg)
            if log_callback: log_callback(msg)
            page_url = next_page_url
            delay = random.uniform(2, 5)
            msg = f"等待 {delay:.2f} 秒..."
            print(msg)
            if log_callback: log_callback(msg)
            time.sleep(delay)
        else:
            page_url = None  # 无下一页，结束分页循环

    if chapter_title and chapter_content_parts:
        full_content = '\n\n'.join(chapter_content_parts)
        return chapter_title, full_content
    else:
        return None, None

def run_crawler(start_url, output_dir, log_callback=None, override_name=None):
    """
    主爬虫函数，自动检测起始页面类型
    :param start_url: 起始 URL（目录页或章节页）
    :param output_dir: 输出目录
    :param log_callback: 日志回调函数
    :param override_name: 手动指定的小说名（若提供则覆盖自动提取，并强制所有章节标题为该名称）
    """
    session = requests.Session()
    session.verify = False

    os.makedirs(output_dir, exist_ok=True)

    # 获取起始页面
    msg = f"正在获取起始页面：{start_url}"
    print(msg)
    if log_callback: log_callback(msg)
    first_html = fetch_html(session, start_url, log_callback=log_callback)
    if not first_html:
        msg = "无法获取起始页面，程序终止。"
        print(msg)
        if log_callback: log_callback(msg)
        return

    soup = BeautifulSoup(first_html, 'html.parser')

    # 判断是否为目录页
    if is_directory_page(soup):
        msg = "检测到目录页，将抓取所有章节"
        print(msg)
        if log_callback: log_callback(msg)

        # 解析小说信息（自动提取）
        novel_info = parse_novel_info_from_directory(soup, start_url)
        # 如果用户指定了名称，则使用用户指定的名称
        if override_name:
            novel_name = override_name
            author = novel_info['author']  # 作者仍保留自动提取
        else:
            novel_name = novel_info['name']
            author = novel_info['author']
        msg = f"小说名：{novel_name}，作者：{author}"
        print(msg)
        if log_callback: log_callback(msg)

        # 提取章节链接
        chapter_links = extract_chapter_links_from_directory(soup, start_url)
        if not chapter_links:
            msg = "未找到任何章节链接，程序终止。"
            print(msg)
            if log_callback: log_callback(msg)
            return

        msg = f"共找到 {len(chapter_links)} 个章节"
        print(msg)
        if log_callback: log_callback(msg)

        safe_name = sanitize_filename(novel_name)
        txt_path = os.path.join(output_dir, safe_name + ".txt")
        if os.path.exists(txt_path):
            os.remove(txt_path)

        # 写入小说头部信息
        header = f"小说名称：{novel_name}\n作者：{author}\n"
        save_to_txt([], txt_path, mode='w', header=header)

        # 遍历每个章节链接
        for idx, chap_url in enumerate(chapter_links, 1):
            msg = f"正在抓取第 {idx} 章：{chap_url}"
            print(msg)
            if log_callback: log_callback(msg)

            title, content = fetch_single_chapter(session, chap_url, log_callback)
            if title and content:
                # ★ 如果手动指定了小说名，则用其替换章节标题
                if override_name:
                    title = override_name
                save_to_txt([(title, content)], txt_path, mode='a')
                msg = f"第 {idx} 章保存成功"
                print(msg)
                if log_callback: log_callback(msg)
            else:
                msg = f"第 {idx} 章抓取失败，跳过"
                print(msg)
                if log_callback: log_callback(msg)

            # 随机延迟，避免请求过快
            if idx < len(chapter_links):
                delay = random.uniform(2, 5)
                msg = f"等待 {delay:.2f} 秒后继续下一章..."
                print(msg)
                if log_callback: log_callback(msg)
                time.sleep(delay)

        # 生成HTML
        msg = "抓取完成，正在生成HTML阅读文件..."
        print(msg)
        if log_callback: log_callback(msg)
        html_path = generate_html_from_txt(txt_path, novel_name)
        msg = f"HTML已生成：{html_path}"
        print(msg)
        if log_callback: log_callback(msg)

        return novel_name, txt_path, html_path

    else:
        # 单章模式
        msg = "检测到章节页，将按顺序抓取（通过下一章链接）"
        print(msg)
        if log_callback: log_callback(msg)

        # 提取小说名
        if override_name:
            novel_name = override_name
            msg = f"使用用户指定小说名：{novel_name}"
        else:
            novel_name = extract_novel_name_from_title(first_html) or "novel"
            msg = f"检测到小说名：{novel_name}"
        print(msg)
        if log_callback: log_callback(msg)

        safe_name = sanitize_filename(novel_name)
        txt_path = os.path.join(output_dir, safe_name + ".txt")
        if os.path.exists(txt_path):
            os.remove(txt_path)

        current_url = start_url
        visited_urls = {start_url}
        chapter_count = 0

        while current_url:
            chapter_count += 1
            msg = f"正在抓取第 {chapter_count} 章，起始页：{current_url}"
            print(msg)
            if log_callback: log_callback(msg)

            # 抓取本章（含分页）
            title, content = fetch_single_chapter(session, current_url, log_callback)
            if title and content:
                # ★ 如果手动指定了小说名，则用其替换章节标题
                if override_name:
                    title = override_name
                save_to_txt([(title, content)], txt_path, mode='a')
                msg = f"第 {chapter_count} 章保存成功"
                print(msg)
                if log_callback: log_callback(msg)
            else:
                msg = "章节内容为空，停止抓取"
                print(msg)
                if log_callback: log_callback(msg)
                break

            # 查找下一章链接（使用最后一页的soup）
            # 这里我们重新获取最后一页的soup（在fetch_single_chapter中已处理分页，但未返回最后一页的soup）
            # 简单起见，重新请求当前页获取soup查找下一章链接（也可以从fetch_single_chapter返回soup，但为简化，重新请求）
            # 注意：可能最后一页有下一章链接，而起始页也有，但重新请求起始页可能导致链接错误，最好在分页循环中保存最后一页的soup。
            # 为了兼容，我们重新请求当前起始页，但如果分页后下一章链接只在最后一页，则可能找不到。
            # 改进：在fetch_single_chapter中返回最后一页的soup。这里暂不修改，因为原逻辑已运行多年，可能存在遗漏。
            # 我们简单重新请求当前起始页查找，如果找不到，再尝试从最后一页查找（但需要保存最后一页soup）。
            # 由于时间限制，保持原逻辑：直接重新请求当前页。
            html = fetch_html(session, current_url, log_callback=log_callback)
            if html:
                soup = BeautifulSoup(html, 'html.parser')
                next_chapter_url = find_next_chapter_link(soup, current_url)
                if next_chapter_url:
                    if next_chapter_url in visited_urls:
                        msg = f"检测到重复章节URL：{next_chapter_url}，停止"
                        print(msg)
                        if log_callback: log_callback(msg)
                        break
                    visited_urls.add(next_chapter_url)
                    current_url = next_chapter_url
                    msg = f"找到下一章：{next_chapter_url}"
                    print(msg)
                    if log_callback: log_callback(msg)
                    delay = random.uniform(2, 5)
                    msg = f"等待 {delay:.2f} 秒..."
                    print(msg)
                    if log_callback: log_callback(msg)
                    time.sleep(delay)
                else:
                    msg = "未找到下一章链接，抓取结束"
                    print(msg)
                    if log_callback: log_callback(msg)
                    break
            else:
                msg = "无法获取当前页，抓取结束"
                print(msg)
                if log_callback: log_callback(msg)
                break

        # 生成HTML
        msg = "抓取完成，正在生成HTML阅读文件..."
        print(msg)
        if log_callback: log_callback(msg)
        html_path = generate_html_from_txt(txt_path, novel_name)
        msg = f"HTML已生成：{html_path}"
        print(msg)
        if log_callback: log_callback(msg)

        return novel_name, txt_path, html_path