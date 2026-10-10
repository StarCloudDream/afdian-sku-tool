#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
爱发电 SKU 查询器
=================
粘一个爱发电的商品地址进去，告诉你它下面的 plan_id 和 sku_id。

爱发电没有单独的 goods_id，"商品"在它那儿就是 plan（方案），型号才叫 sku。
尴尬的是这两个 id 在页面上都看不见，只能从这个接口捞：

    GET https://afdian.com/api/creator/get-plan-skus?plan_id=<地址里那段>&is_ext=

接口是公开的，不用登录也不用 token（商品页自己就在调它）。
麻烦的是浏览器里直接请求会被跨域挡住，所以这里起了个只监听 127.0.0.1 的小服务，
由它去代替页面请求，再把结果递回窗口。

界面皮是从 CozyUI+ 材质包里裁的（九宫格怎么切的记在 app_assets/manifest.json）。

用法
----
    python afdian_sku_app.py               直接开窗口
    python afdian_sku_app.py --no-window   只起服务不开窗口（调界面的时候方便）
    python afdian_sku_app.py --browser     用系统浏览器打开（没有 WebView2 时自己会退到这里）
    python afdian_sku_app.py --framed      用系统原生边框（无边框模式出问题时的备胎）
    python afdian_sku_app.py --selftest    跑几条用例就退出，不开窗口

打包
----
    python build.py
（同目录的 build.py，只会把真正用到的素材和模块挑进去。）
"""
import json
import os
import re
import socket
import sys
import threading
import time
import urllib.parse
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

APP_TITLE = "爱发电 SKU 查询器"
APP_VER = "1.0"

# 界面上"赞助一下"和版本号点了往哪跳
LINK_AFDIAN = "https://afdian.com/a/StarCloudDream?utm_source=copylink&utm_medium=link"
LINK_GITHUB = "https://github.com/StarCloudDream"
# 能打开的外链白名单，别的一律拒绝（页面万一被人改过也开不出去）
LINK_HOSTS = ("afdian.com", "github.com")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

HEX32 = re.compile(r"^[0-9a-f]{32}$", re.I)
# 从一长串字符里找 id 得用这个不带 ^$ 的版本。
# 上面那个带了首尾锚点，对它调 findall() 永远返回空，兜底搜索会变成一段死代码。
HEX32_FIND = re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{32}(?![0-9a-fA-F])")

API = "https://afdian.com/api/creator/get-plan-skus?plan_id={pid}&is_ext="
DEFAULT_PORT = 8765
IDLE_LIMIT = 150.0          # 心跳断这么久就自己退掉（秒）

WIN_W = 816                 # 窗口宽度，跟着界面素材的尺寸走
WIN_H = 289                 # 初始高度（矮横幅那版）
WIN_TITLE = APP_TITLE + "  v" + APP_VER

# 从进程起来就开始计时，打包版解包花的时间也算在里面
T0 = time.perf_counter()

MIME = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
        ".js": "application/javascript; charset=utf-8", ".png": "image/png",
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
        ".svg": "image/svg+xml", ".json": "application/json; charset=utf-8",
        ".ico": "image/x-icon"}

FROZEN = bool(getattr(sys, "frozen", False))
BASE_DIR = os.path.dirname(os.path.abspath(sys.executable if FROZEN else __file__))
LOG_PATH = os.path.join(BASE_DIR, "sku_tool.log")

_last_ping = [0.0]
_seen_ping = [False]
_win = [None]
_srv = [None]


class _Null:
    """双击运行的那种打包版是没有控制台的，sys.stdout 直接就是 None，print 会当场炸掉"""

    def write(self, *_a):
        pass

    def flush(self, *_a):
        pass


for _name in ("stdout", "stderr"):
    if getattr(sys, _name, None) is None:
        setattr(sys, _name, _Null())

# 同上：输出被重定向时按 UTF-8 走，否则 --selftest 的中文结果在一堆环境下都印不出来
for _name in ("stdout", "stderr"):
    _stream = getattr(sys, _name, None)
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")


def log(msg):
    """每行前面都挂着"距进程启动多少毫秒"，启动慢在哪一段一目了然"""
    line = "[{} +{:5.0f}ms] {}".format(time.strftime("%H:%M:%S"),
                                       (time.perf_counter() - T0) * 1000, msg)
    try:
        print(line, flush=True)
    except Exception:
        pass
    if FROZEN:
        # 打包版看不到控制台，落个文件方便回头查
        try:
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass


def res_path(rel):
    """打包之后资源被解到 _MEIPASS，没打包时就挨着脚本放"""
    base = getattr(sys, "_MEIPASS", None)
    return os.path.join(base, rel) if base else os.path.join(BASE_DIR, rel)


def free_port(start=DEFAULT_PORT, span=50):
    for port in range(start, start + span):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return None


def msgbox(text, title=APP_TITLE, icon=0x40):
    """弹个系统提示。三个系统各玩各的，凑不到一起，就分开写。

    注意这个只在"窗口根本起不来"的时候用得上，所以别指望它多好看。
    """
    log(f"[提示] {text}")
    if IS_WIN:
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, text, title, icon)
        except Exception:
            pass
    elif IS_MAC:
        try:
            import subprocess
            subprocess.run(["osascript", "-e",
                            'display dialog "{}" with title "{}" buttons {{"知道了"}}'
                            .format(text.replace('"', "'"), title)],
                           capture_output=True, timeout=30)
        except Exception:
            pass
    # Linux 上弹窗工具不一定装了，上面的 log 已经写到日志里，够查了


# ---------------------------------------------------------------- 去掉 OpenSSL
def _no_tls(*_a, **_k):
    """空壳 ssl 身上的方法。真被调到说明有代码想加密通信了，这种情况直接报错清楚些。"""
    raise RuntimeError("这个精简版没带 TLS：外网请求交给系统处理，页面也由本地服务提供，"
                       "正常走不到这里")


def strip_openssl():
    """给 pywebview 换个 ssl 空壳，好让 OpenSSL 彻底不用进包。

    pywebview/http.py 开头就 import ssl，webview/__init__.py 又把 http 导进来了，
    所以只要碰一下 webview，ssl 就被拽起来 —— 连带 libcrypto 和 libssl 两个大块头
    （解开后加起来 9MB 出头）一起躺进 exe 里，每次启动都要多解压这么久。

    可我们压根用不上：pywebview 那边唯一碰 ssl 的地方，是"用它自带的那套服务器跑
    HTTPS"那个分支，而我们的页面是本文件里自己的服务端在供，外网请求走 WinHTTP。
    也就是说那个分支永远不会执行，只要让 import 过得去就行。

    只在 Windows 上换。别的平台本来就用标准库发请求，需要真的 ssl。
    """
    if not IS_WIN or "ssl" in sys.modules:
        return
    import types
    stub = types.ModuleType("ssl")
    stub.SSLContext = _no_tls
    stub.create_default_context = _no_tls
    stub.wrap_socket = _no_tls
    stub.SSLError = OSError
    sys.modules["ssl"] = stub


strip_openssl()             # 必须赶在任何 import webview 之前，不然白搭


# ---------------------------------------------------------------- 发请求
class HttpStatusError(Exception):
    """对方回的 HTTP 状态码不是 200"""

    def __init__(self, code):
        super().__init__("HTTP {}".format(code))
        self.code = code


class PlanNotFound(Exception):
    """爱发电说这个 plan_id 不存在。单独分出来是因为：换个候选 id 说不定就有了，
    所以这对调用方来说不算"失败"，只是"这个不对，下一个"。"""


if IS_WIN:
    # Windows 上走系统的 WinHTTP。好处是它用系统自带的 TLS，不用往包里塞 OpenSSL，
    # 证书和代理设置也自动跟着系统走，用户配了代理我们什么都不用做。
    WH_DEFAULT_PROXY = 0
    WH_FLAG_SECURE = 0x00800000
    WH_ADDREQ_FLAG_ADD = 0x20000000
    WH_QUERY_STATUS_CODE = 19
    WH_QUERY_FLAG_NUMBER = 0x20000000
    _wh_cache = [None]

    def _winhttp():
        """把 winhttp.dll 用到的函数签名配好，配一次就够了。

        两个地方不注意会翻车：
        返回句柄的函数必须显式写 restype = HANDLE。HINTERNET 是 64 位的，
                                不写的话 ctypes 按 c_int 收，句柄当场被截断。
        字符串参数得声明成 LPCWSTR，不然按 ANSI 传进去就是一堆乱码。
        """
        if _wh_cache[0] is not None:
            return _wh_cache[0]
        import ctypes
        from ctypes import wintypes
        wh = ctypes.WinDLL("winhttp")
        H = wintypes.HANDLE

        wh.WinHttpOpen.restype = H
        wh.WinHttpOpen.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                                   wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
        wh.WinHttpConnect.restype = H
        wh.WinHttpConnect.argtypes = [H, wintypes.LPCWSTR, wintypes.WORD, wintypes.DWORD]
        wh.WinHttpOpenRequest.restype = H
        wh.WinHttpOpenRequest.argtypes = [H, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                          wintypes.LPCWSTR, wintypes.LPCWSTR,
                                          ctypes.POINTER(wintypes.LPCWSTR), wintypes.DWORD]
        wh.WinHttpSetTimeouts.argtypes = [H, ctypes.c_int, ctypes.c_int,
                                          ctypes.c_int, ctypes.c_int]
        wh.WinHttpAddRequestHeaders.argtypes = [H, wintypes.LPCWSTR, wintypes.DWORD,
                                                wintypes.DWORD]
        wh.WinHttpSendRequest.argtypes = [H, wintypes.LPCWSTR, wintypes.DWORD,
                                          ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                          ctypes.c_void_p]
        wh.WinHttpReceiveResponse.argtypes = [H, ctypes.c_void_p]
        wh.WinHttpQueryHeaders.argtypes = [H, wintypes.DWORD, wintypes.LPCWSTR,
                                           ctypes.c_void_p,
                                           ctypes.POINTER(wintypes.DWORD),
                                           ctypes.POINTER(wintypes.DWORD)]
        wh.WinHttpReadData.argtypes = [H, ctypes.c_void_p, wintypes.DWORD,
                                       ctypes.POINTER(wintypes.DWORD)]
        wh.WinHttpCloseHandle.argtypes = [H]
        _wh_cache[0] = wh
        return wh

    def http_get(url, timeout=20):
        import ctypes
        from ctypes import wintypes
        wh = _winhttp()
        u = urllib.parse.urlsplit(url)
        if u.scheme not in ("http", "https") or not u.hostname:
            raise ValueError("不支持的地址：" + url)
        secure = u.scheme == "https"
        port = u.port or (443 if secure else 80)
        path = u.path or "/"
        if u.query:
            path += "?" + u.query
        ms = int(timeout * 1000)

        hs = hc = hr = None
        try:
            hs = wh.WinHttpOpen(UA, WH_DEFAULT_PROXY, None, None, 0)
            if not hs:
                raise OSError("WinHttpOpen 失败")
            wh.WinHttpSetTimeouts(hs, ms, ms, ms, ms)
            hc = wh.WinHttpConnect(hs, str(u.hostname), port, 0)
            if not hc:
                raise OSError("连不上 {}:{}".format(u.hostname, port))
            hr = wh.WinHttpOpenRequest(hc, "GET", path, None, None, None,
                                       WH_FLAG_SECURE if secure else 0)
            if not hr:
                raise OSError("WinHttpOpenRequest 失败")
            wh.WinHttpAddRequestHeaders(hr,
                                        "Referer: https://afdian.com/\r\n"
                                        "Accept: application/json, text/plain, */*\r\n",
                                        -1, WH_ADDREQ_FLAG_ADD)
            if not wh.WinHttpSendRequest(hr, None, 0, None, 0, 0, None):
                raise OSError("发送请求失败")
            if not wh.WinHttpReceiveResponse(hr, None):
                raise OSError("接收响应失败")

            code = wintypes.DWORD()
            size = wintypes.DWORD(ctypes.sizeof(code))
            wh.WinHttpQueryHeaders(hr, WH_QUERY_STATUS_CODE | WH_QUERY_FLAG_NUMBER,
                                   None, ctypes.byref(code), ctypes.byref(size), None)

            buf = ctypes.create_string_buffer(65536)
            chunks = []
            while True:
                got = wintypes.DWORD()
                if not wh.WinHttpReadData(hr, buf, len(buf), ctypes.byref(got)):
                    break
                if not got.value:
                    break
                chunks.append(buf.raw[:got.value])
            body = b"".join(chunks)
            if code.value != 200:
                raise HttpStatusError(code.value)
            return body.decode("utf-8", "replace")
        finally:
            for handle in (hr, hc, hs):
                if handle:
                    try:
                        wh.WinHttpCloseHandle(handle)
                    except Exception:
                        pass
else:
    def http_get(url, timeout=20):
        """macOS / Linux 上就老老实实用标准库，反正这俩平台本来就有一堆依赖要带"""
        import urllib.error
        import urllib.request
        req = urllib.request.Request(url, headers={
            "User-Agent": UA, "Referer": "https://afdian.com/",
            "Accept": "application/json, text/plain, */*"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            raise HttpStatusError(e.code) from None


def fetch_plan(plan_id, timeout=20):
    """问接口要某个方案的信息，返回 (方案, 型号列表)"""
    d = json.loads(http_get(API.format(pid=plan_id), timeout=timeout))
    if d.get("ec") != 200:
        if d.get("ec") == 404:
            # 404 单独抛，因为换个候选 id 说不定就有，调用方还要接着试
            raise PlanNotFound("ec=404 em={}".format(d.get("em")))
        raise ValueError("接口返回 ec={} em={}".format(d.get("ec"), d.get("em")))
    data = d.get("data") or {}
    return (data.get("plan") or {}), (data.get("list") or [])


# ---------------------------------------------------------------- 认 id
def extract_plan_id(text):
    """从地址（或者光秃秃一个 id）里把 plan_id 抠出来。

    返回 (主 id, 来源说明, 备用 id 列表)。

    一个地址里冒出来的 32 位 id 可能有好几个：创作者的 user_id、订单号、型号 sku_id……
    分不清哪个是我们要的，那就全收着，主 id 查不到就顺着往下试，
    总比"抠错一个就报 404"让人干瞪眼强。
    """
    t = (text or "").strip()
    if not t:
        raise ValueError("空输入")
    if HEX32.match(t):
        return t, "直接给了 32 位 id", []

    if "://" not in t:
        t = "https://" + t
    u = urllib.parse.urlparse(t)
    q = urllib.parse.parse_qs(u.query)
    cand = []

    def push(value, how):
        if HEX32.match(value) and value not in [c[0] for c in cand]:
            cand.append((value, how))

    # 地址参数里点名写了 plan_id 的，最可信
    for key in ("plan_id", "id"):
        for value in q.get(key, []):
            push(value, "取自 URL 参数 {}".format(key))

    m = re.match(r"^/item/([0-9a-f]{32})/?$", u.path, re.I)
    if m:
        push(m.group(1), "取自 /item/<id> 路径")

    if not cand:
        # 什么都没认出来，先把"肯定不是商品页"的几种排掉，免得报错报得莫名其妙
        if re.match(r"^/a/", u.path):
            raise ValueError("这是创作者主页，地址里没有 id。要到具体的档位或商品上"
                             "点「右键 → 复制链接地址」，或者点「给 TA 发电」看地址栏。")
        if re.match(r"^/p/", u.path):
            raise ValueError("这是动态文章的地址，不是商品。要指向文章里挂的那个方案"
                             "（右键「给 TA 发电」→ 复制链接）。")
        for value in HEX32_FIND.findall(u.path + "/" + u.query):
            push(value, "从地址里找到的 32 位 id（路径没认出来，当 plan_id 试）")

    # 剩下的 32 位 id 全留着当备选
    for value in HEX32_FIND.findall(u.path + "/" + u.query):
        push(value, "备选：地址里的其他 32 位 id")

    if cand:
        return cand[0][0], cand[0][1], cand[1:]
    raise ValueError("这个地址里找不到 32 位 id，确认下是不是 /item/... 或者 "
                     "order/create?plan_id=... 这种形式。")


def resolve(text):
    """把一条输入跑完整流程，返回给界面用的结果字典"""
    out = {"input": (text or "").strip()}
    try:
        pid, how, alts = extract_plan_id(text)
    except ValueError as e:
        out.update(ok=False, error=str(e))
        return out

    tried = []
    plan = skus = None
    for cand, note in [(pid, how)] + list(alts):
        try:
            plan, skus = fetch_plan(cand)
        except PlanNotFound:
            tried.append("{}（{}）".format(cand, note))
            continue
        except HttpStatusError as e:
            out.update(ok=False, error="HTTP {}（接口拒绝或者网络不对劲）".format(e.code))
            return out
        except ValueError as e:
            out.update(ok=False, error=str(e))
            return out
        except Exception as e:
            out.update(ok=False, error="{}: {}".format(type(e).__name__, e))
            return out
        out["plan_id"] = cand
        out["source"] = note if cand == pid else note + "（主 id 查不到，自动换了这个）"
        break
    else:
        out.update(ok=False, error=(
            "爱发电说查不到这个方案（ec=404）。\n"
            "试过的 id：" + "、".join(tried) + "\n"
            "常见原因：① 商品删了或者下架了；② 贴进来的是 sku_id 不是 plan_id；"
            "③ 地址被截断了 —— 到商品页点「右键 → 复制链接地址」最稳。"))
        return out

    out.update(ok=True, plan={
        "name": plan.get("name"),
        "price": plan.get("show_price") or plan.get("price"),
        "user_id": plan.get("user_id"),
        "status": plan.get("status"),
    }, skus=[{"sku_id": s.get("sku_id"), "name": s.get("name"),
              "price": s.get("price"), "status": s.get("status")} for s in skus])
    return out


# ---------------------------------------------------------------- 本地服务
class Handler(BaseHTTPRequestHandler):
    server_version = "AfdianSkuApp"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass                      # 默认那个挨个请求打一行，太吵了

    def _send(self, code, body, ctype):
        b = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        # 别让浏览器（WebView2 也一样）缓存住，不然改了界面还是看到旧的
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(b)
        except BrokenPipeError:
            pass

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False),
                   "application/json; charset=utf-8")

    def _file(self, rel):
        p = res_path(rel)
        ext = os.path.splitext(p)[1].lower()
        if not os.path.isfile(p) or ext not in MIME:
            return self._json(404, {"ok": False, "error": "not found: " + rel})
        with open(p, "rb") as f:
            self._send(200, f.read(), MIME[ext])

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(u.path)

        if path in ("/", "/index.html"):
            return self._file("ui.html")
        if path in ("/favicon.ico", "/apple-touch-icon.png", "/favicon.png"):
            # 浏览器一定会来要图标，回 204 别回 404，不然控制台全是红字
            return self._send(204, b"", "image/x-icon")
        if path.startswith("/app_assets/"):
            rel = path.lstrip("/")
            if ".." in rel:
                return self._json(403, {"ok": False, "error": "denied"})
            return self._file(rel)
        if path == "/api/ping":
            _last_ping[0] = time.time()
            _seen_ping[0] = True
            return self._json(200, {"ok": True})
        if path == "/api/quit":
            self._json(200, {"ok": True})
            log("窗口那边点了退出")
            threading.Thread(target=shutdown_all, daemon=True).start()
            return
        if path == "/api/resolve":
            items = urllib.parse.parse_qs(u.query).get("url", [])
            if not items:
                return self._json(400, {"ok": False, "error": "缺少 url 参数"})
            with ThreadPoolExecutor(max_workers=5) as pool:
                results = list(pool.map(resolve, items))
            log("查了 {} 条，成功 {} 条".format(
                len(items), sum(1 for r in results if r["ok"])))
            return self._json(200, {"ok": True, "results": results})

        # 记一笔，以后日志里出现莫名其妙的 404 能直接看出是哪个路径
        log("没见过的请求路径：{} → 404".format(path))
        return self._json(404, {"ok": False, "error": "not found"})


def shutdown_all():
    """退出前收摊：先关窗口，再停服务"""
    try:
        if _win[0] is not None:
            _win[0].destroy()
    except Exception:
        pass
    try:
        if _srv[0] is not None:
            _srv[0].shutdown()
    except Exception:
        pass


def watchdog():
    """页面每 5 秒来一次心跳。连着 150 秒没动静，说明窗口早关了，自己退掉别赖着。"""
    while True:
        time.sleep(5)
        if _seen_ping[0] and (time.time() - _last_ping[0]) > IDLE_LIMIT:
            log("窗口关了超过 {} 秒，自动退出".format(int(IDLE_LIMIT)))
            threading.Thread(target=shutdown_all, daemon=True).start()
            return


# ---------------------------------------------------------------- 窗口位置
def screen_center_xy(width=WIN_W, height=WIN_H):
    """算出窗口摆在主屏正中的坐标，算不出来就返回 None。

    为什么不直接用 pywebview 的居中：它那个 CenterScreen（WinForms 的）在带缩放
    的屏幕上不准，试下来它把窗口丢在 (104,104)、(208,208) 这种左上角，而且每次还不一样，
    干脆自己算。取的是"工作区"（屏幕减去任务栏那块），所以分辨率变了、任务栏挪到
    上边或左边，都不用改代码。
    """
    if not IS_WIN:
        return None
    try:
        import ctypes
        from ctypes import wintypes
        r = wintypes.RECT()
        # SPI_GETWORKAREA = 0x0030
        if not ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0):
            return None
        aw, ah = r.right - r.left, r.bottom - r.top
        if aw <= 0 or ah <= 0:
            return None
        return (r.left + max(0, (aw - width) // 2),
                r.top + max(0, (ah - height) // 2))
    except Exception:
        return None


def center_window(title):
    """把窗口挪到屏幕正中。按窗口当前的真实尺寸算，被系统改过大小也算得对。"""
    if not IS_WIN:
        return False
    try:
        import ctypes
        from ctypes import wintypes
        u32 = ctypes.windll.user32
        hwnd = u32.FindWindowW(None, title)
        if not hwnd:
            return False
        r = wintypes.RECT()
        u32.GetWindowRect(hwnd, ctypes.byref(r))
        xy = screen_center_xy(r.right - r.left, r.bottom - r.top)
        if xy is None:
            return False
        SWP_NOSIZE, SWP_NOZORDER, SWP_NOACTIVATE = 0x0001, 0x0004, 0x0010
        u32.SetWindowPos(hwnd, 0, xy[0], xy[1], 0, 0,
                         SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
        log("窗口居中到 ({},{})，尺寸 {}x{}".format(
            xy[0], xy[1], r.right - r.left, r.bottom - r.top))
        return True
    except Exception as e:
        log("窗口居中失败（不影响用）：{}".format(e))
        return False


def polish_window(title, win=None, delay=1.2):
    """窗口出来之后再收拾三件事：尺寸补正、居中、Win11 圆角"""
    if not IS_WIN:
        return
    if win is not None:
        # pywebview 建窗时给的尺寸会被吃掉一点（要 816x578 最后得到 800x539），补一次
        try:
            time.sleep(0.5)
            win.resize(WIN_W, WIN_H)
            log("窗口尺寸补正为 {}x{}".format(WIN_W, WIN_H))
        except Exception as e:
            log("窗口尺寸补正失败（不影响用）：{}".format(e))

    # 尺寸定下来了再居中，不然按旧尺寸算会偏几个像素
    center_window(title)
    time.sleep(delay)

    try:
        import ctypes
        from ctypes import wintypes
        u32 = ctypes.windll.user32
        hwnd = u32.FindWindowW(None, title)
        if not hwnd:
            log("DWM 圆角：没找到窗口句柄，跳过")
            return
        # 33 = DWMWA_WINDOW_CORNER_PREFERENCE，2 = ROUND
        pref = ctypes.c_int(2)
        rc = ctypes.windll.dwmapi.DwmSetWindowAttribute(
            wintypes.HWND(hwnd), ctypes.c_uint(33), ctypes.byref(pref),
            ctypes.sizeof(pref))
        log("DWM 圆角返回 {}（0 是成功）".format(rc))
    except Exception as e:
        log("DWM 圆角设置失败（不影响用）：{}".format(e))


# ---------------------------------------------------------------- 给页面用的小接口
class Api:
    """无边框窗口得自己做标题栏按钮，这几个方法就是给那两个按钮用的"""

    def minimize(self):
        try:
            _win[0].minimize()
            return True
        except Exception:
            return False

    def resize(self, height=None, width=None):
        """页面要改窗口高度（出结果时会拉高一点，输入框变多行时也会）"""
        try:
            w = _win[0]
            if w is None:
                return False
            h = int(height) if height else WIN_H
            # 下限得比 WIN_H 小，不然 289 会被顶上去，窗口就压不下来了
            h = max(240, min(h, 1800))
            w.resize(int(width) if width else WIN_W, h)
            return True
        except Exception:
            return False

    def open_url(self, url):
        """页面点"赞助一下"和版本号时走这里。只放行白名单里的域名，非 http(s) 一律拒。"""
        try:
            u = urllib.parse.urlparse(str(url or ""))
            if u.scheme not in ("http", "https"):
                return False
            host = (u.hostname or "").lower()
            if not any(host == h or host.endswith("." + h) for h in LINK_HOSTS):
                log("open_url 拒绝了这个不在白名单里的地址：{}".format(host))
                return False
            webbrowser.open(url)
            log("用浏览器打开：{}".format(url))
            return True
        except Exception:
            return False


def open_window(url, force_browser=False, framed=False):
    """开原生窗口。开不成的话退到系统浏览器 —— 反正界面就是网页，哪儿都能跑。"""
    if force_browser:
        webbrowser.open(url + "?frameless=0")
        return None
    try:
        import webview
    except Exception as e:
        log("没装上 pywebview（{}），改用系统浏览器".format(e))
        webbrowser.open(url)
        return None

    # min_size 必须 <= (WIN_W, WIN_H)，不然 289 会被顶回 500
    kw = dict(width=WIN_W, height=WIN_H, min_size=(620, 260),
              background_color="#15171b", text_select=True)

    # 一建出来就摆正中，免得先闪在左上角、过一秒再被挪过去，看着很别扭。
    # 后面 polish_window 还会按真实尺寸再校正一次，双保险。
    xy = screen_center_xy()
    if xy:
        kw.update(x=xy[0], y=xy[1])
        log("窗口初始位置：屏幕工作区正中 ({},{})".format(xy[0], xy[1]))

    if framed:
        log("窗口模式：系统原生边框")
    elif IS_WIN:
        # Windows 上用无边框，形状交给系统 DWM（它自己会给 8px 圆角）。
        # 试过 transparent=True 想做出真圆角，结果在这台机器上没生效（窗口没有
        # WS_EX_LAYERED），"透明"的地方露出的是 WinForms 的灰底 #F0F0F0，四个角
        # 反而多出一圈浅灰。所以页面自己铺满不透明底色，圆角交给 DWM 就行。
        kw.update(frameless=True, easy_drag=False, transparent=True)
        url = url + "?frameless=1"
        log("窗口模式：无边框（圆角由系统提供）")
    elif IS_MAC:
        # macOS 的 Cocoa 做无边框比较靠谱，拖动靠页面里的拖动区
        kw.update(frameless=True, easy_drag=False)
        url = url + "?frameless=1"
        log("窗口模式：无边框（macOS）")
    else:
        # Linux 上各家窗口管理器脾气不一样，无边框很容易出现拖不动、关不掉的怪事，
        # 老老实实用系统边框，至少一定能操作
        log("窗口模式：系统原生边框（Linux 上无边框容易出幺蛾子）")

    try:
        w = webview.create_window(WIN_TITLE, url, js_api=Api(), **kw)
        _win[0] = w
        # 窗口真的画出来才算启动完成，拿这个当耗时终点
        try:
            w.events.shown += lambda *_: log("★ 窗口已显示（启动完成）")
        except Exception:
            pass
        log("窗口对象建好了，开始加载 WebView2…")
        threading.Thread(target=polish_window,
                         args=(WIN_TITLE, w), daemon=True).start()
        webview.start()
        return w
    except Exception as e:
        msg = str(e)
        log("webview 起不来：{}: {} → 改用系统浏览器".format(type(e).__name__, msg))
        # 最常见的就是系统里没装 WebView2 运行时（老系统或者精简过的 Win10）
        if "webview2" in msg.lower() or "runtime" in msg.lower() or "edge" in msg.lower():
            msgbox("这台机器缺 Microsoft Edge WebView2 运行时，开不了窗口。\n"
                   "已经改用默认浏览器打开同样的界面了。\n\n"
                   "想要窗口版的话，去微软官网装一下『WebView2 Runtime』就行。")
        try:
            webbrowser.open(url)
        except Exception:
            msgbox("窗口和浏览器都打不开。\n手动访问这个地址试试：{}".format(url), icon=0x10)
        return None


def selftest():
    """自检。打包版没有控制台，所以结果既 print 也写日志。"""
    out = []

    def say(s):
        out.append(s)
        log("SELFTEST " + s)

    # 素材有没有被打进去：直接扫 ui.html 里引用的文件名，不去读 manifest.json。
    # 少一个环节就少一个出问题的点，顺便能发现"新加了图但忘了登记清单"这种事。
    need = ["ui.html"]
    try:
        html = open(res_path("ui.html"), encoding="utf-8").read()
        need += sorted(set(re.findall(
            r'(app_assets/[A-Za-z0-9_.\-]+\.(?:png|jpg|jpeg|webp|svg|gif))', html)))
    except Exception as e:
        say("读 ui.html 失败：{}".format(e))
    if os.path.isfile(res_path("app_assets/manifest.json")):
        need.append("app_assets/manifest.json")
    miss = [n for n in need if not os.path.isfile(res_path(n))]
    say("素材检查: {}/{} 就位{}".format(
        len(need) - len(miss), len(need), "  缺={}".format(miss) if miss else ""))
    say("运行模式: {}   BASE_DIR={}".format("frozen" if FROZEN else "source", BASE_DIR))

    cases = [
        "https://afdian.com/item/5b812044146d11f080825254001e7c00",
        "https://afdian.com/order/create?plan_id=b9746b5a146a11f086b052540025c377&product_type=0",
        "5b812044146d11f080825254001e7c00",
        "https://afdian.com/a/someone",
        "deadbeefdeadbeefdeadbeefdeadbeef",
    ]
    for c in cases:
        r = resolve(c)
        if r.get("ok"):
            sku = [s["sku_id"] for s in r["skus"]] or "(没型号)"
            say("OK   {:<56} plan={} sku={}".format(c[:56], r["plan_id"], sku))
        else:
            say("ERR  {:<56} {}".format(c[:56], r["error"][:56]))
    say("结果 {} 条用例跑完".format(len(out) - 2))
    try:
        print("\n".join(out), flush=True)
    except Exception:
        pass
    return 0


def main():
    argv = sys.argv[1:]
    if "--selftest" in argv:
        return selftest()

    port = free_port()
    if port is None:
        msgbox("8765 到 8814 都被占用了，起不来。", icon=0x10)
        log("没端口了，退出")
        return 1

    url = "http://127.0.0.1:{}/".format(port)
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    _srv[0] = srv
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    threading.Thread(target=watchdog, daemon=True).start()
    log("服务已启动：{}".format(url))

    if "--no-window" in argv:
        print("\n  {} v{}\n  界面地址：{}\n  Ctrl+C 停\n".format(APP_TITLE, APP_VER, url),
              flush=True)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        return 0

    open_window(url, force_browser="--browser" in argv, framed="--framed" in argv)
    log("窗口关了，退出")
    shutdown_all()
    return 0


if __name__ == "__main__":
    sys.exit(main())
