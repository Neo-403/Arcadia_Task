#!/usr/bin/env python3
# [Arcadia-AutoCron-BEGIN]
# 更新时间: 2026-09-12
# cron: 35 8 * * *
# [Arcadia-AutoCron-END]
# 七玩网（www.7chaowan.com）自动签到 —— 手写特例脚本（按标准 §10「手写特例」维护）
#
# 站点特征：zibll 主题 + Cloudflare 人机校验（"Just a moment..." 挑战）+ zibll 滑块验证码。
# 因登录含滑块、且受 CF 挑战，无法用 har2sign 通用模板，故手写并按通用规范对齐。
#
# 过 Cloudflare（标准 §10 技术清单）：
#   默认用 cloudscraper 自动求解 JS 挑战（纯 python，无需浏览器）；
#   不可用则回退 curl_cffi 仅做 TLS 拟真（仍可能被挑战，需手动带 cf_clearance）。
# 滑块：zibll slidercaptcha 纯算法复现（无需图像识别），字段名按 zibll 约定（实跑校准）。
#
# 运行：python ZWZW/七玩网_账号.py
# 环境变量（SITE_TAG=Q7CHAOWAN 前缀）覆盖同名 BUILTIN_*：
#   Q7CHAOWAN_ACCOUNT    账号#密码（推荐，多组换行/@@/&& 分隔，或写列表）
#   Q7CHAOWAN_USER / Q7CHAOWAN_PASS   旧写法，按序配对
#   Q7CHAOWAN_COOKIE     可选兜底：cf_clearance=...; wordpress_logged_in_...=...（CF 挑战失败时用）
#   Q7CHAOWAN_PROXY / Q7CHAOWAN_DELAY / Q7CHAOWAN_FORCE_LOGIN / Q7CHAOWAN_TLS_IMPERSONATE / Q7CHAOWAN_AUTO_INSTALL
import os, re, sys, ssl, json, gzip, zlib, html, time, random, hashlib, subprocess, urllib.parse, urllib.request, urllib.error
from http.cookiejar import CookieJar

SITE_TAG = "Q7CHAOWAN"

def _env(name, default):
    v = os.environ.get("%s_%s" % (SITE_TAG, name))
    return v if v is not None else default

# ===== 内置配置（环境变量优先）=====
BUILTIN_ACCOUNT = ""  # 推荐：账号#密码；多组换行/@@/&&分隔，或写列表
BUILTIN_USER = ""    # 旧写法
BUILTIN_PASS = ""
BUILTIN_COOKIE = ""  # 可选：CF 挑战失败兜底，填 cf_clearance=...; wordpress_logged_in_...=...
BUILTIN_PROXY = ""   # 可选代理，如 http://127.0.0.1:7890
BUILTIN_DELAY = "5-10"   # 账户间隔秒：固定(3) 或区间(5-10)；0=不等待
BUILTIN_FORCE_LOGIN = False  # True=强制每次账号密码登录、不读写缓存
BUILTIN_TLS_IMPERSONATE = ""  # 留空=默认 cloudscraper 过 CF；设 chrome/chrome120 启用 curl_cffi 仅 TLS 拟真
BUILTIN_AUTO_INSTALL = True   # 依赖缺失自动 pip install；设 0 关闭
# =================================

BASE = "https://www.7chaowan.com"
AJAX = BASE + "/wp-admin/admin-ajax.php"

# 滑块尺寸（zibll 默认；若本站滑块宽/拼图尺寸不同，按前端 slidercaptcha 配置调整）
SLIDER_W = 280
SLIDER_L = 42

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/119.0.6045.160 Safari/537.36")


# ---------- zibll 滑块验证码复现（标准 §10；字段名按 zibll 约定，实跑校准）----------
def js_n(lo, hi):
    return random.randint(lo, hi)

def js_a(k):
    return "".join(random.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(k))

def js_i(t):
    e = js_n(11, 40)
    i = js_n(11, 40)
    return "%d%s%d%s%d" % (e, js_a(e), t, js_a(i), i)

def slider_trail():
    n = 45
    arr = []
    cur = 0.0
    for _ in range(n):
        cur += random.uniform(0, 5.0 / n * 3)
        arr.append(round(min(cur, 5.0), 2))
    arr[0] = 0
    arr[-1] = 5
    return arr

def build_captcha():
    """返回 (randstr, submit_dict)；ticket 与 randstr 用同一 x、不同随机壳。"""
    x = js_n(SLIDER_L + 10, SLIDER_W - (SLIDER_L + 10))
    randstr = js_i(x)
    o = js_n(1, 9)
    r = js_n(15, 25)
    ticket = js_i(x)
    return randstr, {
        "captcha[ticket]": ticket,
        "captcha[randstr]": "%d%s%d" % (o, "{RANDSTR}"[o:r], r),  # {RANDSTR} 占位，下面替换
        "captcha[spliced]": "true",
        "captcha[check]": "{CHECK}",     # 由 signin_captcha 响应填入
        "captcha[trail]": json.dumps(slider_trail(), separators=(",", ":")),
        "captcha_mode": "slider",
    }


# ---------- 依赖与网络层 ----------
def _ensure_dep(pkg, import_name=None):
    mod = import_name or pkg
    try:
        return __import__(mod)
    except ImportError:
        pass
    if str(_env("AUTO_INSTALL", BUILTIN_AUTO_INSTALL)).strip().lower() \
            not in ("1", "true", "yes", "y", "on"):
        return None
    print("缺少依赖 %s，正在尝试自动安装..." % pkg)
    try:
        subprocess.run([sys.executable, "-m", "pip", "install",
                       "--disable-pip-version-check", "-q", pkg],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180)
    except Exception as ex:
        print("自动安装 %s 失败：%s" % (pkg, ex))
        return None
    try:
        return __import__(mod)
    except ImportError:
        print("安装 %s 后仍无法导入，请手动：pip install %s" % (pkg, pkg))
        return None


def _mk_session(tls, proxy):
    """返回过 CF 的会话对象；None=走 urllib。
    默认 cloudscraper 自动解 CF 挑战；不可用则按 tls 回退 curl_cffi/urllib。"""
    cs = _ensure_dep("cloudscraper")
    if cs is not None:
        try:
            sc = cs.create_scraper(browser={"custom": UA})
            if proxy:
                sc.proxies.update({"http": proxy, "https": proxy})
            return sc
        except Exception as ex:
            print("cloudscraper 创建失败：%s；回退方案" % ex)
    if tls:
        cr = _ensure_dep("curl_cffi", "curl_cffi.requests")
        if cr is not None:
            s = cr.Session(impersonate=tls)
            if proxy:
                s.proxies.update({"http": proxy, "https": proxy})
            return s
    return None


def _post(session, url, data, headers, cookie=""):
    """统一 POST，返回 (status_int, text)。session=None 走 urllib。"""
    h = dict(headers)
    if cookie:
        h["Cookie"] = cookie
    if session is None:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        data_b = urllib.parse.urlencode(data).encode() if isinstance(data, dict) else (data.encode() if isinstance(data, str) else data)
        req = urllib.request.Request(url, data=data_b, headers=h, method="POST")
        try:
            r = urllib.request.urlopen(req, context=ctx, timeout=60)
            return r.status, r.read().decode("utf-8", "ignore")
        except urllib.error.HTTPError as ex:
            return ex.code, ex.read().decode("utf-8", "ignore")
        except Exception as ex:
            return None, str(ex)
    try:
        resp = session.post(url, data=data, headers=h, timeout=60)
        return resp.status_code, resp.content.decode("utf-8", "ignore")
    except Exception as ex:
        return None, str(ex)


def _warmup(session):
    """先访问首页触发 Cloudflare 挑战解析，拿到 cf_clearance（失败也无妨）。"""
    if session is None:
        return
    try:
        session.get(BASE + "/",
                     headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"},
                     timeout=60)
    except Exception:
        pass


def _unescape_unicode(s):
    """服务端有时把 msg 做了 unicode 转义（\\uXXXX 字面串），这里还原成真实字符；无转义则原样返回。"""
    if not s or "\\u" not in s:
        return s
    try:
        return s.encode("utf-8").decode("unicode_escape")
    except Exception:
        return s


def _get_json_obj(text):
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return None
    return None


# ---------- 业务：登录 + 签到 ----------
def do_login(session, user, pwd, cf_cookie):
    """返回 (ok, msg, wp_cookie_str)。"""
    randstr, cap = build_captcha()
    st, b = _post(session, AJAX,
                  {"action": "signin_captcha", "randstr": randstr},
                  {"User-Agent": UA, "Referer": BASE + "/", "Origin": BASE,
                   "X-Requested-With": "XMLHttpRequest",
                   "Content-Type": "application/x-www-form-urlencoded",
                   "Accept": "application/json, text/plain, */*"},
                  cookie=cf_cookie)
    if _is_cf_challenge(st, b):
        return False, _cf_hint(st), ""
    jobj = _get_json_obj(b)
    token = check = rand_str = None
    if jobj:
        def pick(*keys):
            for k in keys:
                if isinstance(jobj, dict) and jobj.get(k):
                    return jobj.get(k)
                if isinstance(jobj.get("data"), dict) and jobj["data"].get(k):
                    return jobj["data"].get(k)
            return None
        token = pick("token")
        check = pick("check")
        rand_str = pick("rand_str", "randstr")
    if not (token and check):
        return False, "获取滑块凭证失败(响应无 token/check)：HTTP %s | %s" % (st, (b or "")[:300]), ""
    cap["captcha[randstr]"] = cap["captcha[randstr]"].replace("{RANDSTR}", rand_str or randstr)
    cap["captcha[check]"] = check
    body = dict(cap)
    body.update({"action": "user_signin", "username": user, "password": pwd, "remember": "forever"})
    st2, b2 = _post(session, AJAX, body,
                    {"User-Agent": UA, "Referer": BASE + "/user-sign?tab=signin", "Origin": BASE,
                     "X-Requested-With": "XMLHttpRequest",
                     "Content-Type": "application/x-www-form-urlencoded",
                     "Accept": "application/json, text/plain, */*"},
                    cookie=cf_cookie)
    if _is_cf_challenge(st2, b2):
        return False, _cf_hint(st2), ""
    jobj2 = _get_json_obj(b2)
    msg = ""
    if jobj2:
        msg = _unescape_unicode(jobj2.get("msg") or (jobj2.get("data") or {}).get("msg") or "")
        ok = bool(jobj2.get("success")) or str(jobj2.get("status", "")) in ("1", "success", "true")
        if not ok and "登录成功" in b2:
            ok = True
    else:
        ok = ("登录成功" in b2) or ("success" in b2.lower() and "error" not in b2.lower())
    if not ok:
        return False, "登录失败：HTTP %s | %s" % (st2, (b2 or "")[:400]), ""
    wp_ck = ""
    if session is not None:
        wp_ck = "; ".join("%s=%s" % (k, v) for k, v in session.cookies.get_dict().items()
                          if "wordpress" in k.lower() or "wp-" in k.lower())
    return True, (msg or "登录成功"), wp_ck


def do_checkin(sess, cf_cookie, wp_cookie, tries=0):
    """返回 (ok, msg)。本站 zibll 签到 action 实测为 user_checkin；
    响应含 签到成功 / 今日已签到 / 已签到 视为已完成；error:true 视为失败（含限流，自动重试一次）。"""
    cookie = "; ".join([c for c in (cf_cookie, wp_cookie) if c])
    st, b = _post(sess, AJAX, {"action": "user_checkin"},
                  {"User-Agent": UA, "Referer": BASE + "/user", "Origin": BASE,
                   "X-Requested-With": "XMLHttpRequest",
                   "Content-Type": "application/x-www-form-urlencoded",
                   "Accept": "application/json, text/plain, */*"},
                  cookie=cookie)
    if _is_cf_challenge(st, b):
        return False, _cf_hint(st)
    jobj = _get_json_obj(b)
    if jobj:
        raw_msg = jobj.get("msg") or (jobj.get("data") or {}).get("msg") or ""
        msg = _unescape_unicode(raw_msg)
        is_error = bool(jobj.get("error"))
        done = ("签到" in msg or "成功" in msg or "已签" in msg or "今日" in msg) \
               and ("失败" not in msg) and ("error" not in msg.lower())
        ok = bool(jobj.get("success")) or str(jobj.get("status", "")) in ("1", "success", "true") or done
        if (not ok) and is_error and "频繁" in msg and tries < 1:
            print("  [限流，10 秒后重试]")
            time.sleep(10)
            return do_checkin(sess, cf_cookie, wp_cookie, tries + 1)
        return ok, msg
    b2 = _unescape_unicode(b)
    done = ("签到" in b2 or "成功" in b2 or "已签" in b2 or "今日" in b2) and "error" not in b2.lower()
    return done, b2[:300]


def _is_cf_challenge(st, b):
    if st in (403, 503) and b:
        return ("Just a moment" in b) or ("cf-mitigated" in b) or ("challenge-platform" in b) or ("cf-chl" in b)
    return False

def _cf_hint(st):
    return ("Cloudflare 挑战未通过（HTTP %s）。请先在浏览器通过人机验证，再用 %s_COOKIE 填入 "
            "cf_clearance=...; wordpress_logged_in_...=... 后重试；或确认运行环境 IP 被 Cloudflare 信任。"
            % (st, SITE_TAG))


# ---------- 缓存（best-effort，同标准 §4.6）----------
def _cache_path():
    p = _env("COOKIE_CACHE", "")
    base = "七玩网_账号.cookie_cache.json"
    if p and (p.endswith(("/", "\\")) or os.path.isdir(p)):
        return os.path.join(p, base)
    if p:
        return p
    return os.path.splitext(os.path.abspath(__file__))[0] + ".cookie_cache.json"

def _load_cache():
    try:
        with open(_cache_path(), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_cache(d):
    try:
        with open(_cache_path(), "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
    except Exception:
        pass


# ---------- 解析工具 ----------
def _is_placeholder(v):
    """配置值是否明显未填写（空/占位词/单字符重复），提前报错而非空跑。"""
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

def _split_multi(v):
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return [str(x).strip() for x in v if str(x).strip()]
    s = str(v).strip()
    if not s:
        return []
    s = s.replace("\r\n", "\n").replace("\r", "\n").replace("@@", "\n").replace("&&", "\n")
    parts = [p.strip() for p in s.split("\n") if p.strip()]
    if len(parts) > 1:
        return parts
    if "&" in s:
        cand = [p.strip() for p in s.split("&") if p.strip()]
        if len(cand) > 1 and all(re.match(r"^[A-Za-z0-9_.\-]+=", p) for p in cand):
            return cand
    return [s]

def _split_pairs(v):
    """账号#密码 → [(账号, 密码)...]；# 或 : 切一次；也可写列表；不做 & 切分。"""
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

def _sleep_between(i):
    if i <= 0:
        return
    spec = (_env("DELAY", BUILTIN_DELAY) or "").strip()
    if not spec:
        return
    lo_s, _, hi_s = spec.replace("~", "-").partition("-")
    try:
        lo = float(lo_s.strip()); hi = float(hi_s.strip()) if hi_s.strip() else lo
    except ValueError:
        return
    if hi < lo:
        lo, hi = hi, lo
    sec = lo if hi <= lo else random.uniform(lo, hi)
    if sec > 0:
        time.sleep(sec)


# ---------- 主流程 ----------
def run():
    proxy = _env("PROXY", BUILTIN_PROXY)
    tls = _env("TLS_IMPERSONATE", BUILTIN_TLS_IMPERSONATE)
    force_login = str(_env("FORCE_LOGIN", BUILTIN_FORCE_LOGIN)).strip().lower() in ("1", "true", "yes", "y", "on")
    cf_cookie = (_env("COOKIE", BUILTIN_COOKIE) or "").strip()

    pairs = _split_pairs(_env("ACCOUNT", BUILTIN_ACCOUNT))
    if not pairs:
        users = _split_multi(_env("USER", BUILTIN_USER))
        passes = _split_multi(_env("PASS", BUILTIN_PASS))
        if users and passes:
            if len(passes) == 1 and len(users) > 1:
                passes = passes * len(users)
            if len(users) == len(passes):
                pairs = list(zip(users, passes))
    if not pairs:
        if cf_cookie and ("wordpress_logged_in" in cf_cookie or "wordpress_sec" in cf_cookie):
            # cookie-only 模式：已提供登录态 cookie，直接签到，无需账号密码
            pairs = [("__cookie__", "")]
        else:
            print("请配置环境变量 %s_ACCOUNT（账号#密码，多组换行/@@/&&分隔）或 %s_USER/%s_PASS；"
                  "若仅用 cookie 登录态，请通过 %s_COOKIE 提供含 wordpress_logged_in 的 cookie"
                  % (SITE_TAG, SITE_TAG, SITE_TAG, SITE_TAG))
            return 1

    valid = []
    for u, p in pairs:
        if u == "__cookie__":
            valid.append((u, p))  # cookie-only 模式本就无密码，跳过占位检测
            continue
        if _is_placeholder(u) or _is_placeholder(p):
            print("以下账户未填写有效凭证（空值或占位值）：%s" % u)
            continue
        valid.append((u, p))
    if not valid:
        print("没有填写有效的账户凭证")
        return 1

    cache = _load_cache()
    failed = 0
    total = len(valid)
    print("共 %d 个账户待执行" % total)
    for i, (u, p) in enumerate(valid):
        _sleep_between(i)
        sess = _mk_session(tls, proxy)
        if sess is not None:
            _warmup(sess)
        if u == "__cookie__":
            # 仅 cookie 登录态：跳过登录，直接签到
            print("=" * 8 + " [cookie-only] " + "=" * 8)
            ok, out = do_checkin(sess, cf_cookie, "")
            print(out if out else "(无输出)")
            if not ok:
                failed += 1
            continue
        print("=" * 8 + " [%d/%d] %s " % (i + 1, total, u) + "=" * 8)
        label = u
        use_cache = (not force_login) and bool(label in cache and cache[label].get("cookie"))
        wp_cookie = ""
        if use_cache:
            wp_cookie = cache[label]["cookie"]
            print("  [使用缓存 cookie，跳过登录]")
        if force_login:
            print("  [强制账号密码登录，跳过 cookie 缓存]")
        ok, out = False, ""
        if use_cache:
            ok, out = do_checkin(sess, cf_cookie, wp_cookie)
        if not ok:
            lok, lmsg, wp_cookie = do_login(sess, u, p, cf_cookie)
            if not lok:
                print(lmsg)
                failed += 1
                continue
            print("  登录：%s" % lmsg)
            if wp_cookie and not force_login:
                cache[label] = {"cookie": wp_cookie, "ts": int(time.time())}
                _save_cache(cache)
            ok, out = do_checkin(sess, cf_cookie, wp_cookie)
        print(out if out else "(无输出)")
        if not ok:
            failed += 1
    print("=" * 26)
    print("执行完毕：共 %d 个账户，成功 %d，失败 %d" % (total, total - failed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run())
