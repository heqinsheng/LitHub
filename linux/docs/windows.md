# 在 Windows 上跑 LitHub

> ⚠️ **先说清楚现状**：代码已做 Windows 适配（文件读写与子进程强制 UTF-8、控制台输出
> 的编码兜底、命令行长度按平台取阈值、路径全部走 `os.path.join`），**但作者手上没有
> Windows 机器，整条路径没有实测过**。本文按「照做」的顺序写，**本文里的每一步都未经
> 作者验证**。跑通或跑不通都欢迎开 issue 把 `python scripts\doctor.py` 的完整输出贴上来。

Windows 上有两条路，**先试第一条**：

| 路线 | 改动量 | 代价 |
|---|---|---|
| **A. WSL2 里跑 Linux** | 零改动，命令行与文档原样照抄 | 要装 WSL2；Zotero 在 Windows 侧、脚本在 WSL 里，需要开镜像网络 |
| **B. 原生 Windows** | 用 `python` 而不是 `python3`、路径用反斜杠 | 要自己装 poppler / Ghostscript / MinerU；长路径与编码要额外处理 |

本项目里所有脚本**只依赖 Python 标准库**，没有编译型依赖，所以两条路都不需要折腾
C 编译器或虚拟环境——难的是那几个外部命令行工具。

---

## 路线 A：WSL2（推荐先试）

在 WSL2 里就是一个正常的 Linux，`帮助手册.md` 的每一步都能直接照抄（`python3`、`~/LitHub`、
`apt install`）。

### A1. 装 WSL2 + Ubuntu

```powershell
# 管理员 PowerShell
wsl --install -d Ubuntu
# 装完重启，首次进入 Ubuntu 会要你设一个 Linux 用户名和密码
wsl --set-default-version 2
```

### A2. 把项目放进 Linux 家目录

**不要放在 `/mnt/c/...` 下**：跨文件系统访问慢得多（`papers/` 里有几十万个
`images/*.jpg`，全库约 113 MB），而且权限位与文件名大小写语义不同，容易出怪问题。

```bash
sudo apt update && sudo apt install -y python3.12 ghostscript poppler-utils curl
mkdir -p ~/LitHub && cd ~/LitHub      # 然后把发布包解压/复制到这里
python3 --version                     # 需要 ≥ 3.12
```

Ubuntu 22.04 自带的 python3 是 3.10，太旧——用 `deadsnakes` PPA 或直接装 24.04。

### A3. 让 WSL 连上 Windows 侧的 Zotero

Zotero 装在 Windows 上、脚本跑在 WSL 里时，WSL 里的 `localhost:23119` 默认**不是** Windows 的
回环地址，连不上。官方给的办法是**镜像网络模式**：

新建（或编辑）`C:\Users\<你的用户名>\.wslconfig`：

```ini
[wsl2]
networkingMode=mirrored
```

存盘后在 **PowerShell** 里执行 `wsl --shutdown`，再启动 WSL。

> 这来自微软官方文档的镜像网络模式（[WSL networking](https://learn.microsoft.com/windows/wsl/networking)），
> **本项目未实测**。若镜像模式不可用，退路是让脚本走 Windows 主机的 IP（在 WSL 里
> `ip route show | grep default` 拿到的那个地址）——但那要改 `scripts/zapi.py` 的 `API`
> 常量，属于改代码，不再是「零改动」路线。

装好后先自检：

```bash
cd ~/LitHub
python3 scripts/doctor.py     # Zotero 与密钥两项应是 ✅
```

### A4. 长路径、systemd、定时任务

- WSL 里是 ext4，**没有 260 字符限制**，不用管长路径。
- WSL 里的 systemd 默认是开的（`/etc/wsl.conf` 里 `[boot] systemd=true`，较新版本默认如此）。
  本项目的两个 timer 可以按 `帮助手册.md` 原样配。
- 关机时 WSL 会停（Windows 关掉终端后一段时间 WSL 实例会回收），所以定时任务更适合放在
  **Windows 的任务计划程序**里、由它去调 `wsl.exe`；如果只是想手动跑，忽略定时任务即可。

---

## 路线 B：原生 Windows

按顺序做，每步都有验证命令。

### B1. Python 3.12+

到 <https://www.python.org/downloads/windows/> 下载 3.12 或更高版本的安装包，
**安装时勾上「Add python.exe to PATH」**（不勾的话后面所有 `python` 命令都会找不到）。

```powershell
# 重开一个终端再验证
python --version      # Python 3.12.x
pip --version
```

本项目不需要 `pip install -r ...`——脚本只用标准库。

### B2. poppler（提供 `pdfinfo` / `pdftotext`）

`intake_pdfs.py` 靠这两个命令读页数与首页 DOI。官方没有 Windows 安装包，用社区打包版：

1. 打开 <https://github.com/oschwartz10612/poppler-windows/releases>，
   下载最新的 `Release-xx.xx.x-0.zip`
2. 解压到固定位置，比如 `C:\tools\poppler-24.08.0\`（**别放在临时目录**，别删）
3. 把 `C:\tools\poppler-24.08.0\Library\bin` 加进 PATH：
   **设置 → 系统 → 系统信息 → 高级系统设置 → 环境变量 → 用户变量 `Path` → 新建**，
   粘进上面那个目录
4. **重开终端**（PATH 的改动对已经打开的终端不生效），验证：

```powershell
pdfinfo -v
pdftotext -v
```

### B3. Ghostscript（提供 `gswin64c`）

官网安装包：<https://www.ghostscript.com/releases/gsdnld.html>（选 Windows 64-bit）。
安装程序默认把它写进 PATH；装完**重开终端**验证：

```powershell
gswin64c --version
```

⚠️ **Windows 包给的是 `gswin64c.exe`（控制台版），没有 `gs.exe`**——Linux 上那个
`gs` 命令才是这个名字。脚本两处都认（`gs` → `gswin64c` 依次探测），但你自己敲命令验证时
要用 `gswin64c`；`gs --version` 报「找不到」并不代表没装好。

> 只有 MinerU 的 `--flash` 兜底模式（>8 MB 预压缩）才用它；默认的 `extract` 模式不需要。
> 没装、或压不到 8 MB 以内时，`mineru_batch.py` **只跳过那一篇**并写明原因，不会中断整轮。

### B4. MinerU CLI

```powershell
pip install mineru
mineru-open-api auth          # 按提示贴 token
where.exe mineru-open-api     # 应输出 …\Python312\Scripts\mineru-open-api.exe
```

pip 的脚本目录（`…\Python312\Scripts`）必须在 PATH 上，否则 `mineru_batch.py` 会报找不到命令
（脚本里用的是裸命令名）。token 在 <https://mineru.net/apiManage/token> 申请，
**正确长度 51 字符**——复制不全是这里最常见的失败原因。

### B5. LLM CLI

`summarize_batch.py` 与 `embed_figures.py` 默认去 `~/.kimi-code/bin/kimi` 找 CLI——
**那是 Unix 布局的路径，Windows 上通常不存在**。用环境变量指到真正的可执行文件：

```powershell
# 当前会话
$env:LITHUB_KIMI = "C:\Users\<你>\.kimi-code\bin\kimi.exe"
# 永久生效（新开终端起效）
setx LITHUB_KIMI "C:\Users\<你>\.kimi-code\bin\kimi.exe"
```

顺带把礼貌池邮箱设上，否则 Unpaywall 会 422 硬拒、`A` 项永远为 0（见 `帮助手册.md` §3.9）：

```powershell
setx LITHUB_MAILTO "你的邮箱@example.com"
```

日报里的**翻译与中文提要是直接 HTTP 调 DeepSeek**，不走上面这个 CLI，配置在
`C:\Users\<你>\.kimi-code\config.toml` 的 `[providers.deepseek].api_key`——路径由代码里
`Path.home() / ".kimi-code" / "config.toml"` 拼出，Windows 上一样能读到。

### B6. Zotero

1. 装 Zotero 桌面版（<https://www.zotero.org/download/>）并**保持运行**——脚本连的是它开的
   本地端口 23119，Zotero 关着就没人监听。
2. Zotero → **设置 → 高级** → 勾选 **「允许其他应用与本机 Zotero 通信」**。
   不勾会返回 403（`doctor.py` 会明确提示这一条）。
3. 做一次写入授权，把返回的 key 存进 `state\zotero_local_key`。

**PowerShell 版**（推荐，Windows 自带的 `Invoke-RestMethod` 比 curl 省事）：

```powershell
cd $HOME\LitHub
New-Item -ItemType Directory -Force state | Out-Null

# 1) 取 Server-ID（在响应头里）
$sid = (Invoke-WebRequest "http://127.0.0.1:23119/api/users/0/collections?limit=1" `
        -Headers @{"Zotero-API-Version"="3"}).Headers["Zotero-Server-ID"]

# 2) 授权：Zotero 会弹一个对话框，选「始终允许」
$body = @{ appName = "LitHub" } | ConvertTo-Json
$key = (Invoke-RestMethod "http://127.0.0.1:23119/api/local/authorize" -Method Post `
        -ContentType "application/json" -Headers @{"Zotero-Server-ID"=$sid} -Body $body).key

# 3) 落盘（用 .NET 写，不带 BOM、不带多余换行）
[IO.File]::WriteAllText("$PWD\state\zotero_local_key", $key)
```

**bash 版**（Git Bash / WSL / MSYS2 里可用，把 `python3` 换成 `python`）：

```bash
cd ~/LitHub && mkdir -p state
SID=$(curl -sI http://127.0.0.1:23119/api/users/0/collections?limit=1 \
      -H "Zotero-API-Version: 3" | grep -i '^Zotero-Server-ID:' | tr -d '\r' | awk '{print $2}')
curl -s -X POST http://127.0.0.1:23119/api/local/authorize \
  -H "Content-Type: application/json" -H "Zotero-Server-ID: $SID" \
  -d '{"appName":"LitHub"}' | python -c 'import sys,json;print(json.load(sys.stdin)["key"])' \
  > state/zotero_local_key
```

成功判据（两种终端都一样）：

```powershell
(Get-Item state\zotero_local_key).Length      # 非 0 即可，形如 04cB9QTKO9UNhf1umsHgcYZBEsrunZ9J
```

> ⚠️ **`state\zotero_local_key` 要在会连 Zotero 的脚本跑起来之前就存在**：`scripts/zapi.py`
> 的 `_key()` 在**第一次真发请求时**才读它——只 import 不碰，所以 CI 里编译得动；
> 缺了它真发请求就会报错。
>
> ⚠️ 用 PowerShell 的 `>` 重定向写这个文件会带上 UTF-16/BOM 或末尾换行，key 就脏了——
> 用上面那句 `[IO.File]::WriteAllText`。

### B7. 终端编码

Windows 中文环境的 Python 默认编码是 **cp936 (GBK)**。仓库里所有写文本的地方都显式带了
`encoding="utf-8"`（日志、配置、Markdown 都在内），所以**文件读写不依赖它**；踩坑的地方在
**往控制台/管道输出**这一侧。

**真正会崩的是什么**（2026-09-21 实测，用 `PYTHONIOENCODING=cp936` 在 Linux 上复现）：

```
$ python3 scripts/digest.py
UnicodeEncodeError: 'gbk' codec can't encode character '\xc5' in position 248
$ python3 scripts/sync_properties.py
UnicodeEncodeError: 'gbk' codec can't encode character '\xf6' in position 37
```

崩的**不是 emoji**（`✅ ⚠️` 只是偶尔出现在个别输出行上），是**文献数据原样打进控制台**：
标题里的 `Å`、作者里的 `ö`、标题里的 `Π` 这类**非 GBK 字符**。而且**不只是重定向**——
直接打在控制台上一样崩（上面两条就是直接跑出来的），别以为「控制台走 Unicode API 就没事」。

**现在的处置（不需要你做任何事）**：脚本一律把输出流的错误策略放宽成
`errors="replace"`，编不出来的字符退化成 `?`，不再中断：

```
? ? Π ≈ → ℃ 结构退化 …        # Å / ö / ✅ / ⚠️ 变成 ?，其余原样
```

兜底写在四个被普遍 import 的模块里（`zapi.py` / `topic.py` / `find_new.py` /
`sync_properties.py`，与 `zapi.py` 顶部那段 IPv4 补丁同一套路：import 即生效），
另外不 import 本地模块的几个脚本（`digest.py`、`embed_figures.py`、`mineru_batch.py` 等）
各自带一份。**它只改错误策略、不改编码**：UTF-8 环境下的输出字节一个都不变，所以 Linux
侧的行为没有任何变化。CI 里加了一步冒烟（`.github/workflows/ci.yml` 的
「控制台编码冒烟」）：在 cp936 下 import 全部脚本后 print 一段非 GBK 语料，断言退出码 0
且输出里没有 `UnicodeEncodeError`。

> 代价：cp936 下那些字符在终端里显示成 `?`，信息有损（但不再崩）。

**想看到真字符（改善，不是必需）**：把 Python 整个跑在 UTF-8 模式下（PEP 540），
一次设定，所有脚本和子进程都受益：

```powershell
chcp 65001                   # 当前控制台切 UTF-8（每个新窗口都要重来）
$env:PYTHONUTF8 = "1"        # 当前会话
setx PYTHONUTF8 1            # 永久（新开终端起效）
```

设了之后不再有 `?`，中文与 Å/ö/Π/✅ 都原样显示。**在加兜底之前这一步是「必需」，现在是
「想看得更清楚」**——不设也不会再有任何脚本因为编码报错退出。

### B8. 长路径（重要）

Windows 默认单个路径上限 **260 字符**。本项目的深层路径形如：

```
C:\Users\<你>\LitHub\papers\24LAL9CS_ΠConjugated_Polyphenols_Regulate_Interfacial_Charge_to_Stabi\images\a1b2c3….jpg
                          └────── 8 位 key + 60 字标题 slug ──────┘        └─ 64 位 hash ─┘
```

反斜杠后的**最长相对路径实测 152 字符**（2026-09-21 在 193 篇的库上量的：
`papers\<8位key>_<60字slug>\images\<64位hash>.jpg`，其中目录名 69 + 图片名 68 已是上限），
加上 `C:\Users\你的名字\LitHub`（典型 24–28 字符）**总长 176–180，离 260 还有约 80 字符余量**。
`doctor.py` 会把这台机器的实际数字算给你看——想稳妥就直接按下面开长路径，但这一项**不是**
必须先解决的拦路虎。

两种开启方式，**选一种，然后重启**（改注册表后重启资源管理器还不够，安全起见重启系统）：

- **组策略**（Windows 专业版/企业版）：
  `gpedit.msc` → 计算机配置 → 管理模板 → 系统 → 文件系统 →
  **启用 Win32 长路径** → 已启用
- **注册表**（家庭版没有 gpedit）：

```powershell
# 管理员 PowerShell
New-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" `
  -Name LongPathsEnabled -Value 1 -PropertyType DWord -Force
```

另外注意：**项目根目录必须是 `%USERPROFILE%\LitHub`**（即 `C:\Users\<你>\LitHub`）。
脚本里写死了 `~/LitHub`，放到 `D:\文献\LitHub` 是跑不起来的——
`doctor.py` 第一项检查就会报「项目根目录不存在」。

### B9. 定时任务

Windows 上没有 systemd。用**任务计划程序**替代，模板在
[`task-scheduler.xml`](task-scheduler.xml)：

1. 打开「任务计划程序」（`taskschd.msc`）
2. 右侧 → **导入任务…** → 选 `docs\task-scheduler.xml` → 打开
3. **把 XML 里的占位符改成你的实际路径**（`<PATH_TO_PYTHON>`、`<PATH_TO_LITHUB>`），
   或在导入后的「操作」页里直接改
4. 触发时间默认是**每天 05:00**；「条件」页里建议勾掉「只有在计算机使用交流电源时才启动」
5. 导入后先右键 → **运行**，手工试一次；再看「上次运行结果」是否为 `0x0`

对应关系：`lithub-daily.timer` → 日报任务；`lithub-profile.timer`（每月 1 日刷画像）与
`lithub-mailcheck.timer`（06:00 补发邮件）可以照葫芦画瓢复制三份，改 `--arguments` 即可。

> 频率闸门（`config/runtime.json` 的 `schedule.every_n_days`）与 systemd 那套是**同一套代码**，
> 任务计划程序每天触发、脚本自己决定跑不跑，行为一致。

---

## 自检与报错

装完先跑：

```powershell
cd $HOME\LitHub
python scripts\doctor.py
```

九项检查会逐条告诉你缺什么、Windows 上怎么装。退出码 `0` = 没有硬性缺失。

### Windows 上特有的症状

| 症状 | 原因 | 处理 |
|---|---|---|
| `UnicodeDecodeError: 'gbk' codec can't decode byte …` | 读到了旧版脚本写的、或手写的非 UTF-8 文件 | 把文件转成 UTF-8；确认跑的是本仓库当前版本的脚本 |
| 目录名/标签变成 `鐣岄潰鍙嶅簲` 这种乱码 | 文件是 UTF-8、却按 cp936 解码（**静默、不报错**） | 确认脚本是最新的；设 `PYTHONUTF8=1`；乱码已写进 Zotero 的要用 `scripts/migrate_labels.py` 改回 |
| `WinError 206 文件名或扩展名太长` | oneshot 的 inline 载体把全文塞进命令行，Windows 上限 32767 字符 | 已按平台把阈值降到 30 KB，超了自动降级到临时文件载体；若仍报，说明 `ARGV_LIMIT` 与你的调用方式不符，请回报 |
| `OSError: [WinError 3] 系统找不到指定的路径` | 路径超 260，或项目不在 `%USERPROFILE%\LitHub` | 见 B8 |
| `文件名或扩展名太长` / `[Errno 2] No such file` 但文件明明在 | 同上（路径过长） | 见 B8 |
| 一堆 `EXC(...)` 写不出总结 | LLM CLI 路径没设 | 设 `LITHUB_KIMI`，见 B5 |
| `WinError 193「%1 不是有效的 Win32 应用程序」`（`summarize_batch.py` / `embed_figures.py`） | `LITHUB_KIMI` 指向了 `.cmd`/`.bat`——Windows 的 `CreateProcess` 不能直接执行批处理 | 改指向 `.exe`（`where.exe kimi` 列全部候选）；`doctor.py` 会对此单独报警 |
| `mineru_batch.py` 报 `FAIL(超10MB，压不下或缺 gs…)` | `--flash` 模式下 PDF > 8 MB，而 Ghostscript 不在 PATH（Windows 上命令名是 `gswin64c`） | 见 B3 装好并重开终端；或改用默认的 `extract` 引擎（限 200 MB，不需要压缩）|
| 连不上 Zotero（403 / 连接被拒） | 开关没勾 / Zotero 没开 | 见 B6；`doctor.py` 会直接指出是哪一种 |

### 已知的未解决问题

- **整条流水线没有在 Windows 上跑通过**（作者无机器）。文件编码、子进程编码、控制台编码、
  命令行长度、路径分隔符这几类是**按机制改的**，由 `.github/workflows/ci.yml` 在
  `windows-latest` 上验证「能编译、能导入、中文编码往返正确、cp936 下 print 非 GBK 语料不炸」；
  **Zotero / MinerU / LLM CLI 这三样在 CI 里没有**，所以 CI 绿 ≠ 流水线通。
- ~~仓库里若干脚本的 `print()` 会输出 `✅ ⚠️` 等字符，重定向时可能报 `UnicodeEncodeError`~~
  ——**2026-09-21 已修**，而且原来的描述并不准确。真相是：崩的不是 emoji，是**文献数据里的
  非 GBK 字符**（标题的 `Å`、作者的 `ö`、标题的 `Π`），**直接打控制台与重定向都会崩**
  （实测 `digest.py`、`sync_properties.py`）。现在全部脚本的输出流都设了
  `errors="replace"`（编不出来退化成 `?`，UTF-8 环境字节不变），`PYTHONUTF8=1` 从「必需」
  降为「想看到真字符的改善」。细节见 B7。
- `make_release.py` 里那句 `subprocess.run([...], text=True)`（判定是否 git 工作树）
  **没有显式 `encoding=`**，Windows 上按 locale（cp936）解码子进程输出。目前它的输出只有
  ASCII（`true` / 空），实际不会出问题；记在这里是因为仓库其它子进程调用都显式写了
  `encoding="utf-8"`，这一处是例外，改动它的人别以为是漏看。
- **文件里带不带 `newline=""` 不一致**：只有 `sync_properties.py` 写 `summary.md` 时钉了
  `newline=""`（怕 Windows 把 LF 翻成 CRLF，影响 git 与 Obsidian）；其余写 Markdown 的地方
  （`daily_digest.py` 的日报、`embed_figures.py` / `summarize_batch.py` 的 `summary.md`、
  `mineru_batch.py` 的 `paper.md`、`sync_classification.py` 的总表、`build_profile.py` 的画像）
  在 Windows 上都会写成 **CRLF**。后果只是跨平台 git diff 噪声与行尾混用，读回时通用换行会
  还原成 `\n`，不影响功能；**没有实测过**，也不打算为它加一层转换。
- RSS 内存/句柄相关的行为差异没有评估过（本项目没有长驻进程，影响应该很小）。
