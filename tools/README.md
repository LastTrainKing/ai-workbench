# tools/

随源码分发的验收与安全工具。**这些不是可选脚本** —— 它们是"这个仓库能自证"的部分，
所以进了版本控制（它们原先在 `build/rc/`，而 `build/` 被 gitignore 覆盖，
干净克隆里没有它们）。

## 安全

| 工具 | 作用 |
|---|---|
| `scan_for_secrets.py` | 扫**已跟踪文件的当前内容**：`.env`、配置模板、源码注释里的真实凭据 |
| `scan_git_history_secrets.py` | 扫**每一个 Git blob**（含已删除的文件）。只扫工作树是不够的 —— 一个被提交过一次、后来删掉的密钥仍在历史里，仍可被 clone 出来 |

```bash
python tools/scan_for_secrets.py
python tools/scan_git_history_secrets.py
```

两个都会在**什么都没扫到**时返回非 0，而不是报"干净"。一个什么都没查的绿色结论，
比没有结论更危险 —— 这个项目为此吃过一次亏（扫描器把 `bytes` 套了 `str()`，
得到字面量 `b'...'`，按换行切分只得一行，于是扫了 0 个对象却报"历史干净"）。

已知假阳性：测试夹具里刻意写成真凭据形状的字符串（`sk-…` / `AKIA…`）。
它们**必须**像真的，否则测不出"应该拒绝"。人工复核后放行，见
`docs/release/SECURITY_RELEASE_CHECK.md`。

## 界面验收

| 工具 | 作用 |
|---|---|
| `check_qml_pages.py` | 20 个页面逐个无头加载，打印引擎自己的报错。**语法与加载**这一层 |
| `check_layout.py` | 19 页 × 3 档分辨率（1920×1080 / 1280×800 / 960×640）遍历控件树，报四类几何问题：文字被硬切、子元素越界、文字重叠、可见但尺寸为零 |
| `shot_real_pages.py` | **真实显示器**上逐页截图。用真实窗口、真实字体栈，产出人能看的 PNG |

```bash
python tools/check_qml_pages.py
python tools/check_layout.py
python tools/shot_real_pages.py                    # 需要真实显示器
python tools/shot_real_pages.py employees memory   # 只截这几页
```

**`shot_real_pages.py` 与另外两个不是同一件事，而且它补的正是另外两个补不了的。**
`check_layout.py` 答的是"几何对不对"，`check_qml_pages.py` 答的是"能不能加载"，
**两个都不答"字形画出来了吗"** —— 它们跑在 offscreen 平台上，而该平台在本机报告
*零个字体族*。这个项目已经因此漏过一次：员工头像首字改成 emoji，offscreen 报告渲染
干净，真实窗口画的是豆腐块。

**这三个答不了"页面渲染的内容与数据是否一致"。** 那是
`desktop-native/verify_projection.py` 的事，它读的是真实 QML 对象的 `isVisible()` 与
`text`。分工是刻意的：几何 / 加载 / 字形 / 内容，四层各自有工具，因为它们各自出过错。

## 需要真实基础设施的验收（不在本目录）

| 工具 | 需要什么 |
|---|---|
| `desktop-native/verify_native_shell.py` | 无头 Qt；31 项导航 / 快捷键 / 无障碍 |
| `desktop-native/verify_bridge_employees.py` | PostgreSQL |
| `desktop-native/verify_employee_delivery.py` | PostgreSQL + **真实模型 Key**（会产生费用） |
| `desktop-native/verify_live_acceptance.py` | 同上 |
| `backend/tests/` | PostgreSQL + Redis + Qdrant |

完整命令与最近一次的真实结果见 `docs/release/TEST_REPORT.md`。
