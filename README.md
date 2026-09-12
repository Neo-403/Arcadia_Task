# Arcadia_Task

本仓库是 [Arcadia](https://arcadia.cool)（SuperManito 开源的一站式代码自动化运维平台）的**签到脚本仓库**。
所有脚本均为零/低依赖 Python，可直接本地运行，也可整仓导入 Arcadia 后由平台**自动创建定时任务**每天执行。

- 脚本实体存放在 [`ZWZW/`](./ZWZW)，文件名以 `_账号` / `_Cookie` 结尾的脚本由 QD 格式（.har）转换而来（后缀按鉴权方式自动选择）；
  根目录 [`QD转Python.py`](./QD转Python.py) 是转换工具，生成后会自动把脚本登记到导入列表 [`arcadia/tasks.json`](./arcadia/tasks.json)；
- `arcadia/` 存放接入 Arcadia 的同步配置与定时清单；
- `tools/` 提供定时任务元数据生成工具。

---

## 目录结构

```
Arcadia_Task/
├── ZWZW/                              # 各站点签到脚本（.py）
│   ├── 爱阅书吧_Cookie.py
│   ├── 梦楠分享.py
│   └── ...
├── arcadia/
│   ├── sync.yaml                      # 可直接整文件使用的 Arcadia 同步配置
│   ├── tasks.json                     # 每个脚本的定时规则（cron），工具的唯一数据源
│   ├── tasks.state.json               # 脚本正文指纹 + 更新时间记录（工具自动维护）
│   └── tasks.example.json             # 配置格式示例
├── tools/
│   ├── 生成定时头.py         # 把 cron / 更新时间写入脚本头部注释（幂等，自动刷新）
│   └── install_deps.py                # 依赖自检/安装（扫描 ZWZW，缺失的自动 pip install）
└── README.md
```

## 脚本清单

| 脚本文件 | 站点 | SITE_TAG | 鉴权方式 |
|---|---|---|---|
| 爱阅书吧_Cookie.py | iiishu.com | `IIISHU` | Cookie |
| 爱桌游_账号.py | zhuoyoux.com | `ZHUOYOUX` | 账号密码 |
| 边界AI_账号.py | ai1foo.com | `AI1FOO` | 账号密码 |
| 单机Game怀旧馆_账号.py | （占位 `{{DOMAIN}}`） | `GAME` | 账号密码\* |
| 飞牛私有云社区_Cookie.py | club.fnnas.com | `FNNAS` | Cookie |
| 父子被窝_账号.py | fzbw.cn | `FZBW` | 账号密码 |
| 宽带技术网_Cookie.py | chinadsl.net | `CHINADSL` | Cookie |
| 流星社区_账号.py | liuxingw.com | `LIUXINGW` | 账号密码 |
| 梦楠分享_账户.py | mnpc.net | `MNPC` | 账号密码（需 `curl_cffi`） |
| 木木云盘搜_账号.py | api.aclink.top | `ACLINK` | 账号密码 |
| 七日杀中文网_账号.py | 7risha.com | `Q7RISHA` | 账号密码 |
| 数码之家_Cookie.py | mydigit.cn | `MYDIGIT` | Cookie |
| 柚坛社区_账号.py | uotan.cn | `UOTAN` | 账号密码 |
| 再漫画_账号.py | i.zaimanhua.com | `ZAIMANHUA` | 账号密码 |
| 咸鱼单机_账号.py | xianyupc.com | `XIANYUPC` | 账号密码\* |
| 立创开源平台签到_Cookie.py | jlc.com / oshwhub.com | `JLC` | Cookie |
| Godlike_账号.py | godlike.host | `GODLIKE` | Cookie\* |
| NS中文网_账号.py | ns211.com | `NS211` | 账号密码 |
| Switch520_账号.py | switch520.org | `SWITCH520` | 账号密码 |

> \* `单机Game怀旧馆_账号.py` 生成的域名还是占位符 `https://{{DOMAIN}}`，使用前需先按站点实际情况补全 `BASE` 与账号信息。
> \* `咸鱼单机_账号.py` 所在站点带 `/_guard/auto.js` 人机校验：出口 IP 被风控时脚本会明确报"命中 guard 风控"（不会误判成签到成功），
> 需换干净出口（配 `XIANYUPC_PROXY`）或等风控解除再跑。
> \*\* `Godlike_账号.py` 是「主机免费续期 / 每日签到」，Godlike 面板是 Pterodactyl 原生面板，续期就一个 HTTP 请求
> （`POST /api/client/servers/{uuid}/addtime`），**零第三方依赖（Python 标准库 urllib 即可）**，不需要浏览器。
> 鉴权用 **Cookie 模式**最轻（复制浏览器里的 `remember_web_...` cookie 配到 `GODLIKE_COOKIE`）；账号密码模式会尝试自动登录，
> 但登录页带 reCAPTCHA + 加密 CSRF，纯 HTTP 常被拦，失败会提示改用 Cookie（详见生成标准 §11.4）。

## 本地运行与配置

每个脚本顶部都有**内置配置常量**（`BUILTIN_ACCOUNT` / `BUILTIN_COOKIE` / `BUILTIN_USER` / `BUILTIN_PASS` / `BUILTIN_PROXY` / `BUILTIN_DELAY`…），
直接编辑脚本后运行即可；也可以不修改脚本，用**环境变量**注入（带站点前缀，多脚本互不串号）：

```bash
# 账号模式（推荐）：一个变量装完"账号#密码"，多账号用 换行 / @@ / && 分隔（借鉴青龙写法）
FZBW_ACCOUNT='zhangsan#123456' python ZWZW/父子被窝_账号.py
FZBW_ACCOUNT='zhangsan#123456@@lisi#654321' python ZWZW/父子被窝_账号.py

# Cookie 模式：一个变量装 Cookie，多个同样换行/@@/&& 分隔
IIISHU_COOKIE='xxx' python ZWZW/爱阅书吧_Cookie.py
```

**账号格式**：`账号#密码`（`#` 与 `:` 均可，取**最先出现**的那个切一次，另一个可以出现在密码里，
如 `zhangsan:a#b#c` → 账号 `zhangsan`、密码 `a#b#c`）。

- 单变量 `<SITE_TAG>_ACCOUNT`（或内置 `BUILTIN_ACCOUNT`）**优先**；未配置时回退旧写法
  `<SITE_TAG>_USER` + `<SITE_TAG>_PASS`（旧写法继续可用，已配置的脚本无需改动）；
- 多账户：账号/Cookie 用 换行 / `@@` / `&&` 分隔，或直接写 Python 列表；
- 常用依赖：`梦楠分享_账户.py` **必须** `curl_cffi`（TLS 指纹突破）；其余脚本默认走标准库
  `urllib`（零依赖）。`Godlike_账号.py` 本身就是**零依赖**轻量方案（标准库 urllib 直接调 Pterodactyl API，无需浏览器），
  只有个别站点需 `TLS_IMPERSONATE`（见生成标准 §11.1）才需 curl_cffi。
- **依赖自动安装**：脚本运行时发现缺 `curl_cffi` 会**自动 `pip install`**（静默，可用
  `<SITE_TAG>_AUTO_INSTALL=0` 关闭）；也可在拉取仓库后统一执行一次：

```bash
python tools/install_deps.py              # 检测并询问
python tools/install_deps.py --yes        # 直接安装
python tools/install_deps.py --mirror https://pypi.tuna.tsinghua.edu.cn/simple   # 指定镜像
```

  在 Arcadia 里想让它随仓库自动执行，可在面板【定时任务】新建一条任务，命令填
  `python /arcadia/repo/Neo-403_Arcadia_Task/tools/install_deps.py --yes`（每天一次或手动跑一次均可）。

---

## 导入到 Arcadia

### 前置

1. 已部署 Arcadia（镜像 `supermanito/arcadia`，默认面板 `http://<host>:5678`）；
2. 面板【环境配置 → 运行环境】已安装 **Python** 运行时；
3. 本仓库已推到远端（示例地址 `https://github.com/Neo-403/Arcadia_Task.git`，fork 后请换成你自己的）。

### 方式一：整仓 Git 同步（推荐，随仓库自动增删任务）

Arcadia 用 `/arcadia/config/sync.yaml` 统一管理「代码同步」。本仓库已内置一份
**可直接使用**的配置 [`arcadia/sync.yaml`](./arcadia/sync.yaml)：公开仓库 + 实际克隆
地址 + 已开启自动生成定时任务，复制到容器覆盖同名文件即可生效：

```bash
cp arcadia/sync.yaml /arcadia/config/sync.yaml   # 若容器里已有其它仓库配置请改为并入 repo 列表
arcadia update repo
```

> 面板【环境配置 → 代码同步】可视化编辑也可达到同样效果，关键配置为：

```yaml
repo:
  - name: Arcadia_Task
    url: 'https://github.com/Neo-403/Arcadia_Task.git'
    branch: main
    enable: true
    isPrivate: false
    cronSettings:
      updateTaskList: true     # 让平台为脚本自动生成/删除定时任务
      scriptsPath: 'ZWZW'      # 脚本位于仓库 ZWZW/ 子目录
      scriptsType: [ py ]      # 仅 Python 文件进任务列表
```

覆盖配置后执行生效：

```bash
arcadia update repo
```

平台会克隆仓库到 `/arcadia/repo/Neo-403_Arcadia_Task/`，扫描 `ZWZW/` 下新增的 `.py` 文件，
为每个文件自动创建一条**系统任务**（命令形如 `arcadia run Neo-403_Arcadia_Task/ZWZW/爱阅书吧_Cookie.py`），
定时规则取文件头部注释里的 cron。之后仓库有文件**新增/删除**时再次 `arcadia update repo`，任务会自动增/删。

### 方式二：面板手动上传（单机，不跟随仓库更新）

把 `ZWZW/*.py` 直接上传到 Arcadia 面板【文件管理 → scripts】或容器 `/arcadia/scripts/` 目录，
在【定时任务】里手动新建任务，命令填 `arcadia run <文件名>.py` 即可（同目录还有同名文件时
Arcadia 有 Node > Python 的优先级，本仓库都是 `.py` 无冲突）。

### 方式三：raw 远程单文件同步

`raw` 用于从**单个文件 URL** 下载脚本到 `/arcadia/raw`（官方文档：https://arcadia.cool/docs/sync/raw），
适合只想要某仓库里一两个脚本、不想整仓克隆的场景。在 `sync.yaml` 的 `raw:` 里追加条目：

```yaml
raw:
  - name: 爱阅书吧            # 任意别名
    url: 'https://raw.githubusercontent.com/Neo-403/Arcadia_Task/main/ZWZW/爱阅书吧_Cookie.py'
    fileName: '爱阅书吧_Cookie.py'   # 可选；不填则取 URL 尾部的文件名
    cronSettings:
      updateTaskList: true    # 同样会按文件头部注释里的 cron 生成任务
```

- 生效命令是 `arcadia update raw`（与 repo 的 `arcadia update repo` 分开）；
- 文件头部同样需要 `# cron: ...` 注释才能控制定时，否则平台随机"每天一次"；
- GitHub 连不上时可用 jsDelivr 加速（更新会因 CDN 缓存有延迟）；
- 删除 `raw` 条目后再 `arcadia update raw`，对应脚本与任务会自动清理，无需手动删。

### 私有仓库 / 网络

- 私有仓库：`isPrivate: true`，按仓库鉴权方式补 `authSettings`（`ssh` 或 `http` + Token，见示例注释）；
- GitHub 不通时：面板配置代理，或 raw 链接改用 jsDelivr CDN。

### 首次导入的坑（重要）

- **首次克隆时就要开着 `updateTaskList: true`**。文档语义：任务只在检测到「文件变动」时生成，
  首次克隆后才改配置开关是无效的。
- 如果之前已按错误配置克隆过，需要重灌：控制面板【定时任务】按该仓库过滤→全选→批量删除，
  再删除 `/arcadia/repo/Neo-403_Arcadia_Task` 目录，最后重新 `arcadia update repo`。
- `scriptsPath` 只精确匹配一层目录，多目录用空格分隔（如 `'ZWZW scripts'`），不含子目录递归。
  本仓库脚本都在 `ZWZW/` 顶层，单值即可。
- **改 `whiteList`/`blackList` 不影响已建好的任务**：过滤规则只在文件变动事件里生效，
  因此被过滤掉的多余任务需要到面板手动删除。
- 想彻底移除本仓库（不是重灌，而是不再使用）：① 从 sync.yaml 删掉该 `repo` 条目 →
  ② 面板按仓库过滤批量删除任务 → ③ 删除 `/arcadia/repo/Neo-403_Arcadia_Task` 目录，三步缺一不可。

---

## 维护手册（新增 / 改名 / 删除 / 改时间）

> 一句话记住链路：**`arcadia/tasks.json`（人改，唯一数据源） → `tools/生成定时头.py`（写进脚本头部） → Arcadia 读脚本头部的第一条 cron**。
> `arcadia/tasks.state.json` 只是"正文指纹 + 更新时间"缓存，工具自动维护，不用手改（详见下文 FAQ）。
> 三个文件里，**Arcadia 平台真正读的只有脚本头部注释**。

### 1. 新增站点脚本

**A. 有 QD 模板（推荐，全自动）**

```bash
python QD转Python.py 站点.har                      # 生成脚本 + 自动登记到导入列表
python tools/生成定时头.py                # 把 cron 写进脚本头部
git add -A && git commit -m "新增站点：xxx" && git push
arcadia update repo                                # 容器内执行，平台为新文件建任务
```

`QD转Python.py` 会按鉴权模式自动命名（`_账号.py` / `_Cookie.py`），并**自动写入 `arcadia/tasks.json`**：

| 情况 | 行为 |
|---|---|
| 列表里没有该脚本 | 新增条目，cron 在**时间窗**内自动分配（默认 7-21 点、每 5 分钟一档，跳过已被占用的槽位） |
| 列表里已有 | 覆盖条目；未给 `--cron` 时**沿用原 cron**，不打乱现有排班 |
| 找不到列表文件 | 只提示并跳过导入，不新建、不报错（方便在仓库外单独使用） |

可选参数：`--cron "10 8 * * *"` 指定时间、`--tasks <path>` 换列表、`--no-import` 只生成不登记、
`--slot-range 8-23` 调整自动分配的时间窗（也可用环境变量 `QD_SLOT_RANGE`）。

**B. 手写脚本**

1. 把 `.py` 放进 `ZWZW/`；
2. 在 `arcadia/tasks.json` 加一行 `"文件名.py": "分 时 * * *"`（不加也能跑，但会用**派生时刻**，见文末说明）；
3. 跑 `python tools/生成定时头.py`；
4. 提交推送后 `arcadia update repo`。

### 2. 脚本改名 / 删除

改名会让 `tasks.json` 的键失效（生成器把该脚本当成"未配置"而改用派生时刻），所以**改文件名必须同步改键**：

```bash
# 1) 用 git mv 改名，保留历史
git mv ZWZW/旧名.py ZWZW/新名.py

# 2) 同步导入列表与状态文件的键（两处都要改）
#    arcadia/tasks.json       : 旧名.py -> 新名.py（cron 值保持不变）
#    arcadia/tasks.state.json : 旧名.py -> 新名.py

# 3) 刷新脚本头部并自检（状态列应为"不变"或"更新"，cron 不应突变）
python tools/生成定时头.py --dry-run
python tools/生成定时头.py
```

删除同理：删除 `ZWZW/xxx.py` 后，从 `tasks.json` 删掉该键（`tasks.state.json` 会在下次运行生成器时自动清掉残留键），推送后 `arcadia update repo` 会自动删除平台上的任务。

### 3. 改执行时间

```bash
# 1) 改 arcadia/tasks.json 里对应的 cron（分 时 * * *，5 段）
# 2) 刷新脚本头部
python tools/生成定时头.py
# 3) 推送；已存在的任务不会自动变，需二选一：
#    面板【定时任务】直接改该任务的 cron，或删除该任务后重跑 arcadia update repo
```

### 4. 时间窗（自动分配 / 派生时刻）

两个脚本都有"生成时间落在哪个区间"的概念，默认 **7-21 点**、每 5 分钟一档：

| 场景 | 位置 | 调整方式 |
|---|---|---|
| 转换脚本自动分配槽位 | `QD转Python.py` | `--slot-range 8-23` 或 `QD_SLOT_RANGE=8-23` |
| 未配置脚本的派生时刻 | `tools/生成定时头.py` | `--slot-range 8-23` |

- 支持跨天写法，如 `22-6`（晚上 22 点到次日 6 点）；
- 窗口内排满时自动退化为全天找空位；
- **时刻按"运行 cron 的环境的时区"理解**：Arcadia 容器若是 UTC，而你想在北京时间 7-21 点执行，就填 `23-13`。

### 5. 每次改动的收尾三连

```bash
python tools/生成定时头.py   # 刷新头部 cron 与"更新时间"
git add -A && git commit -m "..."  && git push
arcadia update repo                   # 或面板【代码同步】更新
```

---

## 定时任务是如何"自动生成"的

Arcadia 对"新出现的代码文件"会做一次解析（`shell/utils/resolve.sh` → 后端 cron `updateAll`）：

1. 从头扫描文件内容，取**第一个** 5 段 / 6 段标准 Cron 表达式；
2. 校验通过 → 按该表达式创建系统任务，任务命令自动拼成 `arcadia run <相对路径>`；
3. 文件里没有（或校验失败）→ 随机生成一个"每天执行一次"的时间。

因此**本仓库在每个脚本第 2~5 行固定维护了一组注释**：

```python
#!/usr/bin/env python3
# [Arcadia-AutoCron-BEGIN]
# 更新时间: 2026-09-07
# cron: 5 7 * * *          # 即每天 07:05 执行
# [Arcadia-AutoCron-END]
```

这保证：脚本被 Arcadia 扫描时命中的第一条 cron 一定是我们想要的规则，定时任务就按预期时间自动生成，
而不是平台随机时间。注释块由 `tools/生成定时头.py` 统一维护，请勿手改（改了会被工具覆盖）。

### 每个脚本的更新时间

块中的 `更新时间` 即脚本最近一次"被纳入/改动"的日期，运行生成器时会在表格里一并显示，
方便你一眼看出哪些脚本是最近动过的、哪些可以忽略：

| 生成器状态 | 含义 |
|---|---|
| 新增 | 脚本首次被纳入管理，日期写入运行当天 |
| 更新时间 | 旧注释块补齐"更新时间"行（历史格式升级） |
| 更新 | 脚本正文（不含本注释块）相对上次记录发生变化 |
| 更新(cron) | 该脚本在 `tasks.json` 里的 cron 发生了变化 |
| 不变 | 无任何变化，文件不重写、日期保留 |

"正文变化"通过 [`arcadia/tasks.state.json`](./arcadia/tasks.state.json) 里保存的正文指纹
（去掉本注释块后的 SHA-256）判断，所以平时改脚本逻辑/补站点参数后跑一次生成器，日期就会自动刷成当天；
没有实际变化时反复运行也不会产生无谓改动，便于和 `tasks.json`、脚本内容一起入库维护。

### 每个脚本的时间表

以 [`arcadia/tasks.json`](./arcadia/tasks.json) 为准（键为文件名，值为 5 段 cron），当前默认：

| cron（每天） | 脚本 |
|---|---|
| 07:00 | 木木云盘搜_账号.py |
| 07:05 | 爱阅书吧_Cookie.py |
| 07:10 | 爱桌游_账号.py |
| 07:15 | 边界AI_账号.py |
| 07:20 | 单机Game怀旧馆_账号.py |
| 07:25 | 飞牛私有云社区_Cookie.py |
| 07:30 | 父子被窝_账号.py |
| 07:35 | 宽带技术网_Cookie.py |
| 07:40 | 流星社区_账号.py |
| 07:45 | 梦楠分享_账户.py |
| 07:50 | 七日杀中文网_账号.py |
| 07:55 | 数码之家_Cookie.py |
| 08:00 | 柚坛社区_账号.py |
| 08:05 | 再漫画_账号.py |
| 08:10 | NS中文网_账号.py |
| 08:15 | Switch520_账号.py |
| 08:20 | 咸鱼单机_账号.py |
| 08:25 | Godlike_账号.py |

### 改时间 / 新增脚本

```bash
# 1) 修改 arcadia/tasks.json 里的时间，或把 tasks.example.json 复制成 tasks.json 后编辑
# 2) 让改动写进每个脚本的注释头（会自动刷新该脚本的"更新时间"并打印结果）
python tools/生成定时头.py        # 幂等，可重复执行；--dry-run 只预览
```

然后按上文"日常更新"把改动推到远端并在 Arcadia 执行更新。

**重要语义**：Arcadia 只在文件**新增/删除**时同步系统任务；已经建好的任务不会因脚本注释时间改变而自动
变化。首次接入请先定好时间；之后若改了 `tasks.json`，对**已存在**的任务可：
- 面板【定时任务】里直接改该任务的 cron，或
- 删除对应任务后重新 `arcadia update repo`（该文件重新算"新增"），或
- 删除该仓库目录整体重灌（会重置为配置中的规则）。

### 任务启用策略

[`arcadia/sync.yaml`](./arcadia/sync.yaml) 里提供了 `cronSettings.autoDisable`
（默认 `false`=新任务直接启用；`true`=新任务先禁用，便于你到面板逐个确认后再开）。
需要"登记但不立刻跑"时把它改为 `true`。

---

## 日常更新流程

```text
1. git pull                         # 拉取仓库最新脚本
2. python tools/生成定时头.py   # 同步 cron 与"更新时间"注释（正文或时间有变会自动刷新当天并显示）
3. git add/commit/push              # 提交并推送（含 arcadia/tasks.state.json 指纹记录）
4. 面板【环境配置 → 代码同步】→ 更新（或 arcadia update repo）
```

之后：新增脚本 → 自动建任务；删除脚本 → 自动删任务；失败会通过 addNotify/delNotify 推送提醒。

## 常见问题

- **Q：面板里没生成任务？** 检查 `cronSettings.updateTaskList` 是否 `true`、`scriptsPath` 是否指向 `ZWZW`；
  若是首次克隆后才开的开关，按上文"首次导入的坑"重灌一次。
- **Q：任务命令怎么写？** 由 Arcadia 自动生成的系统任务命令是 `arcadia run <仓库目录>/ZWZW/xxx.py`，
  无需手写。
- **Q：脚本执行报缺库？** 在面板【环境配置 → 运行环境/依赖管理】安装 `curl_cffi`（`梦楠分享.py` 必需，
  其余脚本开了 TLS 拟真时也需要）。
- **Q：`arcadia/tasks.json` 和 `arcadia/tasks.state.json` 有什么区别？哪个真的影响执行时间？**
  两个文件**都只被 `tools/生成定时头.py` 使用**，Arcadia 平台本身都不读。
  - `tasks.json`：**人工维护**，键=文件名、值=cron，是定时规则的**唯一数据源**，改它就是改时间；
  - `tasks.state.json`：**工具自动生成**，存 `正文 SHA-256 指纹 / cron / 更新时间`，
    只用来判断"脚本正文是否变过 → 要不要把 `更新时间` 刷成今天"，对执行时间**没有任何影响**
    （删掉它最坏的结果是下次跑生成器时把所有脚本的"更新时间"刷成今天）。
  - 平台真正读的是**脚本头部注释块里的第一条 cron**，所以改完 `tasks.json` **必须跑一次生成器**才会生效。
- **Q：为什么某个脚本的时间突然变成乱七八糟的时刻？** 说明 `tasks.json` 里**没有这个文件名对应的键**
  （常见于改名后没同步键）：生成器会退化为"由文件名稳定派生"的时刻。把键补上再跑生成器即可恢复。
- **Q：新增站点脚本？** 有 QD 模板就用转换脚本：`python QD转Python.py 站点.har`，它会生成脚本并
  **自动登记到导入列表 `arcadia/tasks.json`**（已有同名条目覆盖 cron、新条目自动分配下一个 5 分钟槽位；
  找不到该列表文件则跳过导入、不新建）；手写脚本则自己往 `arcadia/tasks.json` 加一行时间。
  最后跑一次 `python tools/生成定时头.py` 把 cron 写进脚本头部，推送即可。
  可用 `--cron "10 8 * * *"` 指定时间、`--tasks <path>` 换列表路径、`--no-import` 只生成不登记。
- **Q：没填账号/Cookie，任务却显示成功？** 已修复。脚本有三层判定：① 凭证是 `xxx`/空值等占位值 →
  直接报错不发请求；② 响应里出现"请先登录/未登录"等文案 → 判失败；③ 汇总数据全空 → 判失败
  （站点确实不返回数据时用 `<SITE_TAG>_STRICT_EMPTY=0` 关闭）。
- **Q：`curl_cffi` 装不上/不想自动装？** 自动安装只在依赖缺失且功能被启用时触发，可用
  `<SITE_TAG>_AUTO_INSTALL=0` 关闭，然后手工 `pip install curl_cffi`。
- **Q：`arcadia update repo` 时提示 `ReferenceError: require is not defined in ES module scope`（notify.js）？**
  这是平台自身在 Node 新版下的兼容问题（Arcadia 声明了 `"type": "module"`，但 `src/utils/notify.js` 仍用
  CommonJS `require`），只发生在**发送通知**环节。任务本身已处理完成——从日志能看到每个文件
  "添加成功✅"、结束行有"更新定时任务完成"，到面板【定时任务】确认即可；通知收不到不影响任务。
  该问题与仓库配置无关，需等平台侧修复（或在部署侧调整 Node 运行时），无需改动 sync.yaml。
- **Q：raw 同步时提示缺依赖文件？** `raw` 目录只保留已配置的脚本，额外的依赖文件会被清理；
  需用 sync.yaml 里 `global.rawDependencyFilter` 的正则把它们保留下来（用 `|` 分隔多表达式）。

---

## 参考资料（官方文档）

- 代码仓库（`repo` 段字段 / 过滤规则 / 删除与重灌步骤）：https://arcadia.cool/docs/sync/repo
- 代码文件（`raw` 段字段 / `fileName` / 生效命令）：https://arcadia.cool/docs/sync/raw
- Arcadia 项目源码：https://github.com/SuperManito/Arcadia
