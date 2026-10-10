<img src="icon.png" width="120" alt="">

# 爱发电 SKU 查询器
<img width="816" height="289" alt="image" src="https://github.com/user-attachments/assets/7096b933-198a-4b7f-94ec-832bba8bd44f" />

把爱发电的商品地址粘进来，它告诉你这个商品的 `plan_id` 和 `sku_id`。

## 为什么会写这个

爱发电跟别的平台不太一样：它没有 `goods_id` 这种东西。所谓的"商品"在它那儿就是一个**方案**（plan），
地址栏里那串 32 位的十六进制就是 `plan_id`；一个方案里如果分了几种型号，每种型号叫一个 **sku**。

麻烦的地方在于，这两个 id 在商品页面上都看不见。想对接支付、发卡、或者做订单校验的时候，
偏偏就得填这两个东西，只能去翻开发者文档猜，或者自己抓包。

后来发现商品页自己就在调这个接口，而且是公开的，不用登录也不用 token：

```
GET https://afdian.com/api/creator/get-plan-skus?plan_id=<地址里那段>&is_ext=
```

浏览器里直接请求它会被跨域拦掉，所以这个小工具在本地起了个只监听 `127.0.0.1` 的服务，
让它替页面去请求，再把结果递回来。就这么点事。

## 怎么用

1. 打开爱发电的商品页，在想查的那个档位上点右键 →「复制链接地址」
2. 粘到输入框里（一行一个，可以一次粘一堆）
3. 点查询。结果里 `plan_id` 和 `sku_id` 都在，旁边的复制按钮点一下就进剪贴板了

按 `Ctrl+Enter` 也能提交。默认勾了"自动复制 sku_id"，会把查到的所有型号拼起来一起复制走。

地址也可以直接粘 32 位的裸 id。如果一个地址里混着好几个 32 位 id（创作者的 user_id、订单号、
型号 id 之类的），它会挨个试过去，不用自己分辨哪个是哪个。

## 下载

到 [Releases](../../releases) 里挑对应平台的：

| 平台 | 文件 | 备注 |
|---|---|---|
| Windows | `afdian_sku_app.exe` | 双击就能跑，不用装 Python |
| macOS | `afdian_sku_app.app` | 第一次打开可能要在「系统设置 → 隐私与安全性」里放行一下 |
| Linux | `afdian_sku_app` | 需要 Python3 的 PyGObject 和 WebKitGTK，见下面 |

Windows 版是单文件，双击直接开窗口。它用的是系统里的 Edge WebView2 来显示界面，
Win11 和大部分 Win10 都已经有了；万一你的系统没有，它不会报错退出，
而是自动改用默认浏览器打开同样的界面，功能一模一样。

Linux 上要先装两个系统包，界面才显示得出来：

```bash
# Debian / Ubuntu
sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-webkit2-4.1
# Fedora
sudo dnf install python3-gobject gtk3 webkit2gtk4.1
```

## 从源码跑

需要 Python 3.8 以上。

```bash
pip install pywebview
python afdian_sku_app.py
```

几个开关，调界面或者排查问题的时候用得上：

```bash
python afdian_sku_app.py --no-window    # 只起服务不开窗口，用浏览器看
python afdian_sku_app.py --browser      # 强制用系统浏览器打开
python afdian_sku_app.py --framed       # 用系统原生边框（无边框出问题时的备胎）
python afdian_sku_app.py --selftest     # 跑几条用例就退出，不开窗口
```

## 自己打包

```bash
pip install pyinstaller
python build.py
```

产物在 `dist/` 里。注意 PyInstaller 不能交叉编译，想要 macOS 的包就得在 macOS 上打，
想要 Linux 的就得在 Linux 上打。嫌麻烦的话直接用 GitHub Actions 吧，
`.github/workflows/build.yml` 里已经配好了，打个 tag 推上去就自动出三个平台的包。

Windows 那边打包时会顺手把 `ssl`、`_ssl`、`_hashlib` 排掉。因为 Windows 上的网请求走的是
系统的 WinHTTP，OpenSSL 那一整套都用不上，排掉能少 9MB 出来（光 `libcrypto` 一个就 7.8MB）。
macOS 和 Linux 走标准库，没有这个待遇。

## 关于界面

界面上的图是从05的 [CozyUI+](https://github.com/Fogg05/CozyUI-Plus/) 材质包里裁下来的，
用的是 Minecraft GUI 那套九宫格贴图，切法记在 `app_assets/manifest.json` 里。
输入框、结果面板、滚动条这几块没有现成素材，是手写的 CSS。

CozyUI+ 是个 Minecraft 材质包，把原版界面和一堆客户端模组的界面重做成了干净柔和的高清风格，
顺便也欢迎去给那个仓库点个 ⭐。

----

如果喜欢也可以支持一下薄荷（StarCloudDream）这有助于薄荷开发更多有趣的项目：
https://afdian.com/a/StarCloudDream?utm_source=copylink&utm_medium=link
You can also choose to sponsor me directly on GitHub, which could be a huge help for my future development.

<img width="375" height="516" alt="afdian- 未认证 星云梦" src="https://github.com/user-attachments/assets/0b3fdf4f-99ec-4a77-af93-587036574fd8" />

----

## 许可

代码 MIT，随便用。

`app_assets/` 里那十张界面图来自 CozyUI+ ——所以素材按照 GPL 协议这一点需要注意。
