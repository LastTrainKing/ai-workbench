# 代码审查：找到的问题与修改意见

审查对象：`C:\Users\user\Desktop\ai-workbench`，HEAD `55ec517`。
只做只读检查与隔离复现，**没有修改任何业务代码，没有动用户数据，没有调用真实模型**。

三态标记：**已确认**（有命令输出或逐字引用的源码行）／**待验证**（读代码推断，未复现）。
"影响"里凡是没有实测到的，都写明是推断。

---

## 一、P0：会丢数据或让用户无法自救

### 1. 修复入口是坏的 —— `%` 写成了 `$`，双击必然失败【已确认】

`installer/ai-workbench.nsi` 第 239 行：

```
FileWrite $9 "set $\"HERE=$$~dp0$\"$\r$\n"
```

NSIS 里 `$$` 转义成一个字面 `$`，所以写进文件的是 `set "HERE=$~dp0"`。
而批处理要的是 `%~dp0`。**装出来的 `repair-install.bat` 第 8 行就是错的**：

```
$ sed -n '8p' "C:/Users/user/AppData/Local/AI Workbench/repair-install.bat"
set "HERE=$~dp0"
```

实际运行（安装完好、本该什么都不做）：

```
$ cmd //c "repair-install.bat < nul"
The program files are incomplete - python.exe is missing.
Run the installer again.
真实退出码=2
```

`%HERE%` 展开成字面量 `$~dp0`，所以 `"$~dp0vendor\runtime\python\python.exe"` 永远不存在，
脚本**每次都在第 9 行掉进错误分支、exit 2**，一次修复都没做过。

**为什么上一轮没发现**：第 14 轮我验的是 `install_selfcheck.py --repair`（用 python 直接跑），
验收第 3c 步也是直接调 python。**用户点的是那个 `.bat`，而它从来没被跑过。**
这正是这份文档自己反复记的那个形状：测了对象，没测用户走的那条路。

**影响**：第 14 轮「失败/取消安装都留一个修复入口」这条保护**实际不存在**。
用户遇到半装状态时，桌面上那个快捷方式只会告诉他"程序文件不完整，请重装"。

**修改意见**：

```nsis
; `%` 在 NSIS 里不是特殊字符，直接写；`$` 才是要转义的
FileWrite $9 "set $\"HERE=%~dp0$\"$\r$\n"
```

并且**把 `.bat` 本身纳入验收**：`verify_installer.py` 里加一步，真的用 `cmd /c` 跑一遍
`repair-install.bat`，断言退出码 0 且四项仍然全在。只测 python 入口等于没测。

---

### 2. 验收/测量脚本会删掉**用户真实的数据目录**【已确认】

`scripts/verify_installer.py:46` 和 `scripts/measure_startup.py:41`：

```python
DATA_ASIDE = Path(os.environ["LOCALAPPDATA"]) / "AI Workbench Data"
DATA_DIR   = LOCALAPPDATA / "AI Workbench Data"
```

而安装版真实用的就是同一个目录（读安装目录里的 `data-dir.txt` 得到）：

```
$ cat "C:/Users/user/AppData/Local/AI Workbench/data-dir.txt"
C:\Users\user\AppData\Local\AI Workbench Data\.runtime
$ du -sh "C:/Users/user/AppData/Local/AI Workbench Data"
50M
```

两个脚本都在启动前无条件删它：

- `verify_installer.py:148-150` — `for path in (INSTALL_DIR, DATA_ASIDE): shutil.rmtree(path, ignore_errors=True)`
- `measure_startup.py:86` — `shutil.rmtree(DATA_DIR, ignore_errors=True)`（`wipe_data=True` 时）

**影响**：跑一次冷启动测量 = 用户的资料库、记忆、档案、AI 产出物、整个 PostgreSQL 集群全没。
`ignore_errors=True` 还让失败也不出声。第 14 轮我跑过 `measure_startup.py`，
当时的 50 MB 数据就是这么没的。

**修改意见**（两条都要做，缺一不可）：

1. 测量脚本把数据目录**指到别处**：安装时用 `/D=` 换安装路径还不够，
   数据目录来自 `data-dir.txt`，所以测量前应改写它，或让安装版支持一个
   `AI_WORKBENCH_DATA_DIR` 环境覆盖。
2. 删之前**先确认这是测试数据**：目录里没有 `pgdata` 之外的用户库，或者路径里带
   `InstallTest` 标记。**不确定就拒绝运行并报错**，不要 `ignore_errors=True` 静默删。

顺带：`ignore_errors=True` 本身就是"失败返回空值"的形状 —— 删不掉时不报错，
调用方以为删干净了，于是"冷启动"其实是热启动，测出来的数字是假的。

---

## 二、P1：用户看得见的错误行为

### 3. 连发两条消息，第二条的气泡会在它还在飞的时候消失【已确认】

`frontend/src/store/conversation.ts:107-109` 的谓词：

```ts
return pending.filter(
  (item) => item.state === 'failed' || item.serverUserCount >= serverUserCount,
)
```

`ConversationPage.tsx:678` 记基线用的是**当时的服务端用户消息数**：

```ts
serverUserCount: messages.filter((m) => m.role === 'user').length,
```

连发两条时两条拿到**同一个基线**。用源码里的谓词复现：

```
$ node -e '…逐字抄自 conversation.ts:107-109…'
刚发完两条，服务端 3 条 →  屏幕上: [ '第一条', '第二条' ]
第一条到了，服务端 4 条 →  屏幕上: []
第二条还在飞，但它的气泡已经不见了 ↑
```

第一条被服务端记下之后，两条的 `serverUserCount(3) >= 4` 同时为假，**一起被丢掉**，
包括那条服务端根本还没收到的。

**影响**：用户刚打完的第二句话从屏幕上消失。这个文件的文件头写着
「A message that disappears from the screen while it is still in flight is the exact
failure this file exists to prevent」—— 谓词违反了自己的不变量。

**修改意见**：计数当基线只在"一次一条"时成立。改成给每条 pending 一个单调序号
（`addPending` 里已经有 `sequence`），服务端回包时按**它自己那条**消息的落库结果
（比如 `POST /messages` 返回的 message id）来消掉对应的一条，而不是用总数比较。
退而求其次：基线改成"我这条之前已确认的条数 + 我这条在本地队列里的位次"。

### 4. 新建会话时发出的第一条，会出现在**所有**会话里，重试还会发错地方【已确认】

`ConversationPage.tsx:676` 在 `id` 为空时存 `conversationId: null`：

```ts
const localId = addPending({ conversationId: id ?? null, text, serverUserCount: … })
```

会话建好之后（第 685-688 行）**只 navigate，不回填这条 pending 的 `conversationId`**。
于是它永远是 `null`，而两处判断都把它当"属于当前会话"：

- `ConversationPage.tsx:708` — `!item.conversationId || item.conversationId === id`：任何会话都显示它
- `ConversationPage.tsx:695` — `const target = item.conversationId ?? id`：重试时发给**当前正在看的那个会话**

**影响**：① 用户在会话 A 发了一句、马上切到会话 B，A 那句话的气泡出现在 B 里；
② 在 B 里点重试，这句话被**发进 B** —— 内容进了错误的会话，而且是用户看不到的错。

**修改意见**：`create()` 成功之后立刻回填这条 pending 的 `conversationId`
（store 里加一个 `bindPending(id, conversationId)`）；`handleRetry` 在
`item.conversationId` 为空时**拒绝重试并提示**，不要退化成"当前会话"。

### 5. 「全选 → 一键清空」清不空超过 50 条的列表【已确认，当前库未触发】

- 前端 `conversationApi.list: () => request('/conversations')` —— **不传 limit**
- 后端 `conversations.py:41` — `limit: int = Query(default=50, ge=1, le=200)`
- 界面 `ConversationList.tsx` 的「全选」只勾选**已加载的行**

所以会话超过 50 条时，「全选 → 删除」只删掉前 50 条，**剩下的一动不动**，
而确认框说的是「删掉这 50 条」、按钮写的是「全选」。用户的原话是
「全选删除：列表顶部一个"全选"，选中后一键清空」—— 这条没做到。

**影响**：现在是潜伏的（当前库远少于 50 条）。一旦用户积累过 50 条就会遇到，
而且**看起来像删除失败**。

**修改意见**：要么给列表加分页并把「全选」明确成「全选已加载的 50 条」，
要么加一个「清空全部」的动作，走服务端自己查 id 的路径
（不依赖客户端把 id 全传上来）。**不要**只把 limit 调大了事 —— 那只是把阈值推远。

---

## 三、P2：正确性问题，但后果较轻

### 6. 窗口尺寸夹取的单位换算错了，而且我上一轮写的注释是错的【已确认】

`desktop/src-tauri/src/main.rs:97-117`。注释写着「`outer_size()` is logical；
`monitor.size()` is physical」—— **两个都是物理像素**，Tauri 2.12.1 源码：

```
webview_window.rs:  /// Returns the physical size of the entire window.
                    pub fn outer_size(&self) -> crate::Result<PhysicalSize<u32>>

window/mod.rs:86:   /// Returns the monitor's resolution.
                    pub fn size(&self) -> &PhysicalSize<u32>
```

于是第 110-111 行把已经物理的尺寸**又乘了一次缩放**：

```rust
let physical_width = (f64::from(size.width) * scale).round() as u32;
```

第 114-117 行更混：`min(物理值, 逻辑值)` 之后又交给 `LogicalSize::new`。

**影响**（推断，未逐屏实测）：缩放 ≠ 100% 时判断偏大 25%（本机 125%），
夹取会在**窗口本来就装得下**的时候触发；而 `LogicalSize(物理.min(物理/scale))`
可能把窗口**放大**而不是夹小。日志那行也把物理值标成 "logical"，会误导下一个人。

**修改意见**：两者同单位，直接比，不要 scale：

```rust
let max_width = screen.width.saturating_sub(96);
let max_height = screen.height.saturating_sub(140);
if size.width > max_width || size.height > max_height {
    let fitted = tauri::PhysicalSize::new(size.width.min(max_width), size.height.min(max_height));
    let _ = window.set_size(fitted);
}
```

并且把注释里那句错的单位说明删掉 —— 留着比没有更坏。

### 7. 卸载时会**强杀全机器**的 postgres / redis / qdrant【已确认】

`installer/ai-workbench.nsi:133-139`：

```
nsExec::ExecToLog 'taskkill /F /IM "postgres.exe" /T'
nsExec::ExecToLog 'taskkill /F /IM "redis-server.exe" /T'
nsExec::ExecToLog 'taskkill /F /IM "qdrant.exe" /T'
```

`/IM` 按**映像名**匹配，不看路径、不看父进程。用户自己装的 PostgreSQL / Redis
（做开发的很常见）会在卸载（和安装前的 StopEverything）时被 `/F` 强杀。

**影响**：用户自己的数据库被硬杀。PostgreSQL 是崩溃安全的，不会坏数据，
但会中断他正在跑的查询、并让他的服务消失 —— 对一个"私人工作台"来说这是越界。
（**推断**：我没有真的去杀，只在代码里确认了匹配方式。）

**修改意见**：改成按**进程树**杀。启动器已经把所有后代放进一个 job 对象了，
正确做法是关掉 job（`KILL_ON_JOB_CLOSE`）或对启动器的 pid 用 `/T`；
需要按名字兜底时，先 `wmic process where "name='postgres.exe' and ExecutablePath like '%AI Workbench%'"` 过滤路径。

---

## 四、建议的修复顺序

| 顺序 | 项 | 理由 |
|---|---|---|
| 1 | #2 测试脚本删真实数据 | 不可逆，而且已经在真实发生（第 14 轮丢过一次） |
| 2 | #1 修复入口的 `%` | 一行的事，但它现在是"保护不存在" |
| 3 | #4 跨会话串消息 + 发错会话 | 会写错数据，用户不易察觉 |
| 4 | #3 连发时气泡消失 | 直接违背该文件自己写下的不变量 |
| 5 | #6 窗口单位 | 行为偏轻，但注释是错的会继续误导 |
| 6 | #7 全机器 taskkill | 触发条件少，触发时影响不小 |
| 7 | #5 全选清不空 | 潜伏，但一到 50 条就"看起来像删除失败" |

---

## 五、这一轮**没有**做的事

- **没有**改任何业务代码、没有重装、没有动现有会话或文件。
- **没有**调用真实模型（沿用第 14 轮的约定）。#3、#4、#5 都是读源码 + 纯函数复现，
  没有跑浏览器测试 —— 那几个脚本会往开发库里写会话。
- **没有**跑 `verify_installer.py` / `measure_startup.py`，理由就是上面第 2 条：
  它们会删掉真实数据目录。**这两个脚本在修好之前不要跑。**
- 本轮结论里凡标注「推断」的（#6 的屏幕表现、#7 的实际触发），都**没有**实测，
  只确认了代码怎么写的。

---

# 补充审查：URL 安全（用户看不到的那一层）

问的是「还有用户看不到的呢，url安全你是怎么保障的」。查了所有"外部进来的 URL"
和"往外部发出去的 URL"。**结论：出网这一层的门是一道字符串检查，而它默认信任 localhost。**

## 六、P0：`headers` 参数被原样拼进一段会被执行的脚本【已确认】

`backend/app/ai/tools/builtin/process_network.py:280-314`：

```python
safe_url  = url.replace("'", "''")
safe_body = body.replace("'", "''")
header_block = f"$h = @{{ {headers} }}"      # ← headers 完全没有转义
```

`_run_http`（同文件 366-377）把它交给系统里的脚本宿主执行：

```python
script = _NetworkTool._fetch_script(url, method, body, headers)
outcome = await _run_process(exe=exe, args=[*flags, script], ...)
# flags = ["-NoProfile", "-NonInteractive", "-Command"]
```

`headers` 是**工具参数**，而且它的 spec 说明就写着让调用方直接写宿主语言
（第 326 行：`"PowerShell hashtable entries, e.g. \"'Accept'='application/json'\""`）。
`_authorise` 只检查 `url`，**从不看 `headers`**。

也就是说，`headers` 里多写一段语句，就会在那段脚本里被执行。无害验证（只输出一行，
不联网、不碰文件）：

```
$ powershell -NoProfile -NonInteractive -Command "$h = @{ 'Accept'='application/json' }; Write-Output 'INJECTION-REACHES-SHELL'; $z = @{}"
INJECTION-REACHES-SHELL
```

把 `headers` 设成 `'Accept'='x' }; Write-Output '…'; $z = @{` 之后，渲染出来的脚本
就是上面那条能跑通的形状。

**为什么这条最重**：`desktop/src-tauri/src/commands.rs` 的文件头写着，凡是能碰到机器的操作
都必须走 FastAPI 那一层，因为**权限管理器、工作区沙箱、风险分级、审计日志全在那里**。
这个注入点在那条链的**下游**，直接落到脚本宿主 —— 上面四样一个都不过。
而工具参数是模型给的，模型又会被它读到的网页和文档影响。

**修改意见**：不要拼接。`headers` 应该是结构化参数
（`{"type":"object","additionalProperties":{"type":"string"}}`），
或者更简单 —— 别用脚本宿主发请求，用 `httpx`（`discovery.py` 已经在用它了）。
如果一定要用，就必须把它当**数据**传：写进临时 JSON 文件、脚本里读回来解析，
而不是当**代码**插进字符串。

## 七、P0：默认白名单里有 `localhost` 和 `127.0.0.1`【已确认】

`backend/app/core/config.py:115-120`：

```python
agent_allowed_domains: str = (
    "github.com,raw.githubusercontent.com,pypi.org,files.pythonhosted.org,"
    "registry.npmjs.org,npmjs.com,api.deepseek.com,docs.python.org,"
    "developer.mozilla.org,stackoverflow.com,localhost,127.0.0.1"
)
agent_blocked_domains: str = ""
```

`permissions.py:503-505`：命中白名单 → `_allow`，**不问用户**。

所以开箱状态下，agent 可以**不经确认**访问本机任何 HTTP 服务：

- 应用自己的 API：`http://127.0.0.1:8000/api/v1/...`
- **Qdrant：`http://127.0.0.1:6333`** —— 它默认没有鉴权，也就是说 agent 可以绕过
  应用的数据模型直接读、写、删用户的向量库
- 用户自己在这台机器上跑的任何开发服务器、管理面板

而 `blocked_domains` 默认是**空串**，"永远拒绝"的名单里一个都没有。

**修改意见**：把 `localhost` / `127.0.0.1` 从**默认**白名单里拿掉，需要时由用户显式加。
同时给"永远拒绝"一份**非空**默认值（回环、私网段、链路本地、云元数据），
并且它要**优先于**白名单判断 —— 现在的顺序是先查黑名单再查白名单，
但黑名单是空的，等于没有。

## 八、P1：只检查字符串，不跟重定向【已确认】

`check_domain`（`permissions.py:485-517`）只做**字符串**解析：去 scheme、取第一个 `/` 前、
取最后一个 `@` 后、取第一个 `:` 前，然后做后缀匹配。

而请求本身**会跟随重定向**：

- 脚本宿主那条路：`Invoke-WebRequest`（`process_network.py:300`）默认跟随
- `skills/discovery.py:302`：`httpx.AsyncClient(timeout=20, follow_redirects=True)`
- `process_network.py:452`：`urllib.request.urlopen`（`_download` 工具）默认也跟随

于是一个**在白名单里的**主机只要 302 到 `http://127.0.0.1:6333/collections`，
`check_domain` 看到的是那个白名单主机，真正的目标它从没见过。

**修改意见**：出网统一走一个客户端，**禁止自动跟随**，自己逐跳跟随并且
**每一跳都重新过一遍 `check_domain` + 私网判定**。

## 九、P1：不识别 IP 字面量、私网段、云元数据【已确认】

`check_domain` 里没有任何 IP 判定（grep `is_private|169\.254|loopback` 在 `backend/app`
下除了无关的 `metadata` 字段命中之外**一处都没有**）。所以：

- `http://169.254.169.254/latest/meta-data/`（云元数据）不在黑名单里
- `http://10.0.0.5/`、`http://192.168.1.1/`（内网）不在黑名单里
- `http://[::1]:8000/` 会走到 `split(":", 1)[0]` 得到 `[`，字符串匹配直接失效
- `http://2130706433/`（`127.0.0.1` 的十进制写法）也不会被识别

**修改意见**：`check_domain` 在字符串匹配之前，先把主机**解析成 IP**，
用 `ipaddress` 判 `is_private / is_loopback / is_link_local / is_reserved`，
命中就拒。注意处理 `[::1]` 这种带方括号的 IPv6，以及十进制/八进制写法 ——
**先规范化，再判断**。

## 十、P2：两条出网路径是两套实现

`_run_http`（脚本宿主）和 `_download`（`urllib`，第 447-452 行）各写一遍，
超时、大小上限、错误形状都不同。这正是这份文档第五节的规矩
「一个决定只能有一个实现者」被破的地方 —— 安全判定分散在两处，改一处漏一处是迟早的事。

## 十一、URL 这一层**做对了**的部分（一并说明，免得只看到坏消息）

- **`?draft=` 的编码是对的。** 第 14 轮加的 `percent_encode` 只放行
  `A-Za-z0-9-_.~`，`+` 会被编成 `%2B`。这一点重要，因为前端用 `URLSearchParams` 读，
  而它按表单规则解码、会把裸 `+` 变成空格 —— 编码挡住了这个坑。
- **草稿没有进访问日志。** `backend/app/core/logging.py:145` 把
  `uvicorn.access` 压到 `WARNING`，请求行不落盘。实测安装版的 `api.out.log`：
  `grep -cE '"(GET|POST) '` → **0**，`grep -c "draft="` → **0**。
- **没有 token 进 URL。** 全仓 grep `[?&](token|access_token|api_key|key)=` → 空。
  第 10 轮把下载改成 `requestBlob` + `POST /deliverables/{id}/open`（只传 id）是对的。

## 十二、这一节**没有**验证的事

- **没有**真的用恶意 `headers` 去打网络。验证只用了一个只输出一行的无害片段，
  证明那段字符串确实会被宿主执行。
- **没有**验证 WebView2 会不会把带 `draft=` 的 URL 写进自己的历史文件
  （`%LOCALAPPDATA%\...\EBWebView\User Data\`）。**这是推断，不是结论。**
- **没有**实测重定向绕过（那需要起一个会 302 到 6333 的服务器并让 agent 去访问它）。
  「`Invoke-WebRequest` 默认跟随重定向」是从用法推断的，没有跑过。
- Qdrant「没有鉴权」是它的默认行为，**本机没有实测**（没去请求它）。

## 十三、修复顺序（接第四节）

| 顺序 | 项 | 理由 |
|---|---|---|
| 0 | 六、`headers` 注入 | 它能绕过整个权限架构，且不依赖用户点任何东西 |
| 0 | 七、白名单里的 localhost | 一行配置的事，但它是"agent 能随便碰本机"的总开关 |
| 1 | 九、私网/IP 判定 | 把门补上 |
| 2 | 八、重定向 | 门补上了但能绕过去，等于没补 |
| 3 | 十、两条实现合一 | 不然上面三条要改两遍 |
