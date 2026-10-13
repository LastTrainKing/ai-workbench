"""投影层回归测试 —— 断言页面**渲染出来的内容**与数据一致。

## 为什么需要这个工具

上一轮出现过两次"测试全绿，用户看到错误"：

1. 首页把 `summary.providers.llm` 读成了不存在的 `summary.llm`。写错字段名不抛异常，
   只让 `api_key_set` 永远是 undefined，于是首页对着一个配好 Key 的用户说"未配置 API Key"。
2. 员工名字首字改成图标后，字体回退链缺失，图标渲染成豆腐块。

两次都不是逻辑错误，是**投影错误**：数据对，画错了。而当时仓库里所有设施（
`check_qml_pages.py` 只查能否加载，`verify_native_shell.py` 只查存在性，`check_layout.py`
只查几何）**都答不了"页面渲染的内容是否与数据一致"这个问题**。所以它们全绿，而用户看到假话。

## 它断言什么

**渲染树**，不是文件字符串。每一条断言都读真实 QML 对象的属性：

* `isVisible()` —— 沿父链求有效可见性，所以"存在但被隐藏"不会冒充"显示出来了"
* `text` —— 真实渲染的文本
* 对象的 `property()` —— 绑定求值后的值

工具分两组：

* **纯映射**（`QJSEngine` 直接求值 `theme.js`）：不需要界面，快、确定，覆盖取值与优先级规则。
* **渲染**（无头加载 `Main.qml`）：覆盖"数据 → 屏幕"这一段。

## 用法

    python desktop-native/verify_projection.py            # 全部
    python desktop-native/verify_projection.py contract   # 只跑某组

退出码非 0 表示有断言失败。
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("AI_WORKBENCH_DATA_DIR", r"C:\Users\user\Desktop\ai-workbench\.runtime")
os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://aiworkbench:aiworkbench@127.0.0.1:5432/aiworkbench"
)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "desktop-native"))

from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

app = QGuiApplication(sys.argv[:1] + ["projection"])

from awbnative import resources

resources.configure()

RESULTS: list[tuple[str, str, bool, str]] = []


def check(group: str, label: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((group, label, ok, detail))
    mark = "PASS" if ok else "FAIL"
    line = f"  [{mark}] {label}"
    if detail and not ok:
        line += f" — {detail}"
    print(line, flush=True)


# =========================================================================== #
# 1. 纯映射：直接求值 theme.js，不需要界面
# =========================================================================== #
def run_mapping_checks() -> None:
    from PySide6.QtQml import QJSEngine

    js = QJSEngine()
    source = (REPO / "desktop-native" / "qml" / "theme.js").read_text(encoding="utf-8")
    value = js.evaluate(source, "theme.js")
    if value.isError():
        check("mapping", "theme.js 可求值", False, str(value.toString()))
        return
    theme = js.globalObject().property("Theme")
    if theme.isUndefined():
        # theme.js 定义的是顶层 var，不是对象；用 evaluate 拿到函数本身
        theme = js.evaluate("({contractFor: contractFor, contractField: contractField,"
                            " contractLines: contractLines, hasContract: hasContract,"
                            " contractSections: contractSections, roleGlyph: roleGlyph,"
                            " roleName: roleName})")
    check("mapping", "theme.js 可求值", True)

    def call(expr: str):
        v = js.evaluate(expr)
        return v.toVariant() if hasattr(v, "toVariant") else v

    # ---- 章节定义：12 节，标题与顺序就是任务书 §八 的顺序 ----
    titles = call("contractSections.map(function (s) { return s.title })") or []
    expected = ["岗位", "使命", "职责", "工作方式", "需要的输入", "不做什么",
                "先问你的事", "限制", "验收标准", "完成定义", "产出格式", "产出位置"]
    check("contract", "章节数 = 12", len(titles) == 12, f"实际 {len(titles)}")
    check("contract", "章节标题与顺序正确", list(titles) == expected, str(titles))
    ordered = call("contractSections.filter(function (s) { return s.ordered === true })"
                   ".map(function (s) { return s.key })") or []
    check("contract", "只有 SOP 带编号", list(ordered) == ["sop"], str(ordered))

    # ---- 优先级：员工自己的值优先，缺省回退模板 ----
    tpl = "[{role_id: 'researcher', mission: '模板使命', sop: ['t1', 't2'],"
    tpl += " acceptance: ['a1'], out_of_scope: ['o1']}]"
    js.evaluate(f"var TPL = {tpl};")

    own = ("{role_id: 'researcher', mission: '员工自己的使命', responsibilities: ['r1'],"
           " out_of_scope: []}")
    js.evaluate(f"var OWN = {own};")

    check("contract", "员工有值时优先用员工值",
          call("contractField(OWN, TPL, 'mission')") == "员工自己的使命",
          str(call("contractField(OWN, TPL, 'mission')")))
    check("contract", "员工缺省时回退模板",
          call("contractField(OWN, TPL, 'sop')") == ["t1", "t2"],
          str(call("contractField(OWN, TPL, 'sop')")))
    # 空数组是"这个员工明确没有这一项"，还是"没设过"？当前语义是**回退** ——
    # 与 `contractField` 的实现一致（空数组视为未设置）。断言把它钉住。
    check("contract", "空数组视为未设置并回退模板",
          call("contractField(OWN, TPL, 'out_of_scope')") == ["o1"],
          str(call("contractField(OWN, TPL, 'out_of_scope')")))
    check("contract", "两边都没有时返回 undefined",
          call("contractField(OWN, TPL, 'nonexistent')") is None,
          str(call("contractField(OWN, TPL, 'nonexistent')")))

    # ---- 未知角色：不崩、不编造 ----
    js.evaluate("var GHOST = {role_id: 'no_such_role', mission: '只有使命'};")
    check("contract", "未知角色不崩溃",
          call("contractField(GHOST, TPL, 'mission')") == "只有使命")
    check("contract", "未知角色回退到角色 id 而不是空串",
          call("roleName(contractFor(GHOST, TPL), 'no_such_role')") == "no_such_role",
          str(call("roleName(contractFor(GHOST, TPL), 'no_such_role')")))
    check("contract", "未知角色有安全图标回退",
          isinstance(call("roleGlyph('no_such_role')"), str)
          and call("roleGlyph('no_such_role')") != "")
    check("contract", "空员工不崩溃", call("contractLines(null, TPL, 'mission')") == [])
    # 模板缺失时：员工自己的值仍然给得出，需要模板兜底的那一项则给空 ——
    # **不是崩溃，也不是编造**。这条断言第一次写错了（期望 []），实际返回员工值才是对的。
    check("contract", "无模板时仍能用员工自己的值",
          call("contractLines(OWN, null, 'mission')") == ["员工自己的使命"],
          str(call("contractLines(OWN, null, 'mission')")))
    check("contract", "无模板且员工没设时给空，不编造",
          call("contractLines(OWN, null, 'sop')") == [],
          str(call("contractLines(OWN, null, 'sop')")))

    # ---- 每个已知岗位都有图标 ----
    known = ["assistant", "researcher", "knowledge_manager", "document_expert",
             "software_engineer", "blank"]
    glyphs = [call(f"roleGlyph('{r}')") for r in known]
    check("contract", "6 个岗位都有图标", all(isinstance(g, str) and g for g in glyphs),
          str(glyphs))

    # ---- 形状判断：字典不得被渲染成 [object Object] ----
    #
    # 这条守的是一个真实存在的坑：`String({})` 是 `"[object Object]"`，直接渲染出来就是
    # 一句凭空造出来的话。`Array.isArray` 又对 QML 的类数组返回 false —— 两个坑夹在一起，
    # 一度让两节整节消失（见 `toLines` 的注释）。
    check("declared", "字典不渲染成 [object Object]",
          call("toLines({a: 1})") == [], str(call("toLines({a: 1})")))
    check("declared", "空字典给空",
          call("toLines({})") == [], str(call("toLines({})")))
    check("declared", "字符串给一行",
          call("toLines('abc')") == ["abc"], str(call("toLines('abc')")))
    check("declared", "数字给一行",
          call("toLines(7)") == ["7"], str(call("toLines(7)")))
    check("declared", "null / undefined 给空",
          call("toLines(null)") == [] and call("toLines(undefined)") == [])

    # ---- 中文标签：内部值不得泄漏到界面 ----
    check("declared", "记忆范围有中文标签",
          call("memoryScopeLabel('global')") == "全局记忆"
          and call("memoryScopeLabel('project')") == "项目记忆",
          str(call("memoryScopeLabel('global')")))
    # `user` 不在记忆系统的词表里（evaluator.py:321 只产出 global / project）。
    # 照实翻译并说明，而不是伪装成已知类别 —— 那会掩盖"声明与实现对不上"这个事实。
    check("declared", "词表外的范围照实说明，不伪装成已知类别",
          "记忆系统当前不使用" in call("memoryScopeLabel('user')"),
          str(call("memoryScopeLabel('user')")))
    check("declared", "未知范围有安全回退并带原始值",
          call("memoryScopeLabel('weird')") == "未知范围（weird）",
          str(call("memoryScopeLabel('weird')")))
    check("declared", "模型路由有中文标签",
          call("modelRouteLabel('main')") == "主力模型"
          and call("modelRouteLabel('reasoning')") == "推理模型"
          and call("modelRouteLabel('fast')") == "快速模型",
          str(call("modelRouteLabel('fast')")))
    check("declared", "未知路由有安全回退",
          call("modelRouteLabel('nope')") == "未知路由（nope）",
          str(call("modelRouteLabel('nope')")))

    # ---- 三节的取值 ----
    js.evaluate("var MEM = {read: ['global', 'project'], write: ['global']};")
    mem_lines = call("memoryScopeLines(MEM)") or []
    check("declared", "记忆范围给出可读/可写两行", len(mem_lines) == 2, str(mem_lines))
    check("declared", "记忆范围用「声明」口吻，不写成权限",
          all("声明" in line for line in mem_lines), str(mem_lines))

    js.evaluate("var MODEL = {primary: 'reasoning', light: 'fast'};")
    model_lines = call("modelPolicyLines(MODEL)") or []
    check("declared", "模型策略给出两行", len(model_lines) == 2, str(model_lines))
    check("declared", "模型策略不泄漏内部名",
          not any(w in " ".join(model_lines) for w in ("reasoning", "fast", "main")),
          str(model_lines))

    js.evaluate("var KNOW = {knowledge_bases: [], projects: []};")
    know_lines = call("knowledgeScopeLines(KNOW)") or []
    check("declared", "空知识范围说「未限定」而不是「没有」",
          any("未限定" in line for line in know_lines), str(know_lines))
    check("declared", "空值/字典不崩溃",
          call("memoryScopeLines(null)") == []
          and call("modelPolicyLines(null)") == []
          and call("knowledgeScopeLines('not an object')") == [])

    check("declared", "岗位声明共 3 节",
          len(call("declaredSections.map(function (s) { return s.key })") or []) == 3,
          str(call("declaredSections.map(function (s) { return s.key })")))


# =========================================================================== #
# 2. 渲染：无头加载真实界面，读渲染树
# =========================================================================== #
def _walk(item, depth: int = 0, limit: int = 24) -> list:
    out = []
    if item is None or depth > limit:
        return out
    for child in item.childItems():
        out.append(child)
        out.extend(_walk(child, depth + 1, limit))
    return out


def _visible_texts(root) -> list[str]:
    """渲染树里**当前真的可见**的文本，按树的顺序。

    `isVisible()` 会沿父链求有效可见性 —— 这正是"存在但被隐藏"与"显示出来了"的区别。
    只读 `text` 属性会把隐藏的也算进来，那样断言就没有意义了。
    """
    texts = []
    for item in _walk(root):
        try:
            if not item.isVisible():
                continue
            value = item.property("text")
        except Exception:  # noqa: BLE001, S112 - 大多数 QML 对象没有 `text`，这正是筛选方式
            continue
        if isinstance(value, str) and value.strip():
            texts.append(value)
    return texts


def _find_by_class(root, needle: str):
    for item in _walk(root):
        if needle in item.metaObject().className():
            return item
    return None


def _as_list(value) -> list:
    try:
        return value.toVariant() or []
    except Exception:  # noqa: BLE001 - 已经是 Python 值（不是 QJSValue）时 toVariant 不存在
        return value or []


def _as_list_or_dict(value):
    """把一个 QML 属性读成 Python 的 dict/list，两种表示都接受。"""
    if value is None:
        return {}
    try:
        return value.toVariant()
    except Exception:  # noqa: BLE001 - 已经是 Python 值（不是 QJSValue）
        return value


#: 引擎与桥接必须活到进程结束。
#:
#: `QQmlApplicationEngine` 是窗口的拥有者：它在 `_load_shell` 里局部创建、出了函数作用域
#: 就被回收，窗口随之被销毁，之后任何 `setProperty` 都抛
#: "Internal C++ object already deleted"。测试脚本没有别的东西持有它。
_KEEP_ALIVE: list = []


def _load_shell():
    import importlib.util

    from awbnative.bridge import ServiceBridge

    spec = importlib.util.spec_from_file_location("awbmain", REPO / "desktop-native" / "main.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.configure_font(app)

    bridge = ServiceBridge(owner_id=None)
    bridge.record_startup({}, {"ready": True})
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("bridge", bridge)
    engine.load(QUrl.fromLocalFile(str(REPO / "desktop-native" / "qml" / "Main.qml")))
    window = engine.rootObjects()[0]
    _KEEP_ALIVE.extend([engine, bridge, window])
    return bridge, window


def _pump(ms: int) -> None:
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)


def run_render_checks() -> None:
    bridge, window = _load_shell()
    window.setProperty("width", 1280)
    window.setProperty("height", 900)
    _pump(600)

    # ------------------------------------------------------------ 员工合同 --
    window.navigate("employees", None)
    _pump(4500)
    root = window.contentItem()
    page = _find_by_class(root, "EmployeesPage")
    if page is None:
        check("contract", "员工页可打开", False, "找不到 EmployeesPage")
        bridge.shutdown()
        return
    check("contract", "员工页可打开", True)

    employees = _as_list(page.property("employees"))
    templates = _as_list(page.property("templates"))
    if not employees:
        check("contract", "有员工可用于渲染", False, "员工列表为空，无法验证投影")
        bridge.shutdown()
        return
    check("contract", "有员工可用于渲染", True, f"{len(employees)} 个")
    check("contract", "岗位模板目录非空（回退来源）", len(templates) > 0, f"{len(templates)} 个")

    # 默认收起
    check("contract", "默认全部收起", page.property("contractOpen") == "",
          str(page.property("contractOpen")))

    subject = employees[0]
    collapsed_texts = _visible_texts(root)
    contract_titles = ["岗位", "使命", "职责", "工作方式", "需要的输入", "不做什么",
                       "先问你的事", "限制", "验收标准", "完成定义", "产出格式", "产出位置"]
    leaked = [t for t in contract_titles if t in collapsed_texts]
    check("contract", "收起时章节标题不出现在渲染树里", not leaked, str(leaked))

    # 展开
    page.setProperty("contractOpen", subject.get("id"))
    _pump(1500)
    expanded_texts = _visible_texts(root)
    present = [t for t in contract_titles if t in expanded_texts]
    check("contract", "展开后 12 个章节全部渲染", len(present) == 12,
          f"渲染出 {len(present)} 个：{present}")

    # 顺序：渲染树里的出现顺序必须与定义一致
    positions = [expanded_texts.index(t) for t in present]
    check("contract", "章节顺序与定义一致", positions == sorted(positions), str(positions))

    # SOP 编号：展开的合同里应当出现 1. 2. 3. …
    sop_lines = [t for t in expanded_texts if t.strip().rstrip(".").isdigit()]
    check("contract", "SOP 有步骤编号", len(sop_lines) >= 2, f"编号 {sop_lines[:6]}")

    # 岗位声明三节 + 那条"尚未生效"的警告
    declared_titles = ["记忆范围", "知识范围", "模型策略"]
    present_declared = [t for t in declared_titles if t in expanded_texts]
    check("declared", "岗位声明 3 节全部渲染", len(present_declared) == 3,
          f"渲染出 {present_declared}")
    check("declared", "标出「尚未接入执行」",
          any("尚未接入执行" in t for t in expanded_texts),
          "警告未出现")
    # 内部名不得泄漏到界面
    leaked_names = [t for t in expanded_texts
                    if any(w in t for w in ("reasoning", "fast", "main", "[object"))]
    check("declared", "界面不泄漏内部名或 [object Object]", not leaked_names,
          str(leaked_names[:3]))

    # 同时只展开一个
    other = next((e for e in employees if e.get("id") != subject.get("id")), None)
    if other is not None:
        page.setProperty("contractOpen", other.get("id"))
        _pump(1200)
        after = _visible_texts(root)
        count = sum(1 for t in contract_titles if t in after)
        check("contract", "同一时刻只展开一个合同（12 节，不是 24）",
              count == 12, f"渲染出 {count} 个章节标题")

    # ------------------------------------------------------------ 首页服务状态 --
    window.navigate("dashboard", None)
    _pump(4000)
    root = window.contentItem()
    dash = _find_by_class(root, "DashboardPage")
    if dash is None:
        check("dashboard", "首页可打开", False, "找不到 DashboardPage")
    else:
        # 页面上的属性可能是 QJSValue 也可能是 Python 值，取决于它当前绑到了什么。
        # 两种都要能读 —— 这正是"绑定求值后的值"而不是"源码里写了什么"。
        summary = _as_list_or_dict(dash.property("summary"))
        llm = _as_list_or_dict(dash.property("llm"))
        check("dashboard", "首页可打开", True)

        providers = (summary or {}).get("providers") or {}
        expected_llm = providers.get("llm") or {}
        # **这条就是那个 bug 的守卫**：绑定的必须是 `providers.llm`，不是 `summary.llm`。
        check("dashboard", "llm 绑定到 summary.providers.llm（不是 summary.llm）",
              (llm or {}).get("api_key_set") == expected_llm.get("api_key_set"),
              f"页面={llm} 数据={expected_llm}")

        texts = _visible_texts(root)
        service_line = next((t for t in texts if t.startswith("AI 服务")), "")
        if expected_llm.get("api_key_set"):
            check("dashboard", "已配置 Key 时不说「未配置」",
                  "未配置" not in service_line, service_line)
            check("dashboard", "已配置 Key 时显示真实模型名",
                  bool(expected_llm.get("model")) and expected_llm["model"] in service_line,
                  f"{service_line} / 期望含 {expected_llm.get('model')}")
        else:
            check("dashboard", "未配置 Key 时说「未配置」",
                  "未配置" in service_line, service_line)

    bridge.shutdown()


# =========================================================================== #
def main() -> int:
    only = sys.argv[1] if len(sys.argv) > 1 else ""
    print("投影层回归测试（断言渲染内容与数据一致）", flush=True)

    if not only or only == "mapping":
        print("\n=== 1. 纯映射（theme.js）===", flush=True)
        run_mapping_checks()
    if not only or only == "contract" or only == "dashboard":
        print("\n=== 2. 渲染（真实界面）===", flush=True)
        run_render_checks()

    failed = [row for row in RESULTS if not row[2]]
    total = len(RESULTS)
    print("\n" + "=" * 62)
    for group, label, _ok, detail in failed:
        print(f"  [FAIL] {group} / {label} — {detail}")
    print(f"结果：{total - len(failed)}/{total} 通过")
    print("=" * 62)
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
