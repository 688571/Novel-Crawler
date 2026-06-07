#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
小说爬虫 - 通用版 + 52shuku 专项优化 + 2kzw.la 适配
支持：目录页、单章页、作者页批量抓取、增量更新。
"""

import requests
import urllib3
from bs4 import BeautifulSoup
import time
import random
import os
import re
import hashlib
from urllib.parse import urljoin

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ==================== 配置 ====================
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36',
    'Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Mobile/15E148 Safari/604.1',
    'Mozilla/5.0 (iPad; CPU OS 16_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Mobile/15E148 Safari/604.1',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/114.0'
]

# 示例代理池，用户可以根据需要添加
PROXY_POOL = [
    # 'http://user:pass@host:port',
    # 'http://host:port'
]

# 尝试从外部文件加载代理
if os.path.exists('proxies.txt'):
    with open('proxies.txt', 'r', encoding='utf-8') as f:
        PROXY_POOL.extend([line.strip() for line in f if line.strip() and not line.startswith('#')])

def get_random_headers(url=None):
    ua = random.choice(USER_AGENTS)
    headers = {
        'User-Agent': ua,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Connection': 'keep-alive',
    }
    if url:
        headers['Referer'] = url
    return headers

def sanitize_filename(name):
    return re.sub(r'[\\/*?:"<>|\s]', '_', name)

def get_url_hash(url):
    return hashlib.md5(url.encode('utf-8')).hexdigest()

def find_next_page_link(soup, current_url, is_52shuku=False):
    # 方法1：直接查找文本为“下一页”的链接
    next_a = soup.find('a', string=re.compile(r'下一页|下页|下一章'))
    if next_a and next_a.get('href'):
        href = next_a['href']
        if is_52shuku and href.endswith('/'): return None # 52shuku 下一页如果跳回目录通常是一级结尾
        return urljoin(current_url, href)

    # 方法2：查找 rel="next"
    next_a = soup.find('a', attrs={'rel': 'next'})
    if next_a and next_a.get('href'):
        return urljoin(current_url, next_a['href'])

    # 方法3：查找 class 包含 next 的链接
    for a in soup.find_all('a', class_=re.compile(r'next', re.I)):
        if a.get('href'):
            return urljoin(current_url, a['href'])

    # 方法4：查找文本包含“下一页”的任何链接
    for a in soup.find_all('a', href=True):
        text = a.get_text().strip()
        if '下一页' in text or '下页' in text or '下一章' in text:
            return urljoin(current_url, a['href'])

    return None

# ==================== 核心逻辑 ====================

def fetch_html(session, url, headers=None, proxy=None, retries=3):
    if not headers:
        headers = get_random_headers(url)
    
    current_proxy = proxy
    if not current_proxy and PROXY_POOL:
        current_proxy = random.choice(PROXY_POOL)
        
    for attempt in range(retries):
        try:
            proxies = {'http': current_proxy, 'https': current_proxy} if current_proxy else None
            resp = session.get(url, headers=headers, timeout=15, proxies=proxies, verify=False)
            
            if resp.status_code == 200:
                # 检查常见的防护页面特征
                challenge_keywords = ["安全验证", "Cloudflare", "5秒", "浏览器安全性检查", "请稍候", "Checking your browser", "Verification"]
                if any(k in resp.text for k in challenge_keywords) and len(resp.text) < 5000:
                    print(f"Detected block/challenge for {url} (Attempt {attempt+1})")
                    if attempt < retries - 1:
                        time.sleep(6) # 稍微多等一会儿让挑战过期
                        continue
                
                if 'kanunu8.com' in url:
                    resp.encoding = 'gbk'
                elif 'xbanxia.cc' in url:
                    resp.encoding = 'utf-8'
                else:
                    resp.encoding = resp.apparent_encoding if resp.apparent_encoding else 'utf-8'
                return resp.text
            
            print(f"Fetch failed with status {resp.status_code} for {url} (Attempt {attempt+1}/{retries})")
            if resp.status_code in [403, 503, 429]: # Likely blocked or rate limited
                time.sleep(3 * (attempt + 1))
            
        except Exception as e:
            print(f"Fetch Error ({url}) Attempt {attempt+1}/{retries}: {e}")
            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
            else:
                return None
    return None

def parse_author_page(author_url, log_callback=None, proxy=None):
    """解析作者页作品列表"""
    session = requests.Session()
    session.verify = False
    
    # 在抓取作者页前稍微等待，特别是频繁解析时
    time.sleep(1)
    
    # 确保 URL 中的非 ASCII 字符被正确编码
    from urllib.parse import quote, urlparse
    parsed = urlparse(author_url)
    author_url = f"{parsed.scheme}://{parsed.netloc}{quote(parsed.path)}"
    
    html = fetch_html(session, author_url, proxy=proxy)
    if not html: 
        # 尝试再次获取，可能需要换个 UA 
        html = fetch_html(session, author_url, proxy=proxy)
        if not html:
            raise Exception(f"无法获取作者页: {author_url}。可能是被拦截或地址错误。建议使用代理。")
    
    soup = BeautifulSoup(html, 'html.parser')
    author_name = "未知作者"
    
    # 尝试多种作者名获取方式
    bc = soup.select_one('div.breadcrumbs, .breadcrumb, .place')
    if bc:
        match = re.search(r'([^\s>]+)(?:小说作品|的全部作品|的小說作品)', bc.text)
        if match: author_name = match.group(1)
    
    if author_name == "未知作者":
        h1 = soup.find('h1')
        if h1: 
            author_name = h1.text.replace('的全部小说作品', '').replace('的全部小說作品', '').strip()
    
    if author_name == "未知作者":
        title_tag = soup.find('title')
        if title_tag:
            match = re.search(r'^(.+?)(?:小说作品|的全部作品|的小說作品)', title_tag.text)
            if match: author_name = match.group(1).strip()

    works = []
    # 针对不同站点的特殊处理
    if 'xbanxia' in author_url:
        # 半夏小说列表改进：支持更多层级和样式
        # 常见结构: <div class="list"> <ul> <li> <a href="...">...</a> </li> </ul> </div>
        # 或 <div class="book-item"> <h3> <a ...> </h3> </div>
        for a in soup.select('div.list li a, .list a, .book-item a, .book-list a, .bookname a, .item a'):
            title = a.get_text(strip=True)
            href = a.get('href')
            if href and title and len(title) > 1:
                # 过滤掉非书籍链接
                if any(x in href for x in ['/books/', '/book/', '/novel/']) or re.search(r'/\d+(?:_\d+)?/?$', href):
                    works.append({'title': title, 'url': urljoin(author_url, href)})
    
    # 通用的选择器兜底
    if not works:
        selectors = [
            'article.excerpt header h2 a', 
            'div.mulu-list ul li a',
            '.book-list ul li a',
            '.book-item a',
            'li a[href*="/books/"]',
            'li a[href*="/book/"]',
            'li a[href*="/book5/"]',
            '.author-works a',
            'td a[href*="/book"]'
        ]
        
        for sel in selectors:
            for a in soup.select(sel):
                title = a.get_text(strip=True)
                href = a.get('href')
                if href and len(title) > 1:
                    if 'books/' in href or 'book/' in href or 'novel' in href:
                        title = re.sub(r'^\d+\.\s*', '', title)
                        works.append({'title': title, 'url': urljoin(author_url, href)})
    
    unique_works = []
    seen = set()
    for w in works:
        if w['url'] not in seen:
            seen.add(w['url'])
            unique_works.append(w)
            
    return author_name, unique_works

def run_crawler(start_url, output_dir, log_callback=None, override_name=None, 
                override_author=None, existing_chapter_hashes=None, proxy=None):
    """主抓取流程"""
    session = requests.Session()
    session.verify = False
    
    html = fetch_html(session, start_url, proxy=proxy)
    if not html: return None, None, None
    
    soup = BeautifulSoup(html, 'html.parser')
    
    h1 = soup.find('h1')
    title_raw = h1.text.strip() if h1 else "Unknown"
    novel_name = override_name if override_name else title_raw.split('_')[0].split('(')[0].split(',')[0].strip()
    author_name = override_author if override_author else "佚名" # 默认抓取没做作者解析，这里支持覆盖
    safe_name = sanitize_filename(novel_name)
    
    # 检测是否适合动态“下一页”抓取模式
    content_div = (
        soup.select_one('div.book_con.fix#text') or 
        soup.select_one('#article') or
        soup.select_one('.article-content') or
        soup.select_one('#article-content') or
        soup.select_one('.maintext') or
        soup.select_one('#content') or
        soup.select_one('.content') or
        soup.select_one('#txt') or
        soup.select_one('div.read-content') or
        soup.select_one('article.article-content') or
        soup.select_one('td[width="820"]') or
        soup.select_one('div.main_content') or
        soup.select_one('.post-content') or
        soup.select_one('.chapter-content') or
        soup.select_one('td p')
    )
    
    is_52shuku = '52shuku.net' in start_url
    next_page_link = find_next_page_link(soup, start_url, is_52shuku)
    
    if is_52shuku and not content_div:
        # Detect directory page, grab first chapter for dynamic crawling bypass
        first_chapter_url = None
        base_match = re.search(r'([a-zA-Z0-9]+)\.html', start_url)
        if base_match:
            book_id = base_match.group(1)
            pattern = rf'href=[\"\']([^\"\']*{re.escape(book_id)}(?:_\d+)?\.html)[\"\']'
            found_links = []
            for match in re.finditer(pattern, html):
                found_links.append(urljoin(start_url, match.group(1)))
            if found_links:
                found_links = list(set(found_links))
                if start_url in found_links: found_links.remove(start_url)
                if found_links:
                    # Sort to find the first chapter, usually ends with _2.html or similar
                    def s_key(u):
                        m = re.search(r'_(\d+)\.html$', u)
                        return int(m.group(1)) if m else 0
                    first_chapter_url = sorted(found_links, key=s_key)[0]
        
        if first_chapter_url:
            if log_callback: log_callback(f"检测到目录页，跳转至第一页启动动态抓取: {first_chapter_url}")
            start_url = first_chapter_url
            html = fetch_html(session, start_url, proxy=proxy)
            if html:
                soup = BeautifulSoup(html, 'html.parser')
                content_div = soup.select_one('div.book_con.fix#text')
                next_page_link = find_next_page_link(soup, start_url, is_52shuku)

    use_dynamic = False
    if is_52shuku and content_div and next_page_link:
        use_dynamic = True
    elif content_div and next_page_link:
        use_dynamic = True
        
    if use_dynamic:
        if log_callback: log_callback(f"开始抓取: {novel_name}")
        if log_callback: log_callback("采用动态“下一页”模式抓取")
        
        full_content = []
        os.makedirs(output_dir, exist_ok=True)
        txt_path = os.path.join(output_dir, f"{safe_name}.txt")
        
        current_url = start_url
        visited = set()
        page_num = 0
        
        current_html = html
        current_soup = soup
        
        while current_url and current_url not in visited:
            visited.add(current_url)
            page_num += 1
            
            if log_callback: log_callback(f"处理中 [{page_num}/?] : {current_url}")
            
            c_div = (
                current_soup.select_one('div.book_con.fix#text') or 
                current_soup.select_one('#article') or
                current_soup.select_one('.article-content') or
                current_soup.select_one('#article-content') or
                current_soup.select_one('.maintext') or
                current_soup.select_one('#content') or
                current_soup.select_one('.content') or
                current_soup.select_one('#txt') or
                current_soup.select_one('div.read-content') or
                current_soup.select_one('article.article-content') or
                current_soup.select_one('td[width="820"]') or
                current_soup.select_one('div.main_content') or
                current_soup.select_one('.post-content') or
                current_soup.select_one('.chapter-content') or
                current_soup.select_one('td p')
            )
            
            if c_div:
                for s in c_div(['script', 'ins', 'nav', 'style', 'div', 'iframe', 'button', 'input']): s.decompose()
                
                title_elem = current_soup.find('h1') or current_soup.find('h2') or current_soup.select_one('td strong')
                page_title = title_elem.text.strip() if title_elem else f"第 {page_num} 章节"
                
                text = c_div.get_text(separator='\n')
                lines = [l.strip() for l in text.split('\n') if l.strip()]
                
                filtered_lines = []
                junk_keywords = [
                    '52书库', '52shuku', '传送门', 'APP下载', '半夏小说', 'xbanxia', 'kanunu8', 
                    '看书吧', 'www.', '.com', '.net', '本站域名', '2wxsi', '爱文学', 
                    '喜欢就分享', '收藏本页', '返回首页', '点此报错', '举报反馈',
                    '微信', '公众号', '扫码', '关注', '加入书架', '推荐书目', '最新章节',
                    '下一页', '上一页'
                ]
                for line in lines:
                    line_lower = line.lower()
                    if not any(k.lower() in line_lower for k in junk_keywords):
                        filtered_lines.append("　　" + line)
                
                separator_line = "=" * 40
                page_header = f"\n\n{separator_line}\n【第 {page_num} 页】: {page_title}\n{separator_line}\n\n"
                
                full_content.append(page_header + "\n\n".join(filtered_lines))
            
            next_url = find_next_page_link(current_soup, current_url, is_52shuku)
            
            if is_52shuku and next_url:
                base_match = re.search(r'([a-zA-Z0-9]+)(?:_\d+)?\.html', start_url)
                if base_match:
                    book_id = base_match.group(1)
                    if book_id not in next_url:
                        next_url = None
            
            if not next_url:
                if log_callback: log_callback("未发现有效下一页链接，结束当前小说抓取。")
                break
                
            current_url = next_url
            time.sleep(random.uniform(0.5, 1.5))
            
            p_html = fetch_html(session, current_url, proxy=proxy)
            if not p_html:
                break
            current_soup = BeautifulSoup(p_html, 'html.parser')

        with open(txt_path, "a" if existing_chapter_hashes else "w", encoding="utf-8") as f:
            if not existing_chapter_hashes: 
                header = f"《{novel_name}》\n"
                if author_name and author_name != "佚名":
                    header += f"作者：{author_name}\n"
                header += "\n"
                f.write(header)
            f.write("\n\n".join(full_content))
            
        return novel_name, txt_path, None

    # ==================== 提取目录链接 ====================
    links = []
    
    # 特别针对 52shuku.net 的目录提取
    if '52shuku.net' in start_url:
        # 正则表达式兜底提取所有直接相关的分页链接
        # 允许相对路径和绝对路径
        base_match = re.search(r'([a-zA-Z0-9]+)(?:_\d+)?\.html', start_url)
        if base_match:
            book_id = base_match.group(1)
            # 匹配包含 book_id 的 html 链接，比如 bjZ2n.html or bjZ2n_2.html
            # 甚至是相对路径 /yanqing/18_b/bjZ2n_2.html
            pattern = rf'href=[\"\']([^\"\']*{re.escape(book_id)}(?:_\d+)?\.html)[\"\']'
            for match in re.finditer(pattern, html):
                href = match.group(1)
                full_url = urljoin(start_url, href)
                if full_url not in links: links.append(full_url)
                
        # 52shuku 的目录通常在 <ul class="list clearfix"> <li> <a ...> 
        # 或者 <div class="list"> 下的链接
        # 特别注意有些目录页同时也包含了第一页内容
        for a in soup.select('ul.list li a, .list li a, div.list a, #list a, .mulu a, .book-list a'):
            href = a.get('href')
            if href and ('.html' in href or re.search(r'_\d+\.html$', href)):
                # 排除一些非正文链接
                title = a.get_text(strip=True)
                if not any(x in title for x in ['首页', '返回', '书评', '下载', '投诉', 'APP']):
                    full_url = urljoin(start_url, href)
                    if full_url not in links: links.append(full_url)
        
        # 针对有些直接是正文第一页的情况，提取底部的翻页/分页导航
        if not links or len(links) < 5:
            pagination = soup.select('.pagination a, .pagination2 a, .page-links a, .mulu-list a, .list a')
            for a in pagination:
                href = a.get('href')
                if href and ('.html' in href or re.search(r'_\d+\.html', href)):
                    full_url = urljoin(start_url, href)
                    if full_url not in links: links.append(full_url)
            
            # 如果能从链接中推断出结尾页，自动补全所有的分页列表
            base_url_match = re.search(r'^(.*?)(?:_\d+)?\.html', start_url)
            if base_url_match:
                base_url = base_url_match.group(1)
                max_page = 1
                for link in links:
                    m = re.search(r'_(\d+)\.html', link)
                    if m:
                        page_num = int(m.group(1))
                        if page_num > max_page:
                            max_page = page_num
                
                if max_page > 1:
                    for i in range(1, max_page + 1):
                        p_url = f"{base_url}_{i}.html" if i > 1 else f"{base_url}.html"
                        if p_url not in links: links.append(p_url)
        
        # 排除掉起始页自身（如果已经在列表中，移动到第一位）
        if start_url in links:
            links.remove(start_url)
        
        # 对于 52shuku.net，过滤掉纯目录页（没有 _数字.html 且在当前获取中没有正文框）
        is_52shuku_dir_only = False
        if '52shuku.net' in start_url:
            if not soup.select_one('div.book_con.fix#text'):
                is_52shuku_dir_only = True
            
            # 删除所有被错误添加的没有尾缀的可能是纯目录的链接
            base_url_match = re.search(r'^(.*?)(?:_\d+)?\.html', start_url)
            if base_url_match:
                dir_url = f"{base_url_match.group(1)}.html"
                if dir_url in links and start_url != dir_url:
                    links.remove(dir_url)
                    
        if not is_52shuku_dir_only:
            links.insert(0, start_url)
        
        # 排序：确保 _2.html 在 _10.html 之前
        def sort_key(url):
            match = re.search(r'_(\d+)\.html$', url)
            if match: return int(match.group(1))
            return 0
        
        if len(links) > 1:
            # 只有当链接看起来像是分页（带有 _数字.html）时才排序
            if any('_' in l for l in links[1:]):
                first = links[0]
                rest = sorted(links[1:], key=sort_key)
                links = [first] + rest

        # 去重并保持顺序
        seen = set()
        links = [x for x in links if not (x in seen or seen.add(x))]

        # 如果提取出的链接太少且不包含 _ 数字，可能这不是目录页而是单页
    
    # 特别针对 xbanxia.cc 的目录提取
    if 'xbanxia.cc' in start_url:
        # 半夏一般在 <div id="list"> 下的 <a href="...">
        # 或者 <div class="list">
        for a in soup.select('#list a, .list a, div.list ul li a'):
            href = a.get('href')
            if href and ('.html' in href or re.search(r'/\d+/\d+\.html', href)):
                full_url = urljoin(start_url, href)
                if full_url not in links: links.append(full_url)
    
    # 针对 2wxsi.com 的目录提取
    if '2wxsi' in start_url:
        for a in soup.select('.book-item a, .book-list a, .list a, .item a'):
            href = a.get('href')
            if href and ('.html' in href or re.search(r'/\d+/\d+/?$', href)):
                full_url = urljoin(start_url, href)
                if full_url not in links: links.append(full_url)
    
    # ==================== 新增：针对 2kzw.la 的目录提取 ====================
    if '2kzw.la' in start_url:
        # 使用 dd a 选择器提取所有章节链接
        for a in soup.select('dd a'):
            href = a.get('href')
            if href and not href.startswith('#') and not href.startswith('javascript:'):
                full_url = urljoin(start_url, href)
                if full_url not in links:
                    links.append(full_url)
        # 按链接中的数字 ID 排序（2kzw.la 的 URL 格式为 .../数字.html）
        def numeric_key(url):
            match = re.search(r'/(\d+)\.html$', url)
            return int(match.group(1)) if match else 0
        if links:
            links.sort(key=numeric_key)
        # 如果成功提取到链接，则跳过后续的通用提取逻辑
        if links:
            # 小说名优化：若 h1 不准确，尝试从 title 中提取
            if novel_name == "Unknown" or len(novel_name) < 2:
                title_tag = soup.find('title')
                if title_tag:
                    title_text = title_tag.get_text().strip()
                    # 格式如 "你或像你的人_明开夜合_2k小说"
                    parts = re.split(r'[_\-|]', title_text)
                    novel_name = max(parts, key=len).strip() if len(parts) >= 2 else parts[0].strip()
                    safe_name = sanitize_filename(novel_name)
            # 作者提取（可选，如果不提供覆盖）
            if author_name == "佚名" and not override_author:
                # 尝试从 title 中提取作者
                title_tag = soup.find('title')
                if title_tag:
                    title_text = title_tag.get_text().strip()
                    parts = re.split(r'[_\-|]', title_text)
                    if len(parts) >= 2:
                        # 假设第二部分是作者
                        author_name = parts[1].strip()
            # 直接跳转到链接处理，不再执行后面的通用选择器
            pass  # 注意：不能直接 return，需要继续往下走到链接处理部分
    # ===================================================================
    
    if not links:
        dir_selectors = [
            'ul.list.clearfix li.mulu a',
            '#list dd a',
            '.book-list ul li a',
            '#chapterlist li a',
            '.section-list li a',
            'td a[href*=".html"]'
        ]
        
        found_links = []
        for sel in dir_selectors:
            found_links.extend(soup.select(sel))
        
        if found_links:
            for a in found_links:
                full_url = urljoin(start_url, a.get('href'))
                if full_url not in links: links.append(full_url)
        else:
            links = [start_url]

    if log_callback: log_callback(f"开始抓取: {novel_name}")
    if log_callback: log_callback(f"找到 {len(links)} 个分页/章节")
    
    full_content = []
    os.makedirs(output_dir, exist_ok=True)
    txt_path = os.path.join(output_dir, f"{safe_name}.txt")
    
    for i, link in enumerate(links, 1):
        url_hash = get_url_hash(link)
        if existing_chapter_hashes and url_hash in existing_chapter_hashes:
            if log_callback: log_callback(f"跳过已抓取页 [{i}]: {link}")
            continue
            
        if log_callback: log_callback(f"处理中 [{i}/{len(links)}]: {link}")
        
        p_html = fetch_html(session, link, proxy=proxy)
        if p_html:
            p_soup = BeautifulSoup(p_html, 'html.parser')
            # 内容选择器
            content_div = (
                p_soup.select_one('div.book_con.fix#text') or 
                p_soup.select_one('#article') or
                p_soup.select_one('.article-content') or
                p_soup.select_one('#article-content') or
                p_soup.select_one('.maintext') or
                p_soup.select_one('#content') or
                p_soup.select_one('.content') or
                p_soup.select_one('#txt') or
                p_soup.select_one('div.read-content') or
                p_soup.select_one('article.article-content') or
                p_soup.select_one('td[width="820"]') or
                p_soup.select_one('div.main_content') or
                p_soup.select_one('.post-content') or
                p_soup.select_one('.chapter-content') or
                p_soup.select_one('td p')
            )
            
            if content_div:
                # 移除干扰元素
                for s in content_div(['script', 'ins', 'nav', 'style', 'div', 'iframe', 'button', 'input']): s.decompose()
                
                title_elem = p_soup.find('h1') or p_soup.find('h2') or p_soup.select_one('td strong')
                page_title = title_elem.text.strip() if title_elem else f"第 {i} 章节"
                
                # 特殊处理 52shuku：有些内容在 <p> 标签中
                # 如果没有明显的段落，尝试直接获取文本并按行分割
                text = content_div.get_text(separator='\n')
                lines = [l.strip() for l in text.split('\n') if l.strip()]
                
                filtered_lines = []
                # 扩展垃圾词库
                junk_keywords = [
                    '52书库', '52shuku', '传送门', 'APP下载', '半夏小说', 'xbanxia', 'kanunu8', 
                    '看书吧', 'www.', '.com', '.net', '本站域名', '2wxsi', '爱文学', 
                    '喜欢就分享', '收藏本页', '返回首页', '点此报错', '举报反馈',
                    '微信', '公众号', '扫码', '关注', '加入书架', '推荐书目', '最新章节'
                ]
                for line in lines:
                    line_lower = line.lower()
                    if not any(k.lower() in line_lower for k in junk_keywords):
                        filtered_lines.append("　　" + line)
                
                separator_line = "=" * 40
                page_header = f"\n\n{separator_line}\n【第 {i} 页】: {page_title}\n{separator_line}\n\n"
                
                full_content.append(page_header + "\n\n".join(filtered_lines))
        
        # 频率限制
        time.sleep(random.uniform(0.3, 1.0))
    
    with open(txt_path, "a" if existing_chapter_hashes else "w", encoding="utf-8") as f:
        if not existing_chapter_hashes: 
            header = f"《{novel_name}》\n"
            if author_name and author_name != "佚名":
                header += f"作者：{author_name}\n"
            header += "\n"
            f.write(header)
        f.write("\n\n".join(full_content))
    
    return novel_name, txt_path, None


if __name__ == "__main__":
    import sys
    import json
    
    if len(sys.argv) > 1:
        if sys.argv[1] == "--test":
            proxy = sys.argv[2] if len(sys.argv) > 2 else None
            if not proxy:
                print(json.dumps({"success": False, "message": "未提供代理地址"}))
                sys.exit(0)
            
            session = requests.Session()
            session.verify = False
            headers = get_random_headers("https://www.google.com")
            try:
                proxies = {'http': proxy, 'https': proxy}
                start_time = time.time()
                target_test = "http://www.baidu.com" if "xbanxia" not in (proxy or "") else "https://www.google.com"
                resp = session.get(target_test, headers=headers, timeout=10, proxies=proxies)
                elapsed = time.time() - start_time
                if resp.status_code == 200:
                    print(json.dumps({"success": True, "message": f"连接成功", "latency": round(elapsed, 3)}))
                else:
                    print(json.dumps({"success": False, "message": f"连接失败 (HTTP {resp.status_code})"}))
            except Exception as e:
                print(json.dumps({"success": False, "message": f"连接异常: {str(e)}"}))
            sys.exit(0)
        
        if sys.argv[1] == "--batch-test":
            from concurrent.futures import ThreadPoolExecutor, as_completed
            proxy_list = json.loads(sys.argv[2])
            test_target = sys.argv[3] if len(sys.argv) > 3 else "https://www.google.com"
            
            results = []
            def test_one(p):
                session = requests.Session()
                session.verify = False
                headers = get_random_headers(test_target)
                try:
                    proxies = {'http': p, 'https': p}
                    start_time = time.time()
                    # 使用指定的测试目标
                    resp = session.get(test_target, headers=headers, timeout=5, proxies=proxies)
                    elapsed = time.time() - start_time
                    if resp.status_code == 200:
                        return {"proxy": p, "success": True, "latency": round(elapsed, 3)}
                except:
                    pass
                return {"proxy": p, "success": False, "latency": 999}

            with ThreadPoolExecutor(max_workers=15) as executor:
                futures = [executor.submit(test_one, p) for p in proxy_list]
                for future in as_completed(futures):
                    res = future.result()
                    if res["success"]:
                        results.append(res)
            
            # 按延迟排序
            results.sort(key=lambda x: x['latency'])
            print(json.dumps(results))
            sys.exit(0)

        if sys.argv[1] == "--debug":
            url = sys.argv[2]
            proxy = sys.argv[3] if len(sys.argv) > 3 else None
            session = requests.Session()
            session.verify = False
            html = fetch_html(session, url, proxy=proxy)
            if not html:
                print(json.dumps({"success": False, "error": "Fetch failed"}))
                sys.exit(0)
            
            soup = BeautifulSoup(html, 'html.parser')
            links = []
            
            # Use the same logic as in run_crawler to find links
            base_match = re.search(r'([a-zA-Z0-9]+)(?:_\d+)?\.html', url)
            if base_match:
                book_id = base_match.group(1)
                pattern = rf'href=[\"\']([^\"\']*{re.escape(book_id)}(?:_\d+)?\.html)[\"\']'
                for match in re.finditer(pattern, html):
                    links.append(urljoin(url, match.group(1)))
            
            # Pagination links
            pagination = soup.select('.pagination a, .pagination2 a, .page-links a, .mulu-list a, .list a, ul.list li a')
            for a in pagination:
                links.append(urljoin(url, a.get('href')))
            
            unique_links = sorted(list(set([l for l in links if '.html' in l])))
            
            # Content check
            content_div = (
                soup.select_one('div.book_con.fix#text') or 
                soup.select_one('#article') or
                soup.select_one('#content') or
                soup.select_one('.content')
            )
            content_sample = content_div.get_text()[:1000] if content_div else "No content found"
            
            # Title
            h1 = soup.find('h1')
            title = h1.text.strip() if h1 else "Unknown"
            
            print(json.dumps({
                "success": True,
                "title": title,
                "len": len(html),
                "links_count": len(unique_links),
                "links_sample": unique_links[:50],
                "content_sample": content_sample
            }))
            sys.exit(0)

        if sys.argv[1] == "--author":
            author_url = sys.argv[2]
            proxy = sys.argv[3] if len(sys.argv) > 3 else None
            try:
                name, works = parse_author_page(author_url, proxy=proxy)
                print(json.dumps({"author": name, "works": works}))
            except Exception as e:
                print(json.dumps({"error": str(e)}))
            sys.exit(0)

    # 获取参数
    target = sys.argv[1] if len(sys.argv) > 1 else "https://www.52shuku.net/yanqing/18_b/bjZ2n.html"
    out_dir = sys.argv[2] if len(sys.argv) > 2 else "./novels"
    proxy_arg = sys.argv[3] if len(sys.argv) > 3 else None
    
    # 额外支持 --title 和 --author 参数
    ov_name = None
    ov_author = None
    if "--title" in sys.argv:
        idx = sys.argv.index("--title")
        if idx + 1 < len(sys.argv): ov_name = sys.argv[idx+1]
    if "--author-name" in sys.argv:
        idx = sys.argv.index("--author-name")
        if idx + 1 < len(sys.argv): ov_author = sys.argv[idx+1]

    run_crawler(target, out_dir, log_callback=print, override_name=ov_name, override_author=ov_author, proxy=proxy_arg)
