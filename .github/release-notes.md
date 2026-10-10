爱发电商品的 `plan_id` 和 `sku_id` 查询工具。粘一个商品地址进去，直接告诉你这两个 id。

爱发电没有 `goods_id`，「商品」其实是方案（plan），型号才是 sku，
而且这两个 id 在商品页面上都看不见 —— 这个工具就是干这个的。

### 下载

- **Windows**：`afdian_sku_app.exe`，双击运行，不需要装 Python
- **macOS**：`afdian_sku_app-macos.zip`，解压出 `.app` 拖进「应用程序」。
  首次打开如果被拦，右键 →「打开」放行一次即可
- **Linux**：`afdian_sku_app-linux.tar.gz`，解压后 `./afdian_sku_app`。
  需要先装系统依赖：`sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-webkit2-4.1`

Windows 版用的是系统自带的 WebView2 显示界面，Win11 和大部分 Win10 都有了；
万一你的系统没有，它不会报错退出，而是自动改用默认浏览器打开同样的界面，功能一样。

### 用法

粘贴商品地址 → 点查询 → 复制 id。支持一次粘多条（一行一个）。
按 `Ctrl+Enter` 也能提交。地址也可以直接粘 32 位裸 id。
