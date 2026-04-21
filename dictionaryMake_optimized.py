import time
import requests
import re
import hashlib
import json
import uuid
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

# ================= 统一下载器的环境配置与鉴权 =================
device_token = "910D166A-736E-3231-8B21-8D12DFD75F16"
SALT = "lPQDb9AKO7$LjkPG"

# 完全与 downloader 保持一致的 API 请求头
headers = {
    'Host': 'api.sfacg.com',
    'accept-charset': 'UTF-8',
    'authorization': 'Basic YW5kcm9pZHVzZXI6MWEjJDUxLXl0Njk7KkFjdkBxeHE=',
    'accept': 'application/vnd.sfacg.api+json;version=1',
    'user-agent': f'boluobao/5.1.54(android;35)/OPPO/{device_token.lower()}/OPPO',
    'accept-encoding': 'gzip',
    'Content-Type': 'application/json; charset=UTF-8'
}

# PC 端专用的网页爬取头（用于获取正确明文）
headers_PC = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/61.0.3163.100 Safari/537.36'
}

# 统一下载器的加密签名算法
def get_sign(nonce, timestamp, device_token):
    long_nonce = (nonce * 4).encode("ascii")
    def index_calc(x):
        x0 = long_nonce[x]
        return x0 - (x0 // 0x24) * 0x24
    offset1, offset2, offset3, offset4 = index_calc(1), index_calc(2), index_calc(3), index_calc(4)
    nonce_reorder = (
        long_nonce[offset1:offset1 + 13] + long_nonce[offset2:offset2 + 16] +
        long_nonce[offset3:offset3 + 36] + long_nonce[offset4:offset4 + 36]
    )
    auth_string = (str(timestamp) + SALT + device_token + nonce).encode("ascii")
    result = "".join(chr((auth_string[i] + nonce_reorder[i]) >> 1) for i in range(101))
    lens = [13, 16, 36, 36]
    A, B = result[0:lens[0]], result[lens[0]:lens[0] + lens[1]]
    C = result[lens[0] + lens[1]:lens[0] + lens[1] + lens[2]]
    D = result[lens[0] + lens[1] + lens[2]:]
    string_after_reorder = D + A + C + B
    final = ""
    for i in range(101):
        char_code = ord(string_after_reorder[i])
        if char_code < 0x30:
            final += chr(0x39) if 0x39 < char_code + 19 < 0x41 else chr(char_code + 19)
        elif 0x39 < char_code < 0x41 or 0x5A < char_code < 0x61:
            final += chr(char_code + 19)
        else:
            final += string_after_reorder[i]
    return hashlib.md5(final.encode("utf-8")).hexdigest().upper()

# ================= 统一登录鉴权系统 =================
def get_cookie(username, password, nonce):
    timestamp = int(time.time() * 1000)
    sign = get_sign(nonce, timestamp, device_token)
    headers['sfsecurity'] = f'nonce={nonce}&timestamp={timestamp}&devicetoken={device_token}&sign={sign}'
    data = json.dumps({"password": password, "shuMeiId": "", "username": username})
    try:
        resp = requests.post("https://api.sfacg.com/sessions", headers=headers, data=data, timeout=10)
        if resp.json()["status"]["httpCode"] == 200:
            cookie = requests.utils.dict_from_cookiejar(resp.cookies)
            return f'.SFCommunity={cookie[".SFCommunity"]}; session_APP={cookie["session_APP"]}'
    except Exception:
        pass
    return "error"

def check(check_headers):
    try:
        resp = requests.get('https://api.sfacg.com/user?', headers=check_headers, timeout=5)
        data = resp.json()
        if data["status"]["httpCode"] == 200:
            return False
        return True
    except Exception:
        return True

def init_nonce():
    resp = {"status": {"httpCode": 417}}
    nonce = ""
    while resp['status']['httpCode'] == 417:
        nonce = str(uuid.uuid4()).upper()
        timestamp = int(time.time() * 1000)
        sign = get_sign(nonce, timestamp, device_token)
        headers['sfsecurity'] = f'nonce={nonce}&timestamp={timestamp}&devicetoken={device_token}&sign={sign}'
        try:
            url_init = f"https://api.sfacg.com/Chaps/8436696?expand=content%2Cexpand.content"
            resp = requests.get(url_init, headers=headers, timeout=10).json()
        except:
            pass
    return nonce

def get_catalog(novel):
    chapters = []
    url = f"https://book.sfacg.com/Novel/{novel}/MainIndex/"
    try:
        with requests.get(url, headers=headers_PC, timeout=10) as resp:
            links = re.findall(r'<a href="(.*?)" title=', resp.text)
            for link in links:
                if link.split('/')[1] == "vip": 
                    continue # 字典生成必须过滤VIP，因为PC端无法直接获取VIP明文用于比对
                chapters.append(link)
        return chapters
    except Exception as e:
        print(f"[!] 获取小说 {novel} 目录失败: {e}")
        return []

# ================= 核心：单章节处理与局部字典生成 =================
def process_single_chapter(chapter_link, nonce, max_retries):
    local_headers = headers.copy()
    chapter_id = chapter_link.split('/')[-2]
    
    api_content = ""
    pc_content = ""
    
    # 1. 抓取 API 乱码端 (完全复用 downloader 的请求格式与数据提取逻辑)
    retry_count = 0
    while retry_count < max_retries:
        try:
            timestamp = int(time.time() * 1000)
            sign = get_sign(nonce, timestamp, device_token)
            local_headers['sfsecurity'] = f'nonce={nonce}&timestamp={timestamp}&devicetoken={device_token}&sign={sign}'
            url = f"https://api.sfacg.com/Chaps/{chapter_id}?expand=content%2Cexpand.content"
            
            resp = requests.get(url, headers=local_headers, timeout=10).json()
            
            if resp['status']['httpCode'] == 200:
                tmp = ""
                if 'content' in resp['data']:
                    tmp += resp['data']['content']
                    if 'expand' in resp['data'] and 'content' in resp['data']['expand']:
                        tmp += resp['data']['expand']['content']
                else:
                    tmp += resp['data']['expand']['content']
                
                api_content = tmp
                break
        except Exception:
            time.sleep(1)
        retry_count += 1
            
    # 2. 抓取 PC 明文端 (带重试)
    retry_count = 0
    while retry_count < max_retries:
        try:
            resp_pc = requests.get('https://book.sfacg.com' + chapter_link, headers=headers_PC, timeout=10)
            pc_content_raw = ''.join(re.findall(r'<p>(.*?)</p>', resp_pc.text)[0:-1])
            pc_content = pc_content_raw
            break
        except Exception:
            time.sleep(1)
        retry_count += 1
            
    # 3. 数据清洗与映射提取
    api_clean = ''.join(re.findall(r'[\u4e00-\u9fff]+', api_content))
    pc_clean = ''.join(re.findall(r'[\u4e00-\u9fff]+', pc_content))
    
    local_dict = {}
    if len(api_clean) > 0 and len(api_clean) == len(pc_clean):
        for i in range(len(api_clean)):
            if api_clean[i] not in local_dict:
                local_dict[api_clean[i]] = pc_clean[i]
        print(f" [+] 章节 {chapter_id} 映射成功，提取字符对: {len(local_dict)}")
        return {'status': 'success', 'dict': local_dict, 'id': chapter_id}
    else:
        print(f" [-] 章节 {chapter_id} 跳过: 长度不匹配或内容为空 (API端汉字: {len(api_clean)} 个, PC端汉字: {len(pc_clean)} 个)")
        return {'status': 'mismatch', 'id': chapter_id}

# ================= 主控制流 =================
if __name__ == "__main__":
    cookie_file = "cookie.txt"
    config = {
        "cookie": "",
        "max_retries": 3,
        "max_threads": 5
    }

    # 读取及向前兼容配置格式 (与 downloader 完全相同的逻辑)
    if not os.path.exists(cookie_file):
        with open(cookie_file, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4)
        print("配置文件 cookie.txt 已自动创建。")
    else:
        with open(cookie_file, "r", encoding="utf-8") as f:
            content = f.read().strip()
            try:
                config = json.loads(content)
                print("加载 cookie.txt 配置文件成功。")
            except json.JSONDecodeError:
                config["cookie"] = content
                with open(cookie_file, "w", encoding="utf-8") as fw:
                    json.dump(config, fw, indent=4)
                print("已将旧版 cookie 文件转换为兼容 JSON 格式配置。")

    headers['cookie'] = config.get("cookie", "")
    global_nonce = init_nonce()
    
    # 鉴权过期或未登录时，阻塞拉取凭证
    while check(headers):
        print("\n当前凭证无效或未登录！请登录以获取正确的 API 数据。")
        username = input("请输入手机号: ")
        password = input("请输入密码: ")
        new_cookie = get_cookie(username, password, global_nonce)
        if new_cookie != "error":
            config["cookie"] = new_cookie
            headers['cookie'] = new_cookie
            with open(cookie_file, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=4)
            print("登录成功，Cookie已更新。")
        else:
            print("登录失败，请重试。")

    headers['user-agent'] = f'boluobao/5.2.16(android;35)/OPPO/{device_token.lower()}/OPPO'

    # 检查任务列表
    if not os.path.exists('novelList.txt'):
        with open('novelList.txt', 'w') as f:
            f.write('')
        print("\n[中断] 请在同级目录下的 novelList.txt 中填入需要用于生成字典的小说ID（每行一个）。")
        exit()

    with open('novelList.txt', 'r') as file:
        novels = [line.strip() for line in file if line.strip()]

    if not novels:
        print("\n[中断] novelList.txt 为空，请先添加小说ID。")
        exit()

    global_char_dict = {}
    
    # 支持断点续作，加载已有字典
    if os.path.exists('dict.json'):
        with open('dict.json', 'r', encoding="utf-8") as f:
            global_char_dict = json.load(f)
            print(f"已加载本地历史字典，当前已映射词汇量: {len(global_char_dict)}")

    # 获取多线程参数
    max_threads = config.get("max_threads", 5)
    max_retries = config.get("max_retries", 3)

    for novel in novels:
        print(f"\n--- 开始处理小说 ID: {novel} ---")
        chapters = get_catalog(novel)
        if not chapters:
            continue
            
        print(f"共获取到 {len(chapters)} 个免费章节进行对比分析...")
        
        # 多线程并发执行映射构建
        with ThreadPoolExecutor(max_workers=max_threads) as executor:
            futures = [executor.submit(process_single_chapter, chap, global_nonce, max_retries) for chap in chapters]
            
            for future in as_completed(futures):
                res = future.result()
                if res['status'] == 'success':
                    # 将各线程的局部字典安全合并入全局字典
                    global_char_dict.update(res['dict'])

        print(f"当前全局字典词汇量扩充至: {len(global_char_dict)}")
        with open('dict.json', 'w', encoding="utf-8") as f:
            json.dump(global_char_dict, f, ensure_ascii=False, indent=4)
            
    print("\n[字典构建完毕] 数据已持久化保存到 dict.json，随时可供 downloader 使用。")
