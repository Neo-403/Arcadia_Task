#!/usr/bin/env python3
# [Arcadia-AutoCron-BEGIN]
# 更新时间: 2026-09-12
# cron: 15 13 * * *
# [Arcadia-AutoCron-END]
# www.mnpc.net 签到（zibll 主题）— 纯 HTTP 版（登录 + 极验 geetest4 签到）
# ============================================================================
# 依赖（需先安装）：
#     pip install curl_cffi
#     pip install pycryptodome ddddocr      # 仅"每日签到"的极验滑块需要
# 说明：
#   - 站点对标准库 urllib 直接 RemoteDisconnected 断连，用 curl_cffi 的浏览器级
#     TLS 指纹(impersonate="chrome")突破（三步业务请求均返回 200）。
#   - 滑块验证码(zibll slidercaptcha)的答案 x 是由前端 JS 随机生成、编码进 randstr
#     发给 captcha.php 备案，服务端返回 check 封存；提交时把同一个 x 编码进 ticket
#     回传比对。服务端并不校验图像几何，因此【不需要图像识别 / ddddocr / Playwright】，
#     只要复现 getX 算法即可（见 js_i / js_n / js_a）。
#   - 业务链：check_auth_block -> captcha.php(登录滑块,服务端不校验图像几何) ->
#             user_signin(登录) -> get_current_user(验证登录态) ->
#             zibll_dk_sam_checkin(每日签到, 需极验 geetest4 滑块验证)。
# 环境变量（MNPC_ 前缀）优先于内置常量；多账户用 换行 / @@ / && 分隔。
#     MNPC_ACCOUNT（推荐：一个变量装完，格式 "账号#密码"，多组换行/@@/&& 分隔）
#     MNPC_USER / MNPC_PASS（旧写法，ACCOUNT 未配置时生效）
#     MNPC_PROXY / MNPC_DELAY / MNPC_FORCE_LOGIN
#
# 关于"cookie 缓存"：本脚本【默认启用】cookie 缓存复用。默认（MNPC_FORCE_LOGIN
# 未开启）优先用落盘 cookie 直接恢复登录态，仅在无有效缓存时才回退账号密码登录，
# 这样多数运行可跳过滑块。若设 MNPC_FORCE_LOGIN=1（或脚本顶部
# BUILTIN_FORCE_LOGIN=True）则强制每次账号密码全新登录。cookie 存于脚本同目录的
# .mnpc_cookies.json（按账号区分，明文保存，注意保密）。开关命名与 har2sign
# 系列脚本保持一致，便于多脚本统一管理。
# ============================================================================
import os, re, sys, time, random, string, json, subprocess


def _ensure_curl_cffi():
    """依赖自检：缺失时自动 pip install（MNPC_AUTO_INSTALL=0 可关闭，默认开启）。"""
    try:
        from curl_cffi import requests as _r
        return _r
    except ImportError:
        pass
    if os.environ.get("MNPC_AUTO_INSTALL", "1").strip().lower() \
            not in ("1", "true", "yes", "y", "on"):
        return None
    print("缺少依赖 curl_cffi，正在尝试自动安装（MNPC_AUTO_INSTALL=0 可关闭）...")
    try:
        subprocess.run([sys.executable, "-m", "pip", "install",
                        "--disable-pip-version-check", "curl_cffi"],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except Exception as ex:
        print("自动安装 curl_cffi 失败：%s；请手动执行： pip install curl_cffi" % ex)
        return None
    try:
        from curl_cffi import requests as _r
        return _r
    except ImportError:
        return None


cffi_requests = _ensure_curl_cffi()
if cffi_requests is None:
    sys.exit("缺少依赖 curl_cffi，请先运行： pip install curl_cffi")

SITE_TAG = "MNPC"

def _env(name, default):
    v = os.environ.get("%s_%s" % (SITE_TAG, name))
    return v if v is not None else default

# ===== 内置配置（环境变量优先）=====
BUILTIN_ACCOUNT = ""          # 推荐：账号#密码（# 也可写 :）；多组换行/@@/&&分隔，如 "u1#p1@@u2#p2"
BUILTIN_USER = "xxx"          # 登录账号；多账户用换行/@@/&&分隔（BUILTIN_ACCOUNT 为空时生效）
BUILTIN_PASS = "xxx"          # 登录密码，与账号一一对应（BUILTIN_ACCOUNT 为空时生效）
BUILTIN_PROXY = ""                    # 可选代理，如 http://127.0.0.1:7890
BUILTIN_DELAY = "5-10"                # 账户间隔秒数：固定(3) 或区间随机(5-10)
BUILTIN_FORCE_LOGIN = False           # 强制账号密码登录开关（见下）

ACCOUNT = _env("ACCOUNT", BUILTIN_ACCOUNT)
USER = _env("USER", BUILTIN_USER)
PASS = _env("PASS", BUILTIN_PASS)
PROXY = _env("PROXY", BUILTIN_PROXY)
# 强制账号密码登录开关：默认 False -> 优先复用 cookie 缓存（无有效缓存才账号密码）；
# 设为 1/true/yes/on（环境变量 MNPC_FORCE_LOGIN 或内置常量）则每次强制账号密码全新登录。
# 命名与 har2sign 系列脚本保持一致，便于统一管理。
FORCE_LOGIN = str(_env("FORCE_LOGIN", BUILTIN_FORCE_LOGIN)).strip().lower() \
    in ("1", "true", "yes", "y", "on")

URL = "https://www.mnpc.net/wp-admin/admin-ajax.php"
CAPTCHA_URL = "https://www.mnpc.net/wp-content/themes/zibll/action/captcha.php"
HEADERS = {
    "x-requested-with": "XMLHttpRequest",
    "referer": "https://www.mnpc.net/",
    "origin": "https://www.mnpc.net",
    "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
    "accept": "application/json, text/javascript, */*; q=0.01",
    "accept-language": "zh-CN,zh;q=0.9",
}

# ===== 复现前端 slidercaptcha 的 getX / i / a 算法 =====
# JS: n(t,e)=Math.round(Math.random()*(t-e)+e)  -> 整数 [e,t]
def js_n(lo, hi):
    return random.randint(lo, hi)

# JS: a(t) = t 个随机小写字母
def js_a(t):
    return ''.join(random.choice(string.ascii_lowercase) for _ in range(t))

# JS: i(t) = e + a(e) + t + a(i) + i   （t 为整数；把数字藏进随机串里）
def js_i(t):
    e = js_n(11, 40)
    i = js_n(11, 40)
    return str(e) + js_a(e) + str(t) + js_a(i) + str(i)

# JS onSuccess: captcha.randstr = o + rand_str[o:r] + r  （o∈[1,9], r∈[15,25]）
def build_randstr_post(rand_str):
    o = js_n(1, 9)
    r = js_n(15, 25)
    return str(o) + rand_str[o:r] + str(r)

# 拟人 trail：长度 45，Y 偏移从 0 抖动到 5 左右
def build_trail():
    base = [0,0,0,1,1,1,1,2,2,2,2,2,2,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,4,4,4,4,4]
    return [int(round(v + random.uniform(-0.4, 0.4))) for v in base]

def dec(r):
    return r.content.decode("utf-8-sig", "replace")

def jload(r):
    # 把响应体解析成 dict；失败返回 {}，不抛异常
    try:
        return json.loads(dec(r))
    except Exception:
        return {}

def split_multi(s):
    if not s:
        return []
    return [x.strip() for x in re.split(r"[\n@&]+", s) if x.strip()]

def parse_delay(s):
    s = (s or "5-10").strip()
    if "-" in s:
        a, b = s.split("-", 1)
        return float(a), float(b)
    return float(s), float(s)

def step(t):
    print("  · " + t)


# curl_cffi 底层错误码：92=HTTP/2 stream 被服务端重置、56=连接被 abruptly 关闭、
# 35=TLS 握手失败、7=连不上、28=超时、52=空响应。这些都属于"连都没连上/被中途掐断"，
# 与账号密码无关，通常是站点 WAF/CDN 按 IP 风控（或临时拦截），提示用户换出口 IP 而不是反复重试。
_NET_ERR_CODES = {"7", "28", "35", "52", "56", "92"}


def explain_ex(action, ex):
    """把 curl_cffi 的底层异常翻译成人话，附排查建议。"""
    s = str(ex)
    codes = sorted(set(re.findall(r"curl:\s*\((\d+)\)", s)))
    is_net = bool(set(codes) & _NET_ERR_CODES) or any(
        k in s for k in ("reset by server", "closed abruptly", "RemoteDisconnected",
                         "Connection reset", "TLS"))
    if not is_net:
        return "%s：%s" % (action, s)
    return ("%s：%s\n      提示：请求在传输层就被服务端掐断（%s），与账号密码无关——"
            "多为站点 WAF/CDN 按 IP 风控或临时拦截。\n"
            "      建议：① 浏览器打开 https://www.mnpc.net 验证是否也打不开；"
            "② 稍后再试（临时封禁常自动解除）；③ 设置 MNPC_PROXY 换出口 IP。"
            % (action, s, (",".join("curl %s" % c for c in codes) or "连接被重置")))

# ===== 极验 geetest 4 滑块（mnpc 每日签到用）=====
# 说明：mnpc 的"每日签到"接口 zibll_dk_sam_checkin 要求极验 geetest 4 滑块验证。
#   流程：load(拿 lot_number/图片/PoW 挑战) -> ddddocr 识别缺口 -> 生成加密 w
#   (随机 AES key + 零 IV 加密 JSON，再 RSA 加密该 AES key，用极验公开公钥) ->
#   verify -> 取结果回填签到。
#   注意：极验 4 的 captcha_output/pass_token 由前端 SDK 用 captcha_id 派生 key 内部生成，
#   纯 Python 复刻属"尽力实现"，w 加密算法已按公开逆向落地；因本机无法连接该站，
#   需在你可连通的环境实测，captcha_output/pass_token 生成、PoW 参数、verify 返回字段
#   映射可能需按站点 gct4.js 微调（详见各函数注释）。
GEETEST_CAPTCHA_ID = "7f954cbf9834347763d0a3075685e167"
GEETEST_LOAD = "https://gcaptcha4.geetest.com/load"
GEETEST_VERIFY = "https://gcaptcha4.geetest.com/verify"
# 极验 4 前端硬编码的 RSA 公钥（gct4.js 内，所有站点共用）
_GT_RSA_MOD = ("00C1E3934D1614465B33053E7F48EE4EC87B14B95EF88947713D25EECBFF7E74"
               "C7977D02DC1D9451F79DD5D1C10C29ACB6A9B4D6FB7D0A0279B6719E1772565F"
               "09AF627715919221AEF91899CAE08C0D686D748B20A3603BE2318CA6BC2B5970"
               "6592A9219D0BF05C9F65023A21D2330807252AE0066D59CEEFA5F2748EA80BAB81")
_GT_RSA_EXP = "10001"


def _ensure_geetest_deps():
    """极验滑块需要 pycryptodome(AES/RSA) + ddddocr(缺口识别)。缺失则尝试自动安装。"""
    ok = True
    try:
        from Crypto.Cipher import AES  # noqa
    except ImportError:
        ok = False
    try:
        import ddddocr  # noqa
    except ImportError:
        ok = False
    if ok:
        return True
    if os.environ.get("MNPC_AUTO_INSTALL", "1").strip().lower() not in ("1", "true", "yes", "y", "on"):
        return False
    print("缺少极验滑块依赖（pycryptodome / ddddocr），正在尝试自动安装...")
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
                        "pycryptodome", "ddddocr"],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except Exception as ex:
        print("自动安装失败：%s" % ex)
        return False
    try:
        from Crypto.Cipher import AES  # noqa
        import ddddocr  # noqa
        return True
    except ImportError:
        return False


def _gt_aes_key_hex():
    # 极验前端随机 16 字节 AES key（编码为 32 hex）。服务端用 RSA 私钥解出此 key 再解密 payload，
    # 故只要满足 AES-128 长度(16 字节=32 hex)即可，内容随机。
    # 等价 JS: (65536*(1+Math.random())|0).toString(16).substring(1) 拼接至 32 字符
    s = ""
    while len(s) < 32:
        val = int(65536 * (1 + random.random())) & 0xFFFF
        s += format(val, "05x")[1:]
    return s[:32]


def _gt_build_w(payload_dict, rsa_mod=_GT_RSA_MOD):
    """极验 4 逆向：随机 16B AES key(hex) + IV="0000000000000000"(16字节ASCII)，
    AES-CBC 加密 JSON，再拼接 RSA(PKCS#1 v1.5) 加密的 AES key。公钥由 _get_gt4_pubkey 动态获取。"""
    from Crypto.Cipher import AES, PKCS1_v1_5
    from Crypto.PublicKey import RSA
    from Crypto.Util.Padding import pad
    import binascii
    aes_key = _gt_aes_key_hex()
    key = bytes.fromhex(aes_key)
    iv = b"0000000000000000"
    plain = json.dumps(payload_dict, separators=(",", ":"), ensure_ascii=False)
    cipher = AES.new(key, AES.MODE_CBC, iv)
    aes_part = binascii.hexlify(cipher.encrypt(pad(plain.encode("utf-8"), 16))).decode()
    rsa_key = RSA.construct((int(rsa_mod, 16), int(_GT_RSA_EXP, 16)))
    rsa_part = binascii.hexlify(PKCS1_v1_5.new(rsa_key).encrypt(bytes.fromhex(aes_key))).decode()
    return aes_part + rsa_part


def _get_gt4_pubkey(sess, static_path=""):
    """动态从 gct4.js 提取极验4 RSA 公钥 modulus。极验4 公钥随 SDK 版本变化，
    硬编码极易用错（本站曾误用极验3公钥导致 param decrypt error），故运行时提取。"""
    import re as _re
    urls = []
    if static_path:
        urls.append("https://static.geetest.com" + static_path.rstrip("/") + "/js/gcaptcha4.js")
    urls.append("https://static.geetest.com/v4/static/gct4.js")
    for url in urls:
        try:
            js = dec(sess.get(url, headers={"referer": "https://www.mnpc.net/"}, timeout=15))
        except Exception as ex:
            print("  [调试] 取 gct4.js(%s)失败：%s" % (url, ex))
            continue
        if not js:
            continue
        ms = _re.findall(r"[0-9A-Fa-f]{256,}", js)  # 公钥 modulus: 长 hex(>=1024bit)
        if ms:
            return ms[0]
    return ""


def _gt_build_track(set_left, min_t=800, max_t=1500):
    """生成拖动轨迹 track: [[x,y,t],...]（t 为累加毫秒）。极验4 的 w 必须携带 track 才通过风控。"""
    import random
    total = random.randint(min_t, max_t)
    n = random.randint(28, 48)
    track = []
    t = 0
    step = total / n
    for i in range(n + 1):
        p = i / n
        eased = p * p * (3 - 2 * p)  # smoothstep 缓动（先慢后快再慢）
        x = int(round(eased * set_left))
        y = random.randint(-3, 3)
        t += int(round(step)) + random.randint(-8, 8)
        track.append([x, y, max(t, 0)])
    if track:
        track[-1][0] = set_left            # 末点严格对齐目标位移
        track[-1][1] = random.randint(-2, 2)
    return track, (track[-1][2] if track else total)


def _gt_device_id():
    """极验4 前端生成的设备指纹 device_id（16 位大写十六进制近似）。"""
    return "".join(random.choice("0123456789ABCDEF") for _ in range(16))


def _gt_solve_pow(pow_detail, captcha_id, lot_number):
    """极验 4 PoW：构造 pow_msg 并暴力枚举后缀，使 md5(pow_msg) 前 bits 位为 0。
    pow_detail 来自 load 响应（version/bits/datetime/hashfunc）。返回 (pow_msg, pow_sign)。"""
    import hashlib
    ver = pow_detail.get("version", "1")
    bits = int(pow_detail.get("bits", "0") or 0)
    dt = pow_detail.get("datetime", "")
    hfunc = pow_detail.get("hashfunc", "md5")
    base = "%s|%s|%s|%s|%s|%s||" % (ver, bits, hfunc, dt, captcha_id, lot_number)
    nonce = 0
    target = "0" * bits if bits else ""
    while True:
        cand = base + ("%x" % nonce)
        h = hashlib.md5(cand.encode()).hexdigest()
        if bits == 0 or h[:bits] == target:
            return cand, h
        nonce += 1
        if nonce > 5_000_000:  # 兜底，避免极端情况死循环
            return cand, h


def _gt_get_slider_offset(bg_bytes, slide_bytes):
    """用 ddddocr 模板匹配算缺口 x（setLeft）。识别不准时返回 None。"""
    from io import BytesIO
    from PIL import Image
    def _norm(b):
        # 极验可能返回 webp，ddddocr 底层 PIL 未必支持；统一转成 PNG 字节
        try:
            im = Image.open(BytesIO(b)).convert("RGB")
            out = BytesIO()
            im.save(out, "PNG")
            return out.getvalue()
        except Exception:
            return b
    bg_bytes = _norm(bg_bytes)
    slide_bytes = _norm(slide_bytes)
    import ddddocr
    ocr = ddddocr.DdddOcr(det=False, ocr=False, show_ad=False)
    try:
        res = ocr.slide_match(slide_bytes, bg_bytes, simple_target=True)
    except TypeError:
        res = ocr.slide_match(slide_bytes, bg_bytes)
    if isinstance(res, dict):
        return int(res.get("target_x", res.get("x", 0)))
    if isinstance(res, (list, tuple)) and len(res) >= 1:
        return int(res[0])
    return None


def _abs_url(u, origin="https://www.mnpc.net"):
    """把极验 load 返回的图片地址规范成绝对 URL。
    实测 HAR 证实：load 返回的 bg/slice 是相对路径 'pictures/v4_pic/...'，
    前端 SDK 会拼到极验官方图片 CDN 'https://static.geetest.com/'；
    若直接拼到本站域名会得到 404(nginx)。"""
    if not u:
        return u
    if u.startswith("//"):
        return "https:" + u
    if u.startswith("http://") or u.startswith("https://"):
        return u
    if u.startswith("pictures/"):          # 极验图片相对路径 -> 官方 CDN
        return "https://static.geetest.com/" + u
    if u.startswith("/"):
        return origin.rstrip("/") + u
    return origin.rstrip("/") + "/" + u


def _gt_guess_tokens(lot_number, set_left, passtime):
    """极验 4 的 captcha_output/pass_token 由前端 SDK 用 captcha_id 派生 key 内部加密生成，
    纯 Python 复刻属推测实现：用 captcha_id 作 AES key、零 IV 加密 {lot_number,setLeft,...}
    得 captcha_output；pass_token 用 sha256 组合。需按站点 gct4.js 微调。"""
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad
    import binascii, hashlib
    key = (GEETEST_CAPTCHA_ID + "0" * 32)[:32].encode()
    iv = b"\x00" * 16
    inner = json.dumps({"lot_number": lot_number, "setLeft": set_left, "passtime": passtime},
                       separators=(",", ":"))
    co = binascii.hexlify(AES.new(key, AES.MODE_CBC, iv).encrypt(
        pad(inner.encode(), 16))).decode()
    pt = hashlib.sha256(("%s%s" % (lot_number, set_left)).encode()).hexdigest()
    return {"captcha_output": co, "pass_token": pt}


def geetest_v4_solve(sess, referer):
    """完整跑一遍极验 4：load -> 识别缺口 -> 生成 w -> verify。
    返回 dict（含 lot_number/setLeft/pass_token/gen_time/captcha_output/validate/seccode），
    失败返回 None。"""
    try:
        from Crypto.Cipher import AES  # noqa  # 触发依赖检查
    except ImportError:
        if not _ensure_geetest_deps():
            return None
    import re as _re, time as _t
    cb = "geetest_%d" % int(_t.time() * 1000)
    challenge = "%08x-%04x-%04x-%04x-%012x" % (
        random.randint(0, 0xFFFFFFFF), random.randint(0, 0xFFFF),
        random.randint(0, 0xFFFF), random.randint(0, 0xFFFF), random.randint(0, 0xFFFFFFF))
    load_params = {"callback": cb, "captcha_id": GEETEST_CAPTCHA_ID,
                   "challenge": challenge, "client_type": "web", "lang": "zho"}
    try:
        rl = sess.get(GEETEST_LOAD, params=load_params,
                      headers={"referer": referer, "x-requested-with": "XMLHttpRequest"})
        lb = dec(rl)
    except Exception as ex:
        print("  ! 极验 load 失败：%s" % ex)
        return None
    m = _re.search(r"\((\{.*\})\)", lb, _re.S)
    if not m:
        print("  ! 极验 load 响应无法解析：%s" % lb[:120])
        return None
    try:
        data = json.loads(m.group(1))
    except Exception:
        print("  ! 极验 load JSON 解析失败")
        return None
    d = data.get("data", data)
    lot_number = d.get("lot_number")
    process_token = d.get("process_token")
    payload = d.get("payload", "")
    pow_detail = d.get("pow_detail", {}) or {}
    sp = d.get("static_path") or ""
    pubkey = _get_gt4_pubkey(sess, sp)
    print("  [调试] 极验 load 字段=%s static_path=%r" % (list(d.keys()), sp))
    print("  [调试] 极验公钥提取：%s" % (pubkey[:40] + "(...)" if pubkey else "未提取到(回退硬编码)"))
    mod = pubkey if pubkey else _GT_RSA_MOD
    bg_url = d.get("bg") or d.get("fullbg")          # 背景图（带缺口）
    slide_url = d.get("slice") or d.get("slider")    # 滑块小图
    if not (lot_number and bg_url and slide_url):
        print("  ! 极验 load 未返回必要字段：lot_number/bg/slice")
        return None
    bg_url, slide_url = _abs_url(bg_url, referer), _abs_url(slide_url, referer)
    print("  [调试] 极验图片地址：bg=%s" % bg_url)
    img_hdrs = {
        "referer": referer,
        "accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        "accept-language": "zh-CN,zh;q=0.9",
        "sec-fetch-dest": "image",
        "sec-fetch-mode": "no-cors",
        "sec-fetch-site": "same-origin",
    }
    try:
        rb = sess.get(bg_url, headers=img_hdrs)
        sb = sess.get(slide_url, headers=img_hdrs)
        bg, sl = rb.content, sb.content
    except Exception as ex:
        print("  ! 极验图片下载失败：%s" % ex)
        return None
    ct = rb.headers.get("content-type") or rb.headers.get("Content-Type") or ""
    print("  [调试] bg ct=%r len=%d head=%r" % (ct, len(bg), bg[:16]))
    # 诊断：打印图片像素尺寸并落盘，便于分析 setLeft 是否因前端 CSS 缩放而偏
    try:
        import os as _os
        from io import BytesIO as _BIO
        from PIL import Image as _PILImage
        _bgsz = _PILImage.open(_BIO(bg)).size
        _slsz = _PILImage.open(_BIO(sl)).size
        print("  [调试] 极验图尺寸 bg=%s slice=%s" % (_bgsz, _slsz))
        _dbg_dir = _os.path.dirname(_os.path.abspath(__file__))
        open(_os.path.join(_dbg_dir, "geetest_debug_bg.png"), "wb").write(bg)
        open(_os.path.join(_dbg_dir, "geetest_debug_slice.png"), "wb").write(sl)
    except Exception as _e:
        print("  [调试] 极验图尺寸/落盘失败：%s" % _e)
    if ct.startswith("text/html") or bg[:1] == b"<":
        print("  [调试] 图片返回 HTML，页面内容：\n%s" % bg[:500].decode("utf-8", "replace"))
        return None
    offset = _gt_get_slider_offset(bg, sl)
    if offset is None:
        print("  ! 极验缺口识别失败（ddddocr 未能定位滑块）")
        return None
    set_left = int(offset)
    # 生成拖动轨迹（极验4 的 w 必须携带 track 才可通过风控）；passtime 取轨迹末时间
    track, passtime = _gt_build_track(set_left)
    pow_msg, pow_sign = _gt_solve_pow(pow_detail, GEETEST_CAPTCHA_ID, lot_number)
    dyn_key = lot_number[1:9]
    dyn_val = lot_number[9:15]
    payload_dict = {
        "pow_sign": pow_sign,
        "ep": "123",
        "passtime": passtime,
        "biht": "%d" % random.randint(1000000000, 1999999999),
        "pow_msg": pow_msg,
        "lot_number": lot_number,
        "track": track,                       # 关键：拖动轨迹 [[x,y,t],...]
        "em": {"wd": 1, "sc": 0, "ek": "11", "nt": 0, "ph": 0, "cp": 0, "si": 0},
        "geetest": "captcha",
        "setLeft": set_left,
        dyn_key: dyn_val,
        "userresponse": round(set_left / 0.8876, 2),
        "device_id": _gt_device_id(),
        "lang": "zh",
        "w22T": "72PZ",
    }
    w = _gt_build_w(payload_dict, mod)
    cb2 = "geetest_%d" % int(_t.time() * 1000)
    verify_params = {
        "callback": cb2, "captcha_id": GEETEST_CAPTCHA_ID, "client_type": "web",
        "lot_number": lot_number, "payload": payload, "process_token": process_token,
        "payload_protocol": "1", "pt": "1", "w": w,
    }
    try:
        rv = sess.get(GEETEST_VERIFY, params=verify_params,
                      headers={"referer": referer, "x-requested-with": "XMLHttpRequest"})
        vb = dec(rv)
    except Exception as ex:
        print("  ! 极验 verify 失败：%s" % ex)
        return None
    m2 = _re.search(r"\((\{.*\})\)", vb, _re.S)
    if not m2:
        print("  ! 极验 verify 响应无法解析：%s" % vb[:120])
        return None
    try:
        vdata = json.loads(m2.group(1))
    except Exception:
        vdata = {}
    print("  [调试] 极验 verify status=%s" % vdata.get("status"))
    print("  [调试] 极验 verify data=%s" % json.dumps(vdata.get("data", vdata), ensure_ascii=False)[:700])
    vd = vdata.get("data", vdata)
    # 极验4 verify 成功时，pass_token/captcha_output/gen_time 都在 data.seccode 对象内
    # （并非前端生成）。早期误从 data 顶层取，导致取到空值而掉入猜测实现 -> 签到校验失败。
    _sc = vd.get("seccode")
    seccode = _sc if isinstance(_sc, dict) else {}
    result = {
        "lot_number": lot_number,
        "setLeft": set_left,
        "pass_token": seccode.get("pass_token") or vd.get("pass_token") or "",
        "gen_time": seccode.get("gen_time") or vd.get("gen_time") or str(int(_t.time())),
        "captcha_output": seccode.get("captcha_output") or vd.get("captcha_output") or "",
        "validate": seccode.get("validate") or vd.get("validate") or "",
        "seccode": seccode.get("seccode") or vd.get("seccode") or "",
    }
    # 兜底：极验 verify 通常不直接返回 captcha_output/pass_token（由前端 SDK 内部生成），
    # 若取不到则用推测实现生成（见 _gt_guess_tokens 注释）。
    if not result["captcha_output"] or not result["pass_token"]:
        g = _gt_guess_tokens(lot_number, set_left, passtime)
        result["captcha_output"] = result["captcha_output"] or g["captcha_output"]
        result["pass_token"] = result["pass_token"] or g["pass_token"]
    print("  [调试] 极验结果 setLeft=%s passtime=%s pass_from_seccode=%s co_from_seccode=%s"
          % (set_left, passtime, bool(seccode.get("pass_token")), bool(seccode.get("captcha_output"))))
    print("  [调试] 极验结果 validate=%r seccode=%r" % (result["validate"], result["seccode"]))
    if vd.get("result") != "success":
        print("  ! 极验 verify 业务判定失败：result=%s fail_count=%s（setLeft=%s 可能被识别偏）"
              % (vd.get("result"), vd.get("fail_count"), set_left))
        return None
    return result


def _get_checkin_nonce(sess, referer=None):
    """签到 nonce 嵌在首页 HTML 内联脚本：var zibllDkSamScan = { "nonce":"...", ... }
    （HAR 实证）。注意：主页带有人机守卫，必须用 http_version=2 + 完整 nav 头
    才能拿到真页（HTTP/1.1 直连会返回守卫拦截页、不含 nonce），故与 fetch_points
    保持一致的请求方式。"""
    import re as _re, os as _os
    nav_headers = {
        "referer": referer or "https://www.mnpc.net/",
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "accept-language": "zh-CN,zh;q=0.9",
        "upgrade-insecure-requests": "1",
    }
    # 预热：本站主页首次访问常被守卫拦截并重定向/种 cookie，先空打一次再取真页
    try:
        sess.get("https://www.mnpc.net/", headers=nav_headers, http_version=2)
    except Exception:
        pass
    html = ""
    last_ex = ""
    for _att in range(3):                          # 连接偶发被 reset，重试 http1 优先
        for hv in (1, 2):
            try:
                kw = {"headers": nav_headers}
                if hv is not None:
                    kw["http_version"] = hv
                r = sess.get("https://www.mnpc.net/", **kw)
                html = dec(r)
                if html:
                    break
            except Exception as ex:
                last_ex = ex
        if html:
            break
        import time as _time
        _time.sleep(1)
    if not html:
        print("  [调试] nonce 主页全部重试失败：%s" % last_ex)
    # 调试：把主页存盘，便于排查守卫页 / 字段位置
    try:
        dbg = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "nonce_debug.html")
        with open(dbg, "w", encoding="utf-8") as f:
            f.write(html)
    except Exception:
        pass
    print("  [调试] nonce 主页：len=%d 含zibllDkSam=%s 含nonce=%s 含userPoints=%s"
          % (len(html), "zibllDkSam" in html, "nonce" in html, "userPoints" in html))
    pats = (
        r'zibllDkSam\w*\s*=\s*\{[^}]*"nonce"\s*:\s*"([a-z0-9]+)"',  # 对象字面量
        r"zibllDkSam\w*\.nonce\s*=\s*['\"]([a-z0-9]+)['\"]",         # 点赋值
        r"['\"]nonce['\"]?\s*[:=]\s*['\"]([a-z0-9]+)['\"]",          # JSON 形式
        r"name=['\"]nonce['\"]\s+value=['\"]([a-z0-9]+)['\"]",       # 隐藏域
    )
    for pat in pats:
        m = _re.search(pat, html, _re.I)
        if m:
            return m.group(1)
    return ""


def do_checkin(sess):
    """每日签到：极验 4 滑块 -> zibll_dk_sam_checkin。返回 (ok, msg)。"""
    referer = "https://www.mnpc.net/"
    if not _ensure_geetest_deps():
        return False, "缺少极验滑块依赖（pycryptodome/ddddocr），请先 pip install pycryptodome ddddocr"
    step("开始每日签到（极验滑块验证）")
    gt = None
    for _att in range(1, 5):                      # 极验识别可能偏，自动重试新挑战
        gt = geetest_v4_solve(sess, referer)
        if gt is not None:
            break
        print("  [重试] 极验验证未通过，重新发起挑战（%d/4）" % _att)
    if gt is None:
        return False, "极验滑块验证未通过（多次重试仍失败，可能 setLeft 识别持续偏差）"
    nonce = _get_checkin_nonce(sess, referer)
    if not nonce:
        return False, "未能获取签到 nonce（可能需从签到页提取，见脚本注释）"
    # click_token 来源不明：HAR 示例 MHw4Mjd8MjI5（base64）。此处用 setLeft 编码 best-effort。
    import base64
    click_token = base64.b64encode(("8|%d|240" % gt.get("setLeft", 0)).encode()).decode()
    fields = [
        ("action", "zibll_dk_sam_checkin"),
        ("nonce", nonce),
        ("click_token", click_token),
        ("captcha[captcha_id]", GEETEST_CAPTCHA_ID),
        ("captcha[lot_number]", gt["lot_number"]),
        ("captcha[pass_token]", gt["pass_token"]),
        ("captcha[gen_time]", gt["gen_time"]),
        ("captcha[captcha_output]", gt["captcha_output"]),
        ("captcha[validate]", gt["validate"]),
        ("captcha[seccode]", gt["seccode"]),
    ]
    boundary = "----WebKitFormBoundary%08x" % random.randint(0, 0xFFFFFFFF)
    body = ""
    for k, v in fields:
        body += '--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n' % (boundary, k, v)
    body += "--%s--\r\n" % boundary
    ck_headers = dict(HEADERS)
    ck_headers["content-type"] = "multipart/form-data; boundary=%s" % boundary
    print("  [调试] 签到提交：nonce=%s click_token=%s lot=%s pass_token=%s co=%s validate=%s seccode=%s"
          % (nonce, click_token, gt.get("lot_number"), gt.get("pass_token"),
             gt.get("captcha_output"), gt.get("validate"), gt.get("seccode")))
    try:
        r = sess.post(URL, data=body.encode("utf-8"), headers=ck_headers)
        c = jload(r)
    except Exception as ex:
        return False, explain_ex("签到接口请求异常", ex)
    if c.get("error") == 0 or c.get("success"):
        return True, (c.get("msg") or "签到成功")
    return False, "签到失败：%s" % (c.get("msg") or dec(r)[:120])


def fetch_points(sess):
    """登录后抓取主页，从内联脚本 var ztsamPE = {... userPoints: N ...} 提取当前积分。
    说明：本站主页带有人机验证守卫，脚本直连有时会返回 403 拦截页或连接被重置，
    此时无法读取积分——属正常降级，不影响签到结果。"""
    nav_headers = {
        "referer": "https://www.mnpc.net/",
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "accept-language": "zh-CN,zh;q=0.9",
        "upgrade-insecure-requests": "1",
    }
    try:
        r = sess.get("https://www.mnpc.net/", headers=nav_headers, http_version=2)
        txt = dec(r)
    except Exception:
        return None
    m = re.search(r"userPoints\s*:\s*(\d+)", txt)
    return int(m.group(1)) if m else None

def run_one(sess, user, pwd):
    # 0) 登录方式选择：默认优先复用 cookie 缓存；仅 FORCE_LOGIN 时强制账号密码
    logged_in = False
    if FORCE_LOGIN:
        step("登录方式：强制账号密码登录（FORCE_LOGIN 开启）")
    else:
        if _restore_session(sess, user):
            logged_in = True
            who = ""
            try:
                r4 = sess.post(URL, data="action=get_current_user", headers=HEADERS)
                c4 = jload(r4)
                uid = c4.get("id")
                uname = (c4.get("user_data") or {}).get("user_login", "")
                if c4.get("is_logged_in"):
                    who = ("ID %s" % uid) + (("，账号 %s" % uname) if uname else "")
            except Exception:
                who = ""
            step("登录方式：复用 cookie 缓存（已确认登录态%s）" % (("：%s" % who) if who else ""))
        else:
            step("cookie 缓存无效/不存在，回退账号密码登录")

    if not logged_in:
        # 1) 配套安全验证
        did = "did_%s_%d" % (''.join(random.choice(string.ascii_lowercase) for _ in range(8)), int(random.random()*1e13))
        fp = ''.join(random.choice('0123456789abcdef') for _ in range(64))
        body1 = "action=zibll_zt_sam_check_auth_block&auth_type=signin&local_did=%s&device_fp=%s" % (did, fp)
        try:
            r1 = sess.post(URL, data=body1, headers=HEADERS)
            c1 = jload(r1)
        except Exception as ex:
            return False, explain_ex("安全校验接口请求异常", ex)
        if not c1.get("success"):
            return False, "安全校验未通过（%s）" % dec(r1)[:80]
        step("安全校验：通过")

        # 2) 取验证码 token / rand_str / check；正确答案 x 客户端随机生成并编码进 randstr
        #    width=280, sliderL=42 -> x∈[42+10, 280-(42+10)] = [52,228]
        x = js_n(52, 228)
        randstr_for_captcha = js_i(x)
        r2 = sess.get(CAPTCHA_URL, params={"type": "slider", "randstr": randstr_for_captcha},
                      headers={"referer": "https://www.mnpc.net/", "x-requested-with": "XMLHttpRequest"})
        cap = jload(r2)
        token, rand_str, check = cap.get("token"), cap.get("rand_str"), cap.get("check")
        if not (token and rand_str and check):
            return False, "验证码接口未返回完整凭证（HTTP %s）" % r2.status_code
        step("滑块验证：已生成凭证（答案位置 x=%d）" % x)

        # 3) 登录 + 签到（ticket 编码同一个 x）
        ticket = js_i(x)
        randstr_post = build_randstr_post(rand_str)
        trail = json.dumps(build_trail(), separators=(',', ':'))
        body3 = (
            "username=%s&password=%s&captcha_mode=slider&remember=forever&action=user_signin"
            "&captcha%%5Bticket%%5D=%s&captcha%%5Brandstr%%5D=%s"
            "&captcha%%5Bspliced%%5D=true&captcha%%5Bcheck%%5D=%s&captcha%%5Btrail%%5D=%s"
        ) % (user, pwd, ticket, randstr_post, check, trail)
        try:
            r3 = sess.post(URL, data=body3, headers=HEADERS)
            c3 = jload(r3)
        except Exception as ex:
            return False, explain_ex("登录接口请求异常", ex)
        msg3 = c3.get("msg", "") or dec(r3)[:80]
        if c3.get("error") == 0 or (c3.get("error") != 1 and ("签到" in msg3 or "登录" in msg3)):
            step("登录提交：%s" % msg3)
        else:
            return False, "登录失败：%s" % msg3

        # 4) 验证登录态
        try:
            r4 = sess.post(URL, data="action=get_current_user", headers=HEADERS)
            c4 = jload(r4)
        except Exception as ex:
            return False, explain_ex("登录态校验请求异常", ex)
        uid = c4.get("id")
        uname = (c4.get("user_data") or {}).get("user_login", "")
        if c4.get("is_logged_in"):
            who = ("ID %s" % uid) + (("，账号 %s" % uname) if uname else "")
            step("登录态确认：%s，已登录" % who)
        else:
            return False, "登录态未确认（响应未返回已登录）"

        # 4.2) 保存 cookie 缓存（非强制登录时），下次可免密复用
        if not FORCE_LOGIN:
            _persist_session(sess, user)

    # 4.5) 每日签到（极验 geetest 4 滑块）
    ck_ok, ck_msg = do_checkin(sess)
    if ck_ok:
        step("每日签到：%s" % ck_msg)
    else:
        print("  ! 每日签到未成功：%s" % ck_msg)

    # 5) 抓取主页当前积分（var ztsamPE = {... userPoints: N ...}）
    pts = fetch_points(sess)
    if pts is not None:
        step("当前积分：%d" % pts)
    else:
        step("当前积分：未能从主页解析（不影响签到结果）")

    msg = "成功（登录态已建立，签到随登录一并提交）"
    if pts is not None:
        msg += "；当前积分：%d" % pts
    else:
        msg += "；积分到账请以站内为准"
    return True, msg

def _split_pairs(v):
    """把“账号+密码”配置拆成 [(账号, 密码), ...]（借鉴青龙“单变量装账号密码”的写法）。

    格式（推荐只用一个变量 MNPC_ACCOUNT / BUILTIN_ACCOUNT）：
        单账号： "user#pass"                    # # 也可写成 : ，取第一个出现的分隔符
        多账号： "user1#pass1@@user2#pass2"      # 或换行 / && 分隔，与旧多账户写法一致
    """
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        items = [str(x).strip() for x in v if str(x).strip()]
    else:
        s = str(v).strip()
        if not s:
            return []
        s = s.replace("\r\n", "\n").replace("\r", "\n").replace("@@", "\n").replace("&&", "\n")
        items = [p.strip() for p in s.split("\n") if p.strip()]
    pairs = []
    for it in items:
        # 取最先出现的分隔符（# 或 :）切一次；另一个符号可以出现在密码里
        idx = [i for i in (it.find("#"), it.find(":")) if i > 0]
        if idx:
            i = min(idx)
            u, p = it[:i], it[i + 1:]
        else:
            u, p = it, ""
        u = u.strip()
        if not u:
            continue
        pairs.append((u, p.strip()))
    return pairs


def _is_placeholder(v):
    """配置值是否明显未填写（空 / 占位符 / 单字符重复），用于提前报错而不是空跑一遍。"""
    s = str(v if v is not None else "").strip()
    if not s:
        return True
    if s.lower() in ("xxx", "xxxx", "xxxxx", "yyyy", "aaa", "abc", "test", "test1",
                     "your_account", "your_password", "your_cookie",
                     "account", "password", "cookie", "changeme", "none", "null"):
        return True
    if len(s) <= 6 and len(set(s.lower())) == 1:
        return True
    return False


# ===== cookie 缓存（默认启用，FORCE_LOGIN 时跳过）=====
# 默认优先复用 cookie 登录态：每次成功账号密码登录后把 cookie 落盘到
# .mnpc_cookies.json（按账号区分），下次运行先尝试用缓存登录，省去滑块。
# 设 MNPC_FORCE_LOGIN=1 或脚本顶部 BUILTIN_FORCE_LOGIN=True 时强制账号密码。
COOKIE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".mnpc_cookies.json")


def _load_cookie_store():
    try:
        with open(COOKIE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cookie_store(store):
    try:
        with open(COOKIE_FILE, "w", encoding="utf-8") as f:
            json.dump(store, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def _flatten_cookies(c):
    """curl_cffi 的 get_dict() 可能返回嵌套 {domain:{path:{name:value}}}，压平成 {name:value}。"""
    out = {}
    if isinstance(c, dict):
        for k, v in c.items():
            if isinstance(v, dict):
                out.update(_flatten_cookies(v))
            else:
                out[k] = v
    return out


def _clear_cookies(sess):
    try:
        sess.cookies.clear()
    except Exception:
        pass


def _restore_session(sess, user):
    """尝试用 cookie 文件恢复该账户登录态并校验有效性。成功返回 True。"""
    store = _load_cookie_store()
    cookies = store.get(user)
    if not cookies:
        return False
    _clear_cookies(sess)
    flat = _flatten_cookies(cookies) if isinstance(cookies, dict) else {}
    for name, value in flat.items():
        try:
            sess.cookies.set(name, value, domain=".mnpc.net", path="/")
        except Exception:
            try:
                sess.cookies.set(name, value)
            except Exception:
                pass
    try:
        r = sess.post(URL, data="action=get_current_user", headers=HEADERS)
        c = jload(r)
    except Exception:
        return False
    if c.get("is_logged_in"):
        return True
    _clear_cookies(sess)
    return False


def _persist_session(sess, user):
    """把当前会话 cookie 存入文件（覆盖该账户）。"""
    try:
        cookies = _flatten_cookies(sess.cookies.get_dict())
    except Exception:
        cookies = {}
    if not cookies:
        return
    store = _load_cookie_store()
    store[user] = cookies
    _save_cookie_store(store)


def main():
    # 推荐用单变量 MNPC_ACCOUNT（"账号#密码"，多组换行/@@/&& 分隔）；
    # 未配置时回退旧写法 MNPC_USER / MNPC_PASS（按序配对，密码只填一个则多账号共用）
    pairs = _split_pairs(ACCOUNT)
    if pairs:
        users = [u for u, _ in pairs]
        passes = [p for _, p in pairs]
    else:
        users = split_multi(USER)
        passes = split_multi(PASS)
    if not users:
        print("未配置账号，退出。请设置环境变量 MNPC_ACCOUNT（格式：账号#密码，多组换行/@@/&& 分隔）"
              " 或修改脚本顶部 BUILTIN_ACCOUNT（旧写法 MNPC_USER/MNPC_PASS 仍兼容）。")
        return
    # 占位值检测：账号仍是 xxx/空 时直接报错，避免"未登录却跑一遍并被判成功"
    _ph = [u for u in users if _is_placeholder(u)]
    if _ph:
        print("以下账户未填写有效账号（空值或占位值）：%s" % "、".join(_ph))
        print("请配置环境变量 MNPC_ACCOUNT（格式：账号#密码；多账号用换行/@@/&& 分隔）"
              "\n  单账号例： MNPC_ACCOUNT='zhangsan#123456'"
              "\n  多账号例： MNPC_ACCOUNT='zhangsan#123456@@lisi#654321'"
              "\n  旧写法（仍兼容）： MNPC_USER / MNPC_PASS（按序配对）"
              "\n  或改脚本顶部内置常量： BUILTIN_ACCOUNT（推荐）/ BUILTIN_USER + BUILTIN_PASS")
        return
    if len(passes) == 1:
        passes = passes * len(users)

    if os.environ.get("MNPC_ACCOUNT"):
        user_src = "MNPC_ACCOUNT 环境变量"
    elif os.environ.get("MNPC_USER"):
        user_src = "MNPC_USER 环境变量"
    else:
        user_src = "脚本内置配置"
    proxy_src = ("已启用（%s）" % PROXY) if PROXY else "未启用"
    login_mode = "强制账号密码登录（MNPC_FORCE_LOGIN 已开启）" if FORCE_LOGIN else "cookie 缓存优先（无有效缓存时回退账号密码）"

    print("=" * 52)
    print("  www.mnpc.net 自动签到")
    print("=" * 52)
    print("配置：账户 %d 个（%s） | 代理：%s | 间隔：%s 秒" % (len(users), user_src, proxy_src, BUILTIN_DELAY))
    print("登录方式：%s" % login_mode)

    lo, hi = parse_delay(BUILTIN_DELAY)
    sess = cffi_requests.Session(impersonate="chrome")
    if PROXY:
        sess.proxies.update({"http": PROXY, "https": PROXY})

    ok_n = 0
    for i, (u, p) in enumerate(zip(users, passes), 1):
        print("\n-------- 账户 %d/%d：%s --------" % (i, len(users), u))
        try:
            ok, final = run_one(sess, u, p)
        except Exception as ex:
            ok, final = False, explain_ex("执行异常(%s)" % type(ex).__name__, ex)
        if ok:
            ok_n += 1
            print("  → 结果：成功 —— %s" % final)
        else:
            print("  → 结果：失败 —— %s" % final)
        if i < len(users):
            d = random.uniform(lo, hi)
            print("  （等待 %.0f 秒后处理下一个账户）" % d)
            time.sleep(d)

    print("\n" + "=" * 52)
    print("执行完毕：共 %d 个账户，成功 %d，失败 %d" % (len(users), ok_n, len(users) - ok_n))
    print("=" * 52)

if __name__ == "__main__":
    main()
