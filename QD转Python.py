#!/usr/bin/env python3
"""QD转Python.py —— 将 QD 格式 .har 任务模板转换为零依赖 Python 签到脚本。

适用对象：QD / 青龙 的任务模板 .har（含 comment / request / rule 字段，请求体用
          {{username|urlencode}} 这类模板变量，规则用 success_asserts /
          failed_asserts / extract_variables，日志用 api://util/string/replace
          或 api://util/unicode（参数可在 GET query 或 POST body 中）。
          不适用于浏览器直接导出的 HAR（那是另一种结构）。

用法：
    python QD转Python.py input.har [-o output.py]
    # 不指定 -o 时按鉴权模式输出 <input_stem>_账号.py / <input_stem>_Cookie.py
    # -c/--no-cross：限制跨域(跳过第三方授权/API 域名步骤)；默认放开跨域(仅跳过静态资源)，保留同站与跨域业务步骤
    # --cron "10 8 * * *"：登记到导入列表时使用的 cron（默认：已有条目沿用原值，新条目自动分配下一个 5 分钟槽位）
    # --tasks <path>：导入列表路径（默认 仓库根/arcadia/tasks.json；文件不存在则跳过导入，不新建）
    # --no-import：只生成脚本，不写入导入列表

生成完成后会**自动把脚本登记到导入列表**（arcadia/tasks.json，键为文件名、值为 cron）：
列表中已有同名条目则覆盖其 cron，检测不到列表文件（或 --no-import）则跳过导入、不报错。

生成的脚本：纯标准库(urllib)、零依赖；配置可用环境变量（以 SITE_TAG 为前缀，
如 MNPC_ACCOUNT（格式 账号#密码，多组换行/@@/&& 分隔）/MNPC_COOKIE）或脚本顶部 BUILTIN_* 常量。
直接 `python output.py` 即可运行。
"""
import sys
import os
import re
import json
import pprint
import argparse

# ====== 生成的 Python 脚本模板（用哨兵 ___BASE___ / ___ENTRIES___ 替换）======
TEMPLATE = r'''#!/usr/bin/env python3
# 由 QD转Python.py 自动生成（QD 格式 .har -> 零依赖 Python）
# 运行：python __FILE__   （鉴权信息见下方内置配置；环境变量 ___ENVDESC___ 优先）
# 多账户：账号用 <SITE_TAG>_ACCOUNT 一次填完（"账号#密码"，多组换行 / @@ / && 分隔）；Cookie 用 换行 / @@ / && 分隔，也可直接写 Python 列表
# ___CACHENOTE___
import os, re, sys, ssl, json, gzip, zlib, html, time, random, hashlib, subprocess, urllib.request, urllib.parse, urllib.error, http.client
from http.cookiejar import CookieJar

# ---- 环境变量命名空间（防止多个脚本共用同一批 XY_* 变量而串号）----
# 本脚本所有“覆盖用”环境变量都以 SITE_TAG 为前缀；改下面这一行即可整体改名
# （如 SITE_TAG="MNPC" 时，账号用 MNPC_ACCOUNT（格式 账号#密码），Cookie 用 MNPC_COOKIE，代理 MNPC_PROXY）。
SITE_TAG = "___TAG___"

def _env(name, default):
    """带命名空间的环境变量读取：<SITE_TAG>_NAME 优先，缺失则回退 default。"""
    v = os.environ.get("%s_%s" % (SITE_TAG, name))
    return v if v is not None else default

# ===== 内置配置（环境变量优先；按鉴权模式自动生成，只列出需要的项）=====
# 多账户另一种写法（列表）：BUILTIN_ACCOUNT = ["u1#p1", "u2#p2"]；旧写法 BUILTIN_USER = ["user1", "user2"]；BUILTIN_PASS = ["p1", "p2"] 仍兼容
___CONFIG___
# =================================

BASE = ___BASE___
BUILTIN_ALLOW_CROSS = ___ALLOW_CROSS___   # 默认放开跨域(仅跳过静态资源)；设 <SITE_TAG>_ALLOW_CROSS=0/false 才限制跨域(跳过第三方域名步骤)
ALLOW_CROSS = str(_env("ALLOW_CROSS", BUILTIN_ALLOW_CROSS)).strip().lower() in ("1","true","yes","y","on")

ENTRIES = ___ENTRIES___

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/96.0.4664.45 Safari/537.36")


def build_opener(proxy, use_cookiejar=True):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    h = [urllib.request.HTTPSHandler(context=ctx)]
    jar = None
    # Cookie 模式：鉴权 Cookie 由请求头手动携带，关闭 jar 以免重复 Cookie 头
    if use_cookiejar:
        jar = CookieJar()
        h.append(urllib.request.HTTPCookieProcessor(jar))
    if proxy:
        h.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    return urllib.request.build_opener(*h), jar


def _apply_filter(val, filt):
    m = re.match(r"^(\w+)", filt)
    name = m.group(1) if m else ""
    if name == "urlencode":
        return urllib.parse.quote(str(val), safe="")
    if name == "unicode":
        return str(val)
    if name == "default":
        am = re.search(r"default\(\s*(['\"]?)(.*?)\1\s*\)", filt, re.S)
        d = am.group(2) if am else ""
        return d if not val else val
    return val


def _eval_val(expr, ctx):
    expr = expr.strip()
    m = re.match(r"^([A-Za-z_]\w*)\((.*)\)$", expr, re.S)
    if m:
        inner = m.group(2).strip()
        val = _render_expr(inner, ctx, False)
        if m.group(1) == "unicode":
            return str(val)
        if m.group(1) == "md5":
            return hashlib.md5(str(val).encode("utf-8")).hexdigest()
        return val
    return ctx.get(expr, "")


def _render_expr(expr, ctx, display):
    expr = expr.strip()
    depth = 0
    idx = -1
    for i, ch in enumerate(expr):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "|" and depth == 0:
            idx = i
            break
    if idx < 0:
        return _eval_val(expr, ctx)
    left = expr[:idx]
    filt = expr[idx + 1:].strip()
    val = _eval_val(left, ctx)
    if display and filt.startswith("urlencode"):
        return val
    return _apply_filter(val, filt)


def render(tpl, ctx, display=False):
    def repl(mm):
        return _render_expr(mm.group(1), ctx, display)
    return re.sub(r"\{\{\s*(.*?)\s*\}\}", repl, tpl)


def _split_multi(v):
    """把配置项拆成多条：支持 Python 列表，或 换行 / @@ / && / & 分隔的字符串。"""
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
        # 单个 & 仅在切出的每段都以 key= 开头时才当分隔符，避免误伤 Cookie 值里的 &
        cand = [p.strip() for p in s.split("&") if p.strip()]
        if len(cand) > 1 and all(re.match(r"^[A-Za-z0-9_.\-]+=", p) for p in cand):
            return cand
    return [s]


def _split_pairs(v):
    """把“账号+密码”配置拆成 [(账号, 密码), ...]（借鉴青龙“单变量装账号密码”的写法）。

    格式（账号模式推荐只用一个变量 <SITE_TAG>_ACCOUNT / BUILTIN_ACCOUNT）：
        单账号： "user#pass"                    # # 也可写成 : ，取第一个出现的分隔符
        多账号： "user1#pass1@@user2#pass2"      # 或换行 / && 分隔，与 Cookie 多账户写法一致
        只要账号不要密码： "user"                # 密码为空（个别站点只需账号）
        也可写 Python 列表：["user1#pass1", "user2#pass2"]
    注意：这里不做 & 切分，避免把密码里出现的 & 当成分隔符。
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


# 通用"未登录/凭证失效"关键词（响应体命中即判失败，避免仅靠 HTTP 200 误判成功）。
# 刻意不含单独的"登录"二字，以免误伤"登录天数""登录成功"这类正常文案。
# 拆成两组、分阶段检查（见标准 §10 第 16 条）：
#   NOT_LOGGED_IN_RE —— “未登录态”提示（请先登录/未登录/需要登录/登录后再/凭证失效）。
#       这类词在【登录前】必然出现（登录页、取 csrf token 的页面对游客本就显示"未登录"），
#       因此只在“已建立登录态”（登录步骤执行完 / 复用缓存 cookie / Cookie 模式）之后检查。
#   AUTH_ERROR_RE    —— 硬错误（密码错误/账号或密码错误/登录失败/用户不存在），
#       任何步骤命中都判失败，包括登录步骤本身。
NOT_LOGGED_IN_RE = (r"请(?:先)?(?:登录|登陆)|未(?:登录|登陆)|尚未(?:登录|登陆)"
                    r"|需要(?:登录|登陆)|(?:登录|登陆)后再|凭证(?:已)?失效")
AUTH_ERROR_RE = (r"用户不(?:存在)|密码错误|账号或密码错误|登录失败|登陆失败"
                 r"|凭证(?:已)?失效")


def _is_placeholder(v):
    """配置值是否明显未填写（空 / 占位符 / 单字符重复），用于提前报错而不是空跑一遍。"""
    s = str(v if v is not None else "").strip()
    if not s:
        return True
    if s.lower() in ("xxx", "xxxx", "xxxxx", "yyyy", "aaa", "abc", "test", "test1",
                     "your_account", "your_password", "your_cookie",
                     "account", "password", "cookie", "changeme", "none", "null"):
        return True
    # 单一字符重复且长度 <= 6（如 xxx / 111111）视为占位
    if len(s) <= 6 and len(set(s.lower())) == 1:
        return True
    return False


def _ensure_dep(pkg, import_name=None):
    """依赖自检：缺失时按需自动安装（<SITE_TAG>_AUTO_INSTALL=0 可关闭，默认开启）。

    用于 curl_cffi 这类"可选但部分站点必需"的依赖，避免新环境/容器里每次手工 pip install。
    安装失败只提示不崩溃；静默安装（不污染签到输出）。
    """
    mod = import_name or pkg
    try:
        return __import__(mod)
    except ImportError:
        pass
    if str(_env("AUTO_INSTALL", BUILTIN_AUTO_INSTALL)).strip().lower() \
            not in ("1", "true", "yes", "y", "on"):
        return None
    print("缺少依赖 %s，正在尝试自动安装（%s_AUTO_INSTALL=0 可关闭）..." % (pkg, SITE_TAG))
    try:
        subprocess.run([sys.executable, "-m", "pip", "install",
                        "--disable-pip-version-check", pkg],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except Exception as ex:
        print("自动安装 %s 失败：%s；请手动执行： pip install %s" % (pkg, ex, pkg))
        return None
    try:
        return __import__(mod)
    except ImportError:
        print("自动安装 %s 后仍无法导入，请手动执行： pip install %s" % (pkg, pkg))
        return None


def _clean_domain(d):
    """域名统一去掉协议头，保证拼出 https://<domain>/path"""
    return re.sub(r"^https?://", "", str(d or "")).strip().rstrip("/")


def _sleep_between(i):
    """账户间隔：第 1 个账户前不等待。XY_DELAY 支持 固定秒(3) 或 区间随机(2-5)，0=不等待。"""
    if i <= 0:
        return
    spec = (_env("DELAY", BUILTIN_DELAY) or "").strip()
    if not spec:
        return
    lo_s, _, hi_s = spec.replace("~", "-").partition("-")
    try:
        lo = float(lo_s.strip())
        hi = float(hi_s.strip()) if hi_s.strip() else lo
    except ValueError:
        return
    if hi < lo:
        lo, hi = hi, lo
    sec = lo if hi <= lo else random.uniform(lo, hi)
    if sec > 0:
        time.sleep(sec)


___CACHE_HELPERS___


def run_one(proxy, ctx, use_cookiejar, skip_login=False, capture_vars=None, session=None):
    """执行一遍全部步骤（单个账户）。返回 (是否成功, 输出文本, cookie串, 登录变量)。
    session 为 curl_cffi.Session（启用浏览器级 TLS 指纹时传入），为 None 则走标准库 urllib。"""
    if session is None:
        op, jar = build_opener(proxy, use_cookiejar=use_cookiejar)
    else:
        op, jar = None, None

    def _send(method, url, headers, postdata):
        # 统一发送请求，返回 (status_str, raw_bytes, err_str)。err 非空=网络层失败。
        if session is None:
            try:
                req = urllib.request.Request(url, data=postdata,
                                             headers=headers, method=method)
                r = op.open(req, timeout=60)
                raw = r.read()
                # 兜底：若服务器仍返回压缩响应，解压之（已去掉 Accept-Encoding，通常不需要）
                ce = (r.headers.get("Content-Encoding") or "").lower()
                if "gzip" in ce:
                    raw = gzip.decompress(raw)
                elif "deflate" in ce:
                    raw = zlib.decompress(raw)
                return str(r.status), raw, None
            except urllib.error.HTTPError as ex:
                # 服务器返回错误状态码（如 403/401）但可能仍带响应体（错误提示/guard 页）
                raw = b""
                try:
                    raw = ex.read()
                except Exception:
                    pass
                return str(ex.code), raw, None
            except urllib.error.URLError as ex:
                return None, None, "网络错误: %s" % ex
            except (TimeoutError, ConnectionError, http.client.HTTPException) as ex:
                # 单个请求网络异常(超时/连接被重置/服务器提前断连)不应让整个脚本崩溃
                return None, None, "请求异常(%s): %s\n%s" % (type(ex).__name__, url, ex)
        # 浏览器级 TLS 指纹模式（curl_cffi）：HTTP 错误码不抛异常，直接返回状态码，
        # 且响应已自动解压，无需此处 gzip 处理。
        try:
            resp = session.request(method, url, headers=headers,
                                   data=postdata, timeout=60)
        except Exception as ex:
            return None, None, "请求异常(%s): %s\n%s" % (type(ex).__name__, url, ex)
        return str(resp.status_code), resp.content, None

    logs = []
    # 跨域判定基准：先把 BASE 里的 {{DOMAIN}} 等占位符渲染成真实域名，
    # 否则域名参数化脚本(BASE="https://{{DOMAIN}}")会让 _host(BASE) 得到字面量
    # "{{domain}}"，导致所有同域步骤被误判为跨域而整步跳过(表现为“解析不全”)。
    base_url = render(BASE, ctx)
    # 浏览器全量 HAR 常混入大量静态资源(css/图片/字体)与跨域第三方请求
    # (如验证码、外部字体)，它们与签到业务无关，且外部资源易慢/超时。
    # 统一跳过：命中静态扩展名，或请求域名与站点 BASE 不同(开启 ALLOW_CROSS 时不跳过跨域)。
    _static_ext = (".css", ".png", ".jpg", ".jpeg", ".gif", ".webp",
                   ".svg", ".ico", ".woff", ".woff2", ".ttf", ".otf", ".eot",
                   ".map", ".bmp", ".avif")

    def _host(u):
        try:
            return urllib.parse.urlparse(u).netloc.lower()
        except Exception:
            return ""

    def _is_static(u):
        u = u.split("?", 1)[0].split("#", 1)[0].lower()
        return u.endswith(_static_ext)

    def _cross_domain(u):
        # 仅当请求主机与 BASE 属于“完全不同站点”时才视为跨域（用于剔除整页 HAR
        # 里的第三方静态/验证码资源）。同一注册域下的子域（如 api.ai1foo.com
        # 之于 ai1foo.com、www.x.com 之于 x.com、cdn.x.com 之于 x.com）视为本站，
        # 不跳过——否则像“签到/积分接口挂在 api. 子域”的站点会被误杀，导致变量
        # 提取为空（表现为转换脚本“解析不全”：连续天数、积分等全是空）。
        # 开启 ALLOW_CROSS 时本函数不参与过滤(见下方过滤器)。
        if not base_url:
            return False
        h, b = _host(u), _host(base_url)
        if not h or not b or h == b:
            return False
        if h.endswith("." + b) or b.endswith("." + h):
            return False
        return True

    # 是否已建立登录态：只有建立之后才用"未登录"类关键词判失败。
    # 登录前置步骤(取 csrf/token 用的登录页)对游客必然返回"未登录"，此时就检查会把
    # 正常登录流程误判成"凭证失效"（见标准 §10 第 16 条）。
    # 无登录步骤(Cookie 模式)或复用缓存 cookie 跳过登录(skip_login)时，视为已登录。
    has_login_entry = any(e.get("login") for e in ENTRIES)
    auth_ready = (not has_login_entry) or bool(skip_login)
    for e in ENTRIES:
        if skip_login and e.get("login"):
            continue
        url = render(e["request"]["url"], ctx)
        # 注意：跨域/静态过滤只对真实 http(s) 请求生效；api:// 内部工具(如
        # api://util/unicode 汇总步)不是网络请求，且其 url 会被 urlparse 误解析出
        # 伪主机(如 "util")，若不过滤此条件会被误判为跨域而跳过，导致脚本"(无输出)"。
        if url.startswith("http") and (_is_static(url) or (not ALLOW_CROSS and _cross_domain(url))):
            continue
        if url.startswith("api://"):
            # QD 内部工具(api://util/...)，不真正发请求，本地格式化后输出。
            # 参数来源：GET 在 url query，POST 在 data(body)
            body_data = render(e["request"].get("data", ""), ctx, display=True)
            q = urllib.parse.urlparse(url).query
            params = {}
            params.update(urllib.parse.parse_qs(q))
            if body_data:
                params.update(urllib.parse.parse_qs(body_data))
            p = {k: urllib.parse.unquote(v[0]) for k, v in params.items()}
            path = url[len("api://"):]
            if path.startswith("util/unicode"):
                out = p.get("content", p.get("s", ""))
                if p.get("html_unescape") == "true":
                    out = html.unescape(out)
                out = re.sub(r"\\u([0-9a-fA-F]{4})",
                             lambda m: chr(int(m.group(1), 16)), out)
                out = out.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\r", "\n")
            elif path.startswith("util/string/replace"):
                out = p.get("s", "")
                tgt, rep = p.get("t", ""), p.get("p", "")
                if p.get("r") == "text":
                    out = out.replace(tgt, rep)
                elif tgt:
                    out = re.sub(tgt, rep, out)
                out = out.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\r", "\n")
            else:  # 兜底：直接渲染 s / content
                out = p.get("s") or p.get("content") or ""
                out = out.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\r", "\n")
            logs.append(out)
            # api:// 步骤的断言作用于“渲染后的内容”（QD 语义：检查 {{变量}} 的结果），
            # 而非该工具接口的真实响应（QD转Python 本地模拟，并无 HTTP 响应体）。
            # 失败断言直接对 out 匹配，即可捕捉“未登录/失败”等文案——例如失效 cookie
            # 导致签到接口返回“请登录后签到”，渲染进 msg 后被此处命中，进而触发
            # run() 的缓存回退到账号密码登录。success_asserts 对本步骤意义不大（
            # 没有真实响应体可比对），故只对失败断言做检查。
            rule = e.get("rule", {})
            for a in rule.get("failed_asserts", []):
                if re.search(a["re"], out):
                    return False, "失败断言命中（请求未成功）: %s\n响应预览: %s" % (a, out[:500]), None, None
            # 汇总步（api://util/unicode）引用的变量若全部为空，说明没拿到任何业务数据
            # （多半是未登录 / Cookie 失效），判失败而不是"成功"。
            if str(_env("STRICT_EMPTY", BUILTIN_STRICT_EMPTY)).strip().lower() \
                    in ("1", "true", "yes", "y", "on"):
                raw_tpl = (e["request"].get("data", "") or "") + "&" + urllib.parse.urlparse(url).query
                names = re.findall(r"\{\{\s*([A-Za-z_]\w*)[^}]*\}\}", raw_tpl)
                if names and all(not str(ctx.get(n, "")).strip() for n in names):
                    return False, ("未获取到任何数据（汇总变量 %s 全部为空，多半未登录或 Cookie 失效）；"
                                   "确认该站点本就不返回数据可用 %s_STRICT_EMPTY=0 关闭此检查"
                                   % (",".join(names), SITE_TAG)), None, None
            continue
        method = e["request"].get("method", "GET")
        headers = {h["name"]: render(h["value"], ctx)
                   for h in e["request"].get("headers", [])
                   if not h["name"].startswith(":")
                   and h["name"].lower() != "accept-encoding"}
        # QD 会给任务内每个请求都带上 Cookie；有些步骤在 HAR 里没写 Cookie 头，
        # 靠 QD 的会话维持。这里给未显式声明 Cookie 的步骤补齐，避免变成游客态。
        ck = ctx.get("cookie", "")
        if ck and not any(k.lower() == "cookie" for k in headers):
            headers["Cookie"] = ck
        data = e["request"].get("data", "")
        postdata = render(data, ctx).encode("utf-8") if data else None
        status, raw, err = _send(method, url, headers, postdata)
        if err:
            return False, err, None, None
        body = raw.decode("utf-8", "ignore")
        # 服务器常返回带 \uXXXX 转义的 JSON，统一解码成中文，
        # 否则断言里的“登录成功”等中文匹配不上，预览也看不懂
        try:
            body = json.dumps(json.loads(body), ensure_ascii=False,
                              separators=(",", ":"))
        except Exception:
            pass
        rule = e.get("rule", {})
        # 先查失败断言（能给出明确原因，如“用户名或密码错误”）
        for a in rule.get("failed_asserts", []):
            tgt = status if a.get("from") == "status" else body
            if re.search(a["re"], tgt):
                return False, "失败断言命中（请求未成功）: %s\n响应预览: %s" % (a, body[:500]), None, None
        # 通用未登录/凭证失效检测：站点常以 HTTP 200 返回“请先登录”之类提示，
        # 仅凭 success_asserts(status 200) 会误判成功，这里统一兜底。
        # 硬错误（密码错误/账号不存在/登录失败…）任何步骤都查；"未登录"类提示只在
        # 已建立登录态之后查，避免登录前置步骤(登录页/取 token 页)被误判失败。
        _m = re.search(AUTH_ERROR_RE, body)
        if _m:
            return False, ("疑似凭证错误（响应含：%s）\n响应预览: %s"
                           % (_m.group(0), body[:300])), None, None
        if auth_ready:
            _m = re.search(NOT_LOGGED_IN_RE, body)
            if _m:
                return False, ("疑似未登录或凭证失效（响应含：%s）\n响应预览: %s"
                               % (_m.group(0), body[:300])), None, None
        # 成功断言为“或”逻辑：任一条命中即视为成功；全不命中才判失败
        ok = False
        for a in rule.get("success_asserts", []):
            tgt = status if a.get("from") == "status" else body
            if re.search(a["re"], tgt):
                ok = True
                break
        if not ok and rule.get("success_asserts"):
            return False, "成功断言未命中，视为失败: %s\n响应预览: %s" % (rule["success_asserts"], body[:500]), None, None
        for v in rule.get("extract_variables", []):
            m = re.search(v["re"], body)
            if m:
                ctx[v["name"]] = m.group(1) if m.groups() else m.group(0)
        if e.get("login"):
            # 登录步骤执行完即视为已建立登录态，其后步骤才启用"未登录"关键词检测
            auth_ready = True
    captured = {}
    if capture_vars:
        captured = {n: ctx.get(n) for n in capture_vars if n in ctx}
    cookie_str = None
    if session is not None:
        # 浏览器级 TLS 模式：从 curl_cffi 会话 jar 取最新 cookie
        cookie_str = "; ".join("%s=%s" % (k, v)
                               for k, v in session.cookies.get_dict().items())
    if not cookie_str and jar:
        cookie_str = "; ".join("%s=%s" % (c.name, c.value) for c in jar)
    if not cookie_str:
        cookie_str = ctx.get("cookie")
    # 通知内容取最后一个非空输出（通常是最终的 api://util/unicode 汇总步），
    # 避免把登录中间态(如“登录成功”)与最终签到结果拼成多行重复通知。
    nonempty = [x for x in logs if x]
    return True, (nonempty[-1] if nonempty else ""), cookie_str, captured


___RUN___


if __name__ == "__main__":
    sys.exit(run())
'''
# ======================================================================

def derive_tag(base, name=""):
    """从站点 BASE 域名派生环境变量命名空间前缀（如 www.mnpc.net -> MNPC）。
    base 是模板（如 https://{{DOMAIN}}）或无法提取时，回退用文件名 stem。"""
    m = re.search(r"https?://([^/]+)", base or "")
    netloc = m.group(1) if m else (base or "")
    if "{{" in netloc:          # 模板域名（如 {{DOMAIN}}）无法作为前缀，回退文件名
        tag = ""
    else:
        labels = netloc.split(".")
        sld = labels[-2] if len(labels) >= 2 else labels[0]
        tag = re.sub(r"[^A-Z0-9]", "", sld.upper())
    if not tag and name:
        tag = re.sub(r"[^A-Z0-9]", "", name.upper())
    if tag and tag[0].isdigit():
        tag = "Q" + tag
    return tag or "SITE"


def _out_name(input_path, cookie_mode):
    """按鉴权模式推导输出文件名：<stem>_账号.py（账号模式）/ <stem>_Cookie.py（Cookie 模式）。

    若输入名已带与模式相同的括号标注（如 `NS中文网(账号).har`），先去掉该标注，
    避免出现 `NS中文网(账号)_账号.py` 这类重复。
    """
    stem = os.path.splitext(input_path)[0]
    mode_word = "Cookie" if cookie_mode else "账号"
    stem = re.sub(r"[（(]\s*(?:账号|账号密码|Cookie|cookie)\s*[)）]\s*$", "", stem)
    return stem + "_" + mode_word + ".py"


def _default_tasks_path():
    """导入列表默认路径：本脚本所在仓库的 arcadia/tasks.json。"""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "arcadia", "tasks.json")


def _cron_hm(expr):
    """把 cron 解析成 (hour, minute) 用于排序；非"每天固定时刻"的表达式排到最后。"""
    parts = (expr or "").split()
    if len(parts) == 6:
        parts = parts[1:]
    if len(parts) != 5:
        return (99, 99)
    minute, hour = parts[0], parts[1]

    def num(tok):
        tok = tok.split("/")[0].split("-")[0]
        return int(tok) if tok.isdigit() else -1

    if parts[2] != "*" or parts[3] != "*" or parts[4] != "*":
        return (99, 99)
    return (num(hour), num(minute))


def _valid_cron(expr):
    """轻量校验：5 段 / 6 段，每段只含 数字 * , - / 。完整校验交给 生成定时头.py。"""
    parts = (expr or "").split()
    if len(parts) not in (5, 6):
        return False
    return all(re.match(r"^[\d*,/\-]+$", p) for p in parts)


SLOT_STEP = 5          # 自动排班的粒度（分钟）
DEFAULT_SLOT_RANGE = (7, 21)   # 自动排班的时间窗（小时，闭区间；按运行环境的时区理解）


def _parse_slot_range(spec, default=DEFAULT_SLOT_RANGE):
    """解析时间窗："7-21" / "7~21" / "22-6"（跨天）；非法则回退默认。"""
    s = (spec or "").strip().replace("~", "-")
    m = re.match(r"^(\d{1,2})\s*-\s*(\d{1,2})$", s)
    if not m:
        return default
    a, b = int(m.group(1)), int(m.group(2))
    if not (0 <= a <= 23 and 0 <= b <= 23):
        return default
    return (a, b)


def _slots_in_range(start, end, step=SLOT_STEP):
    """窗口内的 (hour, minute) 序列；start>end 表示跨天（如 22-6），start==end 视为全天。"""
    span = ((end - start) % 24) or 24
    n = max(1, span * 60 // step)
    slots = []
    h, m = start, 0
    for _ in range(n):
        slots.append((h, m))
        m += step
        if m >= 60:
            m = 0
            h = (h + 1) % 24
    return slots


def _next_free_slot(tasks, slot_range=DEFAULT_SLOT_RANGE):
    """在"时间窗"内给新脚本找下一个空闲槽位（默认 7-21 点、每 5 分钟一档）。

    窗口排满时退化为全天找空位，保证仍能生成合法 cron。
    """
    used = set()
    for c in tasks.values():
        h, m = _cron_hm(c)
        if h != 99:
            used.add((h, m))
    for h, m in _slots_in_range(slot_range[0], slot_range[1]):
        if (h, m) not in used:
            return "%d %d * * *" % (m, h)
    for h in range(24):
        for m in range(0, 60, SLOT_STEP):
            if (h, m) not in used:
                return "%d %d * * *" % (m, h)
    return "0 8 * * *"


def import_to_tasks(out_path, cron=None, tasks_path=None, slot_range=DEFAULT_SLOT_RANGE):
    """把生成的脚本登记到"导入列表"（arcadia/tasks.json：文件名 -> cron）。

    - 列表中已有同名条目 → 覆盖其 cron（未显式给 cron 时**沿用原值**，避免打乱排班）；
    - 新条目未给 cron → 在 slot_range 时间窗内自动分配下一个空闲槽位（默认 7-21 点、5 分钟一档）；
    - 列表文件不存在 / 解析失败 / 格式不是 文件名->cron 对象 → **跳过导入**，不新建不报错
      （便于在仓库外单独使用转换脚本）。
    """
    tasks_path = tasks_path or _default_tasks_path()
    name = os.path.basename(out_path)
    if not os.path.isfile(tasks_path):
        print("未检测到导入列表：%s（跳过导入，脚本已生成：%s）" % (tasks_path, out_path))
        return False
    try:
        with open(tasks_path, encoding="utf-8") as f:
            tasks = json.load(f)
    except Exception as ex:
        print("导入列表读取失败，跳过导入：%s（%s）" % (tasks_path, ex))
        return False
    if not isinstance(tasks, dict) or not all(isinstance(v, str) for v in tasks.values()):
        print("导入列表格式不是 文件名->cron 的对象，跳过导入：%s" % tasks_path)
        return False
    existed = name in tasks
    new_cron = (cron or "").strip()
    if new_cron and not _valid_cron(new_cron):
        print("--cron 不是合法的 cron 表达式：%s（改用默认分配）" % new_cron)
        new_cron = ""
    if not new_cron:
        new_cron = tasks[name] if existed else _next_free_slot(tasks, slot_range)
    tasks[name] = new_cron
    ordered = {}
    for k in sorted(tasks, key=lambda k: (_cron_hm(tasks[k]), k)):
        ordered[k] = tasks[k]
    try:
        with open(tasks_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(ordered, ensure_ascii=False, indent=2) + "\n")
    except Exception as ex:
        print("写入导入列表失败：%s（%s）" % (tasks_path, ex))
        return False
    print("导入列表已%s：%s -> %s（%s）" % ("覆盖" if existed else "新增", name, new_cron, tasks_path))
    if not existed and not cron:
        print("  （自动分配，时间窗 %d-%d 点、每 %d 分钟一档）" % (slot_range[0], slot_range[1], SLOT_STEP))
    return True


def main():
    ap = argparse.ArgumentParser(description="QD .har -> Python 签到脚本")
    ap.add_argument("input", help="QD 格式 .har 文件")
    ap.add_argument("-o", "--output", help="输出 .py 路径（默认 <输入>_账号.py 或 <输入>_Cookie.py，按鉴权模式自动选择）")
    ap.add_argument("-f", "--force", action="store_true",
                    help="强制覆盖已存在的输出文件（不保留旧 BUILTIN_* 配置）")
    ap.add_argument("-t", "--tag", default=None,
                    help="环境变量命名空间前缀（如 MNPC/NS211）；默认从站点域名自动派生")
    ap.add_argument("-c", "--no-cross", action="store_true",
                    help="限制跨域：跳过第三方授权/API 域名步骤（默认放开跨域，仅跳过静态资源）")
    ap.add_argument("--cron", default=None,
                    help="登记到导入列表时使用的 cron（默认：已有条目沿用原值，新条目自动分配下一个 5 分钟槽位）")
    ap.add_argument("--tasks", default=None,
                    help="导入列表路径（默认 仓库根/arcadia/tasks.json；文件不存在则跳过导入）")
    ap.add_argument("--no-import", action="store_true",
                    help="生成后不写入导入列表")
    ap.add_argument("--slot-range", default=None,
                    help="自动分配时间的窗口（小时，默认 7-21，支持跨天如 22-6；"
                         "也可用环境变量 QD_SLOT_RANGE；按运行环境的时区理解）")
    args = ap.parse_args()

    with open(args.input, encoding="utf-8") as f:
        raw = json.load(f)

    # QD 模板可能是单对象或数组；统一成列表
    if isinstance(raw, dict):
        entries = raw.get("entries") or raw.get("log", {}).get("entries") or [raw]
    else:
        entries = raw
    if not isinstance(entries, list):
        entries = [entries]

    # 校验是否为 QD 格式（至少含 request 字段）
    for e in entries:
        if "request" not in e:
            print("看起来不是 QD 格式 .har（缺少 request 字段），转换可能不准确。")
            break

    # 解码正则里的 \uXXXX 字面转义为真实字符（QD 模板常以 \uXXXX 存中文）
    def _fix_unicode(s):
        if not isinstance(s, str):
            return s
        return re.sub(r"\\*u([0-9a-fA-F]{4})",
                      lambda m: chr(int(m.group(1), 16)), s)

    for e in entries:
        for key in ("success_asserts", "failed_asserts", "extract_variables"):
            for v in (e.get("rule", {}) or {}).get(key, []) or []:
                if "re" in v:
                    v["re"] = _fix_unicode(v["re"])

    base = ""
    for e in entries:
        u = e.get("request", {}).get("url", "")
        if u.startswith("http"):
            base = "/".join(u.split("/")[:3])
            break
    tag = args.tag or derive_tag(base, os.path.splitext(os.path.basename(args.input))[0])

    # 自动识别 Cookie 鉴权模式：任何请求含 Cookie 头或 {{cookie}} 变量即视为 Cookie 模式。
    # 若 Cookie 头是真实字面值，则改写为 {{cookie}} 占位并作为内置默认值，便于用户复用/刷新。
    cookie_mode = False
    cookie_default = ""
    for e in entries:
        for hh in e.get("request", {}).get("headers", []) or []:
            if hh.get("name", "").lower() == "cookie" or "{{cookie}}" in hh.get("value", ""):
                cookie_mode = True
                if hh.get("name", "").lower() == "cookie" and "{{" not in hh.get("value", ""):
                    cookie_default = hh["value"]
                    hh["value"] = "{{cookie}}"

    # 自动识别自定义域名：URL 含 {{domain}} 时需用户提供域名
    domain_mode = False
    domain_default = ""
    for e in entries:
        if "{{domain}}" in e.get("request", {}).get("url", "").lower():
            domain_mode = True

    # 自动识别登录步骤（仅账号模式需要）：某 entry 的 data 同时含“密码类”与“账号类”
    # 模板变量，则标记 login=True；缓存命中时跳过该步骤，改用缓存的 cookie/vars。
    if not cookie_mode:
        for e in entries:
            data = e.get("request", {}).get("data", "") or ""
            if not data:
                continue
            has_pass = re.search(r"\{\{\s*(?:password|pwd|pass)\b[^}]*\}\}", data, re.I)
            has_user = re.search(r"\{\{\s*(?:username|user|account|email|uid|name)\b[^}]*\}\}", data, re.I)
            if has_pass and has_user:
                e["login"] = True

    # 按模式生成内置配置块（只列出当前需要的项，不把账号/Cookie 全列出来）；
    # 每个需填写的变量后附注释说明；多账户可用列表或 换行/@@/&& 分隔。
    cfg = []
    if cookie_mode:
        cfg.append('BUILTIN_COOKIE = %s  # 会话 Cookie；多账户用换行/@@/&&分隔，或写列表 ["ck1","ck2"]'
                   % json.dumps(cookie_default, ensure_ascii=False))
    else:
        cfg.append('BUILTIN_ACCOUNT = ""  # 推荐：账号#密码（# 也可写 :）；多组换行/@@/&&分隔，如 "u1#p1@@u2#p2"；也可用 %s_ACCOUNT 环境变量' % tag)
        cfg.append('BUILTIN_USER = ""  # 旧写法：只填账号（BUILTIN_ACCOUNT 为空时才生效）；多账户换行/@@/&&分隔，或写列表')
        cfg.append('BUILTIN_PASS = ""  # 旧写法：登录密码，与账号一一对应（只填一个则多账号共用）')
        cfg.append('BUILTIN_FORCE_LOGIN = False  # 设为 True 强制每次账号密码登录（不读写 cookie 缓存），应对不靠 cookie 维持登录的站点；也可用 %s_FORCE_LOGIN=1 开启' % tag)
    if domain_mode:
        cfg.append('BUILTIN_DOMAIN = %s  # 站点域名，如 bbs.example.com（不带 https://）；多个按账户顺序配对'
                   % json.dumps(domain_default, ensure_ascii=False))
    cfg.append('BUILTIN_PROXY = ""  # 可选代理，如 http://127.0.0.1:7890；留空不使用')
    cfg.append('BUILTIN_DELAY = "5-10"  # 账户间隔秒数：固定(3) 或区间随机(5-10)；0=不等待')
    cfg.append('BUILTIN_TLS_IMPERSONATE = ""  # 浏览器级 TLS 指纹(拟真)：留空=标准库 urllib(零依赖)；设 "chrome"/"chrome120" 等 curl_cffi 支持值启用，规避 TLS 指纹拦截(需 pip install curl_cffi)。见生成标准 §11.1')
    cfg.append('BUILTIN_AUTO_INSTALL = True  # 依赖缺失时自动 pip install（如 curl_cffi）；设 %s_AUTO_INSTALL=0 关闭' % tag)
    cfg.append('BUILTIN_STRICT_EMPTY = True  # 汇总变量全空时判失败（防"未登录却报成功"）；站点本就不返回数据可设 %s_STRICT_EMPTY=0' % tag)
    config_block = "\n".join(cfg)

    # 按鉴权模式准备头部说明与缓存代码：
    #   - 账号模式：顶部注明“账号/密码鉴权 + cookie 自动缓存复用”，并生成缓存辅助函数 + 带缓存的 run()
    #   - Cookie 模式：顶部注明“Cookie 鉴权、不本地存储”，且【完全不生成】任何缓存代码（含 run() 也简化）
    if cookie_mode:
        envdesc = "%s_COOKIE / %s_PROXY / %s_DELAY / %s_TLS_IMPERSONATE" % (tag, tag, tag, tag)
        cachenote = "Cookie 鉴权（自带 Cookie，不本地存储；Cookie 失效请更新 BUILTIN_COOKIE / %s_COOKIE）" % tag
        cache_helpers = ""
        run_block = '''def run():
    proxy = _env("PROXY", BUILTIN_PROXY)
    # 浏览器级 TLS 指纹（可选）：BUILTIN_TLS_IMPERSONATE / <SITE_TAG>_TLS_IMPERSONATE
    # 留空=标准库 urllib（零依赖）；设 "chrome"/"chrome120" 等 curl_cffi 支持值=启用
    # 拟真 TLS，规避部分站点对 urllib 的 TLS 指纹拦截（如 RemoteDisconnected）。见标准 §11.1。
    tls = _env("TLS_IMPERSONATE", BUILTIN_TLS_IMPERSONATE)
    _cffi_requests = None
    if tls:
        if _ensure_dep("curl_cffi") is None:
            print("已启用浏览器级 TLS 指纹 impersonate=%s，但依赖 curl_cffi 不可用；"
                  "请安装： pip install curl_cffi（或设 %s_AUTO_INSTALL=1 自动安装）" % (tls, SITE_TAG))
            return 1
        from curl_cffi import requests as _cffi_requests
___ACCOUNTS_BLOCK___
    total = len(accounts)
    print("共 %d 个账户待执行" % total)
    failed = 0
    for i, acc in enumerate(accounts):
        _sleep_between(i)
        print("=" * 8 + " [%d/%d] %s " % (i + 1, total, acc["label"]) + "=" * 8)
        sess = None
        if _cffi_requests is not None:
            sess = _cffi_requests.Session(impersonate=tls)
            if proxy:
                sess.proxies.update({"http": proxy, "https": proxy})
        ok, out, ck, captured = run_one(proxy, dict(acc["ctx"]), acc["jar"], session=sess)
        print(out if out else "(无输出)")
        if not ok:
            failed += 1
    print("=" * 26)
    print("执行完毕：共 %d 个账户，成功 %d，失败 %d" % (total, total - failed, failed))
    return 1 if failed else 0'''
    else:
        envdesc = "%s_ACCOUNT / %s_DOMAIN / %s_PROXY / %s_DELAY / %s_COOKIE_CACHE / %s_FORCE_LOGIN / %s_TLS_IMPERSONATE" % (tag, tag, tag, tag, tag, tag, tag)
        cachenote = ("账号/密码鉴权：登录后把会话 Cookie（及登录步骤提取的变量）缓存到脚本同目录 "
                     "<名>.cookie_cache.json，下次直接复用、过期自动重新登录（%s_COOKIE_CACHE 可改路径）。"
                     "若站点不靠 Cookie 维持登录（纯 token / 每次需重登），设 %s_FORCE_LOGIN=1 强制每次账号密码登录、不读写缓存。" % (tag, tag))
        cache_helpers = '''def _cookie_cache_path():
    """缓存文件路径：脚本同目录 <名>.cookie_cache.json；SITE_TAG_COOKIE_CACHE 可覆盖（目录或文件）。"""
    p = _env("COOKIE_CACHE", "")
    if p:
        if p.endswith(("/", "\\\\")) or os.path.isdir(p):
            base = os.path.splitext(os.path.basename(__file__))[0] + ".cookie_cache.json"
            return os.path.join(p, base)
        return p
    return os.path.splitext(os.path.abspath(__file__))[0] + ".cookie_cache.json"


def _load_cache():
    try:
        with open(_cookie_cache_path(), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(data):
    try:
        with open(_cookie_cache_path(), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass'''
        run_block = '''def run():
    proxy = _env("PROXY", BUILTIN_PROXY)
    # 浏览器级 TLS 指纹（可选）：BUILTIN_TLS_IMPERSONATE / <SITE_TAG>_TLS_IMPERSONATE
    # 留空=标准库 urllib（零依赖）；设 "chrome"/"chrome120" 等 curl_cffi 支持值=启用
    # 拟真 TLS，规避部分站点对 urllib 的 TLS 指纹拦截（如 RemoteDisconnected）。见标准 §11.1。
    tls = _env("TLS_IMPERSONATE", BUILTIN_TLS_IMPERSONATE)
    _cffi_requests = None
    if tls:
        if _ensure_dep("curl_cffi") is None:
            print("已启用浏览器级 TLS 指纹 impersonate=%s，但依赖 curl_cffi 不可用；"
                  "请安装： pip install curl_cffi（或设 %s_AUTO_INSTALL=1 自动安装）" % (tls, SITE_TAG))
            return 1
        from curl_cffi import requests as _cffi_requests
___ACCOUNTS_BLOCK___
    total = len(accounts)
    print("共 %d 个账户待执行" % total)
    # 登录步骤提取的变量名（缓存命中时直接复用，避免跳过登录后缺失 token 等）
    login_var_names = []
    for e in ENTRIES:
        if e.get("login"):
            for v in (e.get("rule", {}) or {}).get("extract_variables", []) or []:
                if "name" in v:
                    login_var_names.append(v["name"])
    # 强制账号密码登录：用于不靠 Cookie 维持登录的站点（如纯 token / 每次需重登）。
    # 开启后不读取、不写入任何 cookie 缓存，每次都完整走登录步。
    force_login = str(_env("FORCE_LOGIN", BUILTIN_FORCE_LOGIN)).strip().lower() \\
        in ("1", "true", "yes", "y", "on")
    cache = _load_cache()
    failed = 0
    for i, acc in enumerate(accounts):
        _sleep_between(i)
        print("=" * 8 + " [%d/%d] %s " % (i + 1, total, acc["label"]) + "=" * 8)
        label = acc["label"]
        sess = None
        if _cffi_requests is not None:
            sess = _cffi_requests.Session(impersonate=tls)
            if proxy:
                sess.proxies.update({"http": proxy, "https": proxy})
        ctx = dict(acc["ctx"])
        if force_login:
            print("  [强制账号密码登录，跳过 cookie 缓存]")
        use_cache = (not force_login) and bool(
            acc["jar"] and label in cache and cache[label].get("cookie"))
        if use_cache:
            ctx["cookie"] = cache[label]["cookie"]
            ctx.update(cache[label].get("vars", {}))
            print("  [使用缓存 cookie，跳过登录]")
        ok, out, ck, captured = run_one(proxy, ctx, use_cookiejar=not use_cache,
                                        skip_login=use_cache, capture_vars=login_var_names,
                                        session=sess)
        print(out if out else "(无输出)")
        if not ok and use_cache:
            # 缓存 cookie/vars 可能已失效，清除并重跑一次（含登录自动续期）
            print("  [缓存可能已失效，正在重新登录...]")
            cache.pop(label, None)
            ctx2 = dict(acc["ctx"])
            ok, out, ck, captured = run_one(proxy, ctx2, use_cookiejar=True,
                                            skip_login=False, capture_vars=login_var_names,
                                            session=sess)
            print(out if out else "(无输出)")
        if not ok:
            failed += 1
            continue
        # 账号模式：把本次会话 cookie 与登录步骤提取的变量写回缓存（强制登录时不写）
        if acc["jar"] and ck and not force_login:
            entry = {"cookie": ck, "ts": int(time.time())}
            if captured:
                entry["vars"] = captured
            cache[label] = entry
            _save_cache(cache)
    print("=" * 26)
    print("执行完毕：共 %d 个账户，成功 %d，失败 %d" % (total, total - failed, failed))
    return 1 if failed else 0'''

    # 多账户：按模式解析成统一的 accounts 列表（每项含 ctx / jar / label）
    dom_list = ""
    dom_assign = ""
    if domain_mode:
        dom_list = '''
    domains = [_clean_domain(d) for d in _split_multi(_env("DOMAIN", BUILTIN_DOMAIN))]
    if not domains or not domains[0]:
        print("请配置环境变量 %s_DOMAIN 或在脚本顶部填写内置域名 DOMAIN" % SITE_TAG)
        return 1'''
        dom_assign = '''
        c["DOMAIN"] = domains[i] if len(domains) > 1 else domains[0]'''

    # 凭证仍是占位值/空值时直接报错（顺便打印该配哪个环境变量与格式，照抄即可）
    ph_check_cookie = '''
    _ph = [a["label"] for a in accounts if any(_is_placeholder(v) for v in a.get("secrets", []))]
    if _ph:
        print("以下账户未填写有效凭证（空值或占位值）：%s" % "、".join(str(x) for x in _ph))
        print("请配置环境变量 %s_COOKIE（会话 Cookie；多账户用换行/@@/&& 分隔）"
              "\\n  单账户例： %s_COOKIE='a=1; b=2; c=3'"
              "\\n  多账户例： %s_COOKIE='ck1=1@@ck2=2'"
              "\\n  或改脚本顶部内置常量： BUILTIN_COOKIE" % (SITE_TAG, SITE_TAG, SITE_TAG))
        return 1
'''
    ph_check_account = '''
    _ph = [a["label"] for a in accounts if any(_is_placeholder(v) for v in a.get("secrets", []))]
    if _ph:
        print("以下账户未填写有效凭证（空值或占位值）：%s" % "、".join(str(x) for x in _ph))
        print("请配置环境变量 %s_ACCOUNT（格式：账号#密码；多账号用换行/@@/&& 分隔）"
              "\\n  单账号例： %s_ACCOUNT='zhangsan#123456'"
              "\\n  多账号例： %s_ACCOUNT='zhangsan#123456@@lisi#654321'"
              "\\n  旧写法（仍兼容）： %s_USER / %s_PASS（按序配对）"
              "\\n  或改脚本顶部内置常量： BUILTIN_ACCOUNT（推荐）/ BUILTIN_USER + BUILTIN_PASS"
              % (SITE_TAG, SITE_TAG, SITE_TAG, SITE_TAG, SITE_TAG))
        return 1
'''

    if cookie_mode:
        accounts_block = '''    cookies = _split_multi(_env("COOKIE", BUILTIN_COOKIE))
    if not cookies:
        print("请配置环境变量 %s_COOKIE 或在脚本顶部填写内置 Cookie（多个用换行/@@/&& 分隔）" % SITE_TAG)
        return 1
''' + dom_list + '''
    accounts = []
    for i, ck in enumerate(cookies):
        c = {"cookie": ck}
''' + dom_assign + '''
        accounts.append({"ctx": c, "jar": False, "secrets": [ck],
                         "label": (ck[:10] + "...") if len(ck) > 10 else ck})''' + ph_check_cookie
    else:
        accounts_block = '''    # 账号密码：推荐用单变量 <SITE_TAG>_ACCOUNT（"账号#密码"，多组换行/@@/&& 分隔）；
    # 未配置时回退旧写法 <SITE_TAG>_USER / <SITE_TAG>_PASS（按序配对，密码只填一个则多账号共用）
    pairs = _split_pairs(_env("ACCOUNT", BUILTIN_ACCOUNT))
    if not pairs:
        users = _split_multi(_env("USER", BUILTIN_USER))
        passes = _split_multi(_env("PASS", BUILTIN_PASS))
        if users and passes:
            if len(passes) == 1 and len(users) > 1:
                passes = passes * len(users)      # 多账号共用一个密码
            if len(users) == len(passes):
                pairs = list(zip(users, passes))
    if not pairs:
        print("请配置环境变量 %s_ACCOUNT（格式：账号#密码，多组用换行/@@/&& 分隔）"
              " 或脚本顶部 BUILTIN_ACCOUNT；旧写法 %s_USER/%s_PASS 仍兼容" % (SITE_TAG, SITE_TAG, SITE_TAG))
        return 1
    # 账号模式下模板若仍用到 {{cookie}}，可在 SITE_TAG_COOKIE 提供（多个则按顺序配对）
    cookies = _split_multi(_env("COOKIE", globals().get("BUILTIN_COOKIE", "")))
''' + dom_list + '''
    accounts = []
    for i, (u, p) in enumerate(pairs):
        # 兼容 QD 模板常见的变量名写法：{{user}}/{{username}}、{{password}}/{{pwd}}
        c = {"username": u, "user": u, "name": u,
             "password": p, "pwd": p, "pass": p}
        if cookies:
            c["cookie"] = cookies[i] if len(cookies) == len(pairs) else cookies[0]
''' + dom_assign + '''
        accounts.append({"ctx": c, "jar": True, "secrets": [u, p], "label": u})''' + ph_check_account

    # 输出文件名按鉴权模式自动加后缀：账号模式 _账号.py、Cookie 模式 _Cookie.py。
    # 这样从文件名就能看出该脚本要配账号密码还是 Cookie（见生成标准 §1）。
    out = args.output or (_out_name(args.input, cookie_mode))
    code = (TEMPLATE
            .replace("___BASE___", json.dumps(base, ensure_ascii=False))
            .replace("___ENTRIES___", pprint.pformat(entries, width=200, sort_dicts=False))
            .replace("___CONFIG___", config_block)
            .replace("___ENVDESC___", envdesc)
            .replace("___TAG___", tag)
            .replace("___CACHENOTE___", cachenote)
            .replace("___CACHE_HELPERS___", cache_helpers)
            .replace("___RUN___", run_block)
            .replace("___ALLOW_CROSS___", "False" if args.no_cross else "True")
            .replace("___ACCOUNTS_BLOCK___", accounts_block)
            .replace("__FILE__", os.path.basename(out)))

    # 重生时保留旧文件的 BUILTIN_* 配置（账号密码 / Cookie 不被占位值冲掉）；
    # 指定 -f/--force 时跳过保留，直接按模板占位值覆盖。
    # 旧文件可能是老命名（<stem>_sign.py）或另一种模式后缀（模式识别变了），
    # 此时按"重命名迁移"处理：读旧文件、沿用其配置，写到新文件名下。
    old_src = out
    if not os.path.exists(out):
        stem = os.path.splitext(args.input)[0]
        for cand in (stem + "_sign.py", _out_name(args.input, not cookie_mode)):
            if os.path.exists(cand) and cand != out:
                old_src = cand
                print("检测到旧命名文件：%s → 沿用其配置并输出到 %s" % (cand, out))
                break
    if os.path.exists(old_src) and not args.force:
        with open(old_src, encoding="utf-8") as f:
            old = f.read()
        for var in ("BUILTIN_ACCOUNT", "BUILTIN_USER", "BUILTIN_PASS", "BUILTIN_PROXY", "BUILTIN_COOKIE", "BUILTIN_DOMAIN", "BUILTIN_DELAY", "BUILTIN_FORCE_LOGIN", "SITE_TAG"):
            m_old = re.search(r'^%s\s*=\s*(.*?)\s*(?:#.*)?$' % var, old, re.M)
            if not m_old:
                continue
            old_val = m_old.group(1)
            # 沿用新生成的注释，确保“需填写的变量”始终带说明
            m_new = re.search(r'^%s\s*=\s*.*?(#.*)$' % var, code, re.M)
            comment = m_new.group(1) if m_new else ""
            new_line = ("%s = %s  %s" % (var, old_val, comment)) if comment else ("%s = %s" % (var, old_val))
            code = re.sub(r'^%s\s*=\s*.*$' % var, new_line, code, flags=re.M)

    with open(out, "w", encoding="utf-8") as f:
        f.write(code)

    print("已生成:", out)
    print("  BASE:", base)
    print("  鉴权模式:", "Cookie" if cookie_mode else "账号密码")
    print("  步骤数:", len(entries))
    # 生成后自动登记到导入列表（已有条目覆盖 cron，列表文件缺失则跳过）
    if not args.no_import:
        slot_range = _parse_slot_range(args.slot_range or os.environ.get("QD_SLOT_RANGE", ""))
        import_to_tasks(out, args.cron, args.tasks, slot_range)
        print("  提示：运行 python tools/生成定时头.py 可把 cron 写进脚本头部注释")
    for e in entries:
        print("   -", e.get("comment", "?"), e["request"]["method"],
              e["request"]["url"].split("?")[0])


if __name__ == "__main__":
    main()
