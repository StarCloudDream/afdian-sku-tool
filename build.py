# -*- coding: utf-8 -*-
"""打包脚本
==========
一条命令出对应平台的成品，Windows 出 exe、macOS 出 .app、Linux 出单个可执行文件。
（PyInstaller 不支持交叉编译，要哪个平台的就得在哪个平台上跑。）

做的事就三件：

1. 只把 ui.html 真正引用到的素材挑出来复制到 _bundle/ 再打进去。
   app_assets 里还留着一些没用上的历史图，全塞进去白胖几百 KB。
2. 加一批用不上的模块排除项，顺便给 Windows 开上 OpenSSL 的"免装"开关。
3. 打完自己跑一遍 --selftest，资源齐不齐、几条用例过不过。

用法
----
    python build.py              打包
    python build.py --onedir     不要单文件，出一个文件夹（启动快些，但不好发）
"""
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ENTRY = os.path.join(HERE, "afdian_sku_app.py")
STAGE = os.path.join(HERE, "_bundle")
APP = "afdian_sku_app"

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

# 用不上、体积或者依赖又不小的模块。
# 有三条纪律：别把 pywebview 运行时要用到的排掉（bottle / clr_loader / proxy_tools /
# ftplib 这些），否则窗口直接起不来；urllib 那条链上会顺带 import ftplib 和
# shutil / subprocess，那类也得留着；distutils 和 setuptools 更不能排，
# PyInstaller 自己的 hook 要用它们，排了会当场报错。
EXCLUDES = [
    "tkinter", "_tkinter", "turtle", "turtledemo",
    "PIL", "numpy", "scipy", "pandas", "matplotlib",
    "PyQt5", "PyQt6", "PySide2", "PySide6", "qtpy", "shiboken2", "shiboken6",
    "gi", "cefpython3", "pygobject",
    "unittest", "doctest", "pydoc", "pydoc_data", "pdb", "test",
    "pip", "pkg_resources", "lib2to3",
    "sqlite3", "curses", "xmlrpc", "asyncio", "multiprocessing",
    "_elementtree", "pyexpat", "xml",     # 界面和接口都不碰 XML
    "_bz2", "_lzma",                      # 不做压缩包读写
    "_decimal", "_wmi",
]

if IS_WIN:
    # Windows 那边发请求走的是系统自带的 WinHTTP，OpenSSL 整套都用不上了。
    # 排掉 ssl 能省掉 libssl（1.5MB）；libcrypto 那 7.8MB 是被 _hashlib 拉着的，
    # 得连它一起排才真掉得下来。这项目从头到尾没用过 hashlib，
    # 而且 CPython 的 hashlib 在没有 _hashlib 时会自己退回内置实现，放心排。
    EXCLUDES += ["ssl", "_ssl", "_hashlib"]

# 图标。三个平台认的格式不一样：Windows 要 ico、macOS 要 icns、Linux 用 png。
# 文件不在也没关系，照样能打，只是成品没图标。
_icon_name = "icon.ico" if IS_WIN else ("icon.icns" if IS_MAC else "icon.png")
ICON = os.path.join(HERE, _icon_name)
if not os.path.isfile(ICON):
    ICON = None


def log(s):
    print(s, flush=True)


def stage():
    """把 ui.html 和它引用到的素材收拢到一个干净目录，PyInstaller 只打这里"""
    html = open(os.path.join(HERE, "ui.html"), encoding="utf-8").read()
    used = sorted(set(re.findall(
        r'app_assets/([A-Za-z0-9_.\-]+\.(?:png|jpg|jpeg|webp|svg|gif))', html)))
    if not used:
        raise SystemExit("ui.html 里一个 app_assets/ 引用都没有，先确认下界面文件对不对")

    # 目录本身不删，只清里面的东西；有些环境里删目录会失败
    if os.path.isdir(STAGE):
        shutil.rmtree(STAGE, ignore_errors=True)
    os.makedirs(os.path.join(STAGE, "app_assets"), exist_ok=True)

    shutil.copyfile(os.path.join(HERE, "ui.html"), os.path.join(STAGE, "ui.html"))
    total = os.path.getsize(os.path.join(STAGE, "ui.html"))
    manifest = os.path.join(HERE, "app_assets", "manifest.json")
    if os.path.isfile(manifest):
        shutil.copyfile(manifest, os.path.join(STAGE, "app_assets", "manifest.json"))
    for name in used:
        src = os.path.join(HERE, "app_assets", name)
        if not os.path.isfile(src):
            raise SystemExit("ui.html 引用了不存在的素材：" + name)
        shutil.copyfile(src, os.path.join(STAGE, "app_assets", name))
        total += os.path.getsize(src)
    log("[1/3] 收了 {} 个素材，连 ui.html 一共 {:.1f} KB".format(len(used), total / 1024))
    return used


def build(onedir=False):
    # 中转目录每次都换个新名字。PyInstaller 干活时会先删里面残留的旧文件
    # （base_library.zip 之类），在受限环境里这个删除动作容易被拦下来直接报错，
    # 用一个全新的目录就没这回事了。
    stamp = time.strftime("%m%d-%H%M%S")
    tmp_root = os.path.join(HERE, "_tmp")
    work = os.path.join(tmp_root, "pyi_" + stamp)
    outd = os.path.join(tmp_root, "out_" + stamp)
    os.makedirs(work, exist_ok=True)

    args = [sys.executable, "-m", "PyInstaller",
            "--noconfirm", "--optimize", "2",
            "--name", APP,
            "--distpath", outd,
            "--workpath", work,
            "--specpath", work,
            "--add-data", os.path.join(STAGE, "ui.html") + os.pathsep + ".",
            "--add-data", os.path.join(STAGE, "app_assets") + os.pathsep + "app_assets"]
    if onedir:
        args.append("--onedir")
    else:
        # 单文件；--windowed 是不要那个黑框控制台
        args += ["--onefile", "--windowed"]
    if ICON:
        args += ["--icon", ICON]
        log("        图标：{}".format(os.path.basename(ICON)))
    for m in EXCLUDES:
        args += ["--exclude-module", m]
    args.append(ENTRY)

    env = dict(os.environ)
    # pip 和 PyInstaller 都爱往临时目录里翻东西，指到工程里稳一点
    os.makedirs(tmp_root, exist_ok=True)
    env.update(TMPDIR=tmp_root, TEMP=tmp_root, TMP=tmp_root)

    t0 = time.perf_counter()
    r = subprocess.run(args, cwd=HERE, env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        log(r.stdout[-3000:])
        log(r.stderr[-3000:])
        raise SystemExit("PyInstaller 挂了，退出码 {}".format(r.returncode))
    log("[2/3] 打包完成，用了 {:.1f}s".format(time.perf_counter() - t0))

    if onedir:
        # onedir 出的是一整个目录，exe 在目录里面。拷单个 exe 出来是跑不了的
        built = os.path.join(outd, APP, APP + (".exe" if IS_WIN else ""))
        if not os.path.isfile(built):
            raise SystemExit("没找到 onedir 产物：" + built)
        log("        onedir 目录：{}（整包一起发）".format(os.path.dirname(built)))
        return built

    dist = os.path.join(HERE, "dist")
    os.makedirs(dist, exist_ok=True)

    if IS_MAC:
        # macOS 的成品是一整个 .app 目录，千万别只把它内部那个可执行文件揪出来：
        # Info.plist 和图标资源都在旁边，少了就双击打不开（连报错都看不着）。
        # 整个目录原样搬过去，返回值仍然指里面的可执行文件，自检要用它。
        bundle = os.path.join(outd, APP + ".app")
        if not os.path.isdir(bundle):
            raise SystemExit("没找到 .app：" + bundle)
        final = os.path.join(dist, APP + ".app")
        shutil.rmtree(final, ignore_errors=True)
        shutil.copytree(bundle, final)
        log("        产物：{}（整个 .app 目录，双击就能开）".format(final))
        return os.path.join(final, "Contents", "MacOS", APP)

    built = os.path.join(outd, APP + (".exe" if IS_WIN else ""))
    if not os.path.isfile(built):
        raise SystemExit("没找到产物：" + built)
    final = os.path.join(dist, os.path.basename(built))
    shutil.copyfile(built, final)
    if not IS_WIN:
        # Linux 上这东西是直接拿去执行的，可执行位不能丢
        os.chmod(final, 0o755)
    log("        产物：{}   {:.1f} MB".format(final, os.path.getsize(final) / 1048576))
    return final


def selftest(exe):
    log("[3/3] 自检…")
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    r = subprocess.run([exe, "--selftest"], capture_output=True, text=True, env=env,
                       encoding="utf-8", errors="replace", timeout=180)
    for line in (r.stdout or "").splitlines():
        if "SELFTEST" in line:
            log("        " + line.split("] ", 1)[-1])
    return r.returncode == 0


if __name__ == "__main__":
    stage()
    exe = build(onedir="--onedir" in sys.argv)
    ok = selftest(exe)
    log("搞定" if ok else "打完了，但自检没过，看看上面")
    sys.exit(0 if ok else 1)
