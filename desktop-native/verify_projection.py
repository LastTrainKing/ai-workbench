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

    # ---- 记忆（§5）：7 类 kind 全部有中文标签，未知值有安全回退 ----
    #
    # 取值来自后端常量 `MEMORY_KINDS`（`ai/memory/evaluator.py:73`）。这里逐条钉住，
    # 是因为**漏掉一个 kind 的表现不是报错，而是界面上多出一个英文单词**。
    kinds = {
        "identity": "身份", "goal": "目标", "preference": "偏好", "skill": "技能",
        "constraint": "约束", "fact": "事实", "decision": "决定",
    }
    missing = [k for k in kinds if call(f"memoryKindLabel('{k}')") != kinds[k]]
    check("memory", "7 类 kind 全部有中文标签", not missing, f"未映射 {missing}")
    check("memory", "未知 kind 显示原始值而不是归入已知类别",
          call("memoryKindLabel('speculation')") == "未知类型（speculation）",
          str(call("memoryKindLabel('speculation')")))
    # 缺值时**不补一个已知类别**。原来的界面写 `?? "fact"`，把没有依据的记忆说成"事实"。
    check("memory", "kind 缺值说「类型未知」，不补 fact",
          call("memoryKindLabel(undefined)") == "类型未知"
          and call("memoryKindLabel('')") == "类型未知",
          str(call("memoryKindLabel(undefined)")))

    sources = {"agent": "员工执行时写入", "conversation": "对话中写入",
               "delivery": "交付时写入"}
    bad_sources = [k for k in sources if call(f"memorySourceLabel('{k}')") != sources[k]]
    check("memory", "来源全部有中文标签", not bad_sources, f"未映射 {bad_sources}")
    check("memory", "未知来源有安全回退",
          call("memorySourceLabel('telepathy')") == "未知来源（telepathy）",
          str(call("memorySourceLabel('telepathy')")))

    statuses = {"active": "生效中", "pending": "待你确认", "rejected": "已拒绝"}
    bad_status = [k for k in statuses if call(f"memoryStatusLabel('{k}')") != statuses[k]]
    check("memory", "状态有中文标签", not bad_status, f"未映射 {bad_status}")
    check("memory", "未知状态有安全回退",
          call("memoryStatusLabel('zombie')") == "未知状态（zombie）",
          str(call("memoryStatusLabel('zombie')")))

    # 置信度只格式化，不评级 —— 后端没有定义过阈值
    check("memory", "置信度只格式化不评级",
          call("confidenceText(0.82)") == "置信度 0.82",
          str(call("confidenceText(0.82)")))
    check("memory", "置信度缺失不编造数字",
          call("confidenceText(undefined)") == "置信度未知",
          str(call("confidenceText(undefined)")))
    # 记忆的 scope 复用员工声明那套映射（global/project 是两者共有的）
    check("memory", "记忆范围复用中文映射",
          call("memoryScopeLabel('global')") == "全局记忆"
          and call("memoryScopeLabel('project')") == "项目记忆")

    check("declared", "岗位声明共 3 节",
          len(call("declaredSections.map(function (s) { return s.key })") or []) == 3,
          str(call("declaredSections.map(function (s) { return s.key })")))

    # ---- 交付闭环（§8.4 / §10）：状态词、类型名、验证摘要 ----
    #
    # 这一组守的是"运行结束后用户看到什么"。状态词原先写在 `AgentWorkbenchPage` 里，
    # 而交付物页也要说同一件事 —— 两个副本迟早给出两个答案，所以搬进了 theme.js。
    build_words = {
        "DRAFT": "还没开始", "PLANNING": "正在理解需求", "READY": "正在整理资料",
        "EXECUTING": "正在生成文件", "VERIFYING": "正在检查结果",
        "WAITING_USER": "等你确认", "COMPLETED": "已交付", "PARTIAL": "交付了一部分",
        "FAILED": "没能完成", "CANCELLED": "已取消", "LIMIT_REACHED": "到了资源上限",
    }
    bad_words = [k for k in build_words if call(f"buildWord('{k}')") != build_words[k]]
    check("delivery", "构建状态 11 个全部有人话", not bad_words, f"未映射 {bad_words}")
    # 未知状态显示原始值而不是编一个像"已完成"的词。
    check("delivery", "未知构建状态不伪装成完成",
          call("buildWord('WEIRD')") == "WEIRD", str(call("buildWord('WEIRD')")))

    tone_done = call("buildTone('COMPLETED')")
    tone_failed = call("buildTone('FAILED')")
    tone_running = call("buildTone('EXECUTING')")
    check("delivery", "完成是绿色，失败是红色",
          tone_done == call("success") and tone_failed == call("danger"),
          f"{tone_done} / {tone_failed}")
    check("delivery", "进行中不是完成色",
          tone_running != call("success") and tone_running != call("danger"),
          str(tone_running))

    kinds = {"pptx": "演示文稿", "docx": "Word 文档", "xlsx": "Excel 表格",
             "pdf": "PDF", "md": "Markdown", "txt": "纯文本", "csv": "CSV 数据",
             "report": "报告", "code": "代码", "mixed": "多种格式"}
    bad_kinds = [k for k in kinds if call(f"kindLabel('{k}')") != kinds[k]]
    check("delivery", "10 种产出格式全部有中文名", not bad_kinds, f"未映射 {bad_kinds}")
    check("delivery", "未知格式显示原始值",
          call("kindLabel('xyz')") == "xyz", str(call("kindLabel('xyz')")))
    # 界面不得出现裸的格式代号 —— 用户看到的是"演示文稿"，不是"pptx"。
    check("delivery", "格式代号不直接上屏",
          all(call(f"kindLabel('{k}')") != k for k in kinds), str(kinds))

    check("delivery", "大小只格式化，取不到就给空",
          call("humanBytes(2048)") == "2.0 KB"
          and call("humanBytes(undefined)") == ""
          and call("humanBytes(-1)") == "",
          f"{call('humanBytes(2048)')} / {call('humanBytes(undefined)')}")

    # 验证摘要：**第三种结果必须说出来**。把"没查"写成"通过"是假话，混进分母里
    # 又会让它看起来像失败 —— 两种都不行。
    js.evaluate("var V_OK = {checks: [{outcome: 'pass'}, {outcome: 'pass'}]};")
    js.evaluate("var V_MIX = {checks: [{outcome: 'pass'}, {outcome: 'fail'},"
                " {outcome: 'unverified'}]};")
    js.evaluate("var V_LEGACY = {checks: [{ok: true}, {ok: false}]};")
    check("delivery", "全部通过时说 N/N 通过",
          call("verificationSummary(V_OK)") == "2/2 项检查通过",
          str(call("verificationSummary(V_OK)")))
    check("delivery", "未通过单独计数",
          call("verificationSummary(V_MIX)") == "1/3 项检查通过，1 项未通过，1 项无法验证",
          str(call("verificationSummary(V_MIX)")))
    check("delivery", "无法验证不与未通过混为一谈",
          "1 项无法验证" in call("verificationSummary(V_MIX)")
          and "2 项未通过" not in call("verificationSummary(V_MIX)"),
          str(call("verificationSummary(V_MIX)")))
    check("delivery", "旧的布尔式检查仍然能读",
          call("verificationSummary(V_LEGACY)") == "1/2 项检查通过，1 项未通过",
          str(call("verificationSummary(V_LEGACY)")))
    check("delivery", "没有检查记录时不谎称已验证",
          call("verificationSummary(null)") == "尚未验证"
          and call("verificationSummary({checks: []})") == "尚未验证",
          str(call("verificationSummary(null)")))

    # ---- 普通 / 专家模式的导航（§7）----
    #
    # §7 要求普通模式的一级入口保持克制。这一组钉住两件事：**少的是哪些**，
    # 以及**主路径一个都不能少**。第二条比第一条重要 —— 把 `agent` 一起藏起来
    # 会让首页的「交给员工执行」跳到一个导航栏里没有的页面。
    normal_keys = call("pagesFor(false).map(function (p) { return p.key })") or []
    expert_keys = call("pagesFor(true).map(function (p) { return p.key })") or []
    hidden = ["runs", "graph", "planner", "permissions", "search", "ask"]

    check("nav", "专家模式包含全部 19 个页面", len(expert_keys) == 19, f"{len(expert_keys)} 个")
    check("nav", "普通模式更短", 0 < len(normal_keys) < len(expert_keys),
          f"{len(normal_keys)} vs {len(expert_keys)}")
    check("nav", "普通模式不含开发细节页",
          not [k for k in hidden if k in normal_keys], str([k for k in hidden if k in normal_keys]))
    check("nav", "专家模式仍然含这些页（是收起不是删掉）",
          all(k in expert_keys for k in hidden), str(hidden))
    # 主路径：首页发起 → Agent 执行 → 员工 → 交付物。少一个就断链。
    for required in ("dashboard", "agent", "employees", "deliverables", "settings"):
        check("nav", f"普通模式保留主路径 {required}", required in normal_keys)
    check("nav", "普通模式不出现空分组",
          all(n > 0 for n in (call("navGroupsFor(false).map(function (g) { return g.entries.length })") or [])),
          str(call("navGroupsFor(false).map(function (g) { return g.entries.length })")))
    # 普通模式的顺序必须是专家模式的子序列 —— 否则同一件事在两处顺序不同。
    positions = [expert_keys.index(k) for k in normal_keys if k in expert_keys]
    check("nav", "普通模式是专家模式的子序列", positions == sorted(positions), str(positions))

    # 快捷键提示跟随模式：提示一个跳到看不见的页面的键，比不给提示更糟。
    #
    # 值先算出来再拼进消息。**不是为了好看**：`f"{call('a', \"b\")}"` 这种嵌套引号
    # 要 Python 3.12 才合法，而本项目的 `target-version` 是 py311 —— ruff 直接报
    # `invalid-syntax`，也就是说这个文件在 3.11 上**根本解析不了**。
    # 是在干净克隆里用全新 venv 跑一遍才发现的。
    shortcut_normal = call("shortcutFor('employees', false)")
    shortcut_expert = call("shortcutFor('employees', true)")
    check("nav", "可见页的快捷键在两模式下一致",
          shortcut_normal == shortcut_expert and shortcut_normal != "",
          f"{shortcut_normal} / {shortcut_expert}")
    check("nav", "没有快捷键的页面返回空串",
          call("shortcutFor('settings', false)") == ""
          and call("shortcutFor('runs', true)") == "")
    check("nav", "快捷键数量不超过可见页面数",
          call("shortcutCountFor(false)") <= len(normal_keys)
          and call("shortcutCountFor(true)") <= len(expert_keys)
          and call("shortcutCountFor(true)") == 9,
          f"{call('shortcutCountFor(false)')} / {call('shortcutCountFor(true)')}")


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


def _visible_buttons(root) -> list[tuple[str, bool]]:
    """渲染树里当前可见的按钮，取 (文案, 是否可点)。

    `enabled` 也要读，因为 §8.2 的要求不只是"按钮在不在"，而是"按钮**能不能按**"：
    文件不在了还留一个可点的「打开」，比没有这个按钮更糟。
    """
    out = []
    for item in _walk(root):
        if "Button" not in item.metaObject().className():
            continue
        try:
            if not item.isVisible():
                continue
            text = item.property("text")
            enabled = item.property("enabled")
        except Exception:  # noqa: BLE001, S112 - 非按钮控件没有这些属性，跳过正是筛选方式
            continue
        if isinstance(text, str) and text.strip():
            out.append((text, bool(enabled)))
    return out


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


def _pump_until(predicate, timeout_ms: int, slice_ms: int = 250) -> bool:
    """Pump events until `predicate()` is true, or the deadline passes.

    **A fixed `_pump(4500)` is a guess about how long a database query takes**, and it
    is a guess that failed once: run immediately after a 15-minute suite, the employee
    list had not arrived within 4.5 s and the whole render group reported "员工列表为空,
    无法验证投影" — fourteen assertions skipped because a machine was busy.

    A flaky acceptance test is worse than a slow one: it teaches the reader to re-run
    instead of to look. Waiting for the condition removes the guess without hiding a
    real failure — if the list never arrives, the deadline still expires and the check
    still fails.
    """
    end = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < end:
        app.processEvents()
        if predicate():
            return True
        time.sleep(slice_ms / 1000)
    app.processEvents()
    return bool(predicate())


def run_render_checks() -> None:
    bridge, window = _load_shell()
    window.setProperty("width", 1280)
    window.setProperty("height", 900)
    _pump(600)

    # ------------------------------------------------------- 普通 / 专家模式 --
    #
    # 断言的是**导航栏当下渲染出来的条目**，不是 `theme.js` 里那张表 —— 表对了而
    # 绑定没接上，正是这个工具存在的理由。
    normal_keys = [str(e["key"]) for e in _as_list(window.property("railEntries"))]
    check("nav", "普通模式导航不含开发细节页",
          bool(normal_keys) and "runs" not in normal_keys and "graph" not in normal_keys,
          str(normal_keys))

    window.setProperty("expertMode", True)
    _pump(700)
    expert_keys = [str(e["key"]) for e in _as_list(window.property("railEntries"))]
    check("nav", "专家模式导航更长且含开发细节页",
          len(expert_keys) > len(normal_keys) and "runs" in expert_keys,
          f"{len(normal_keys)} -> {len(expert_keys)}")

    window.setProperty("expertMode", False)
    _pump(700)
    back_keys = [str(e["key"]) for e in _as_list(window.property("railEntries"))]
    check("nav", "切回普通模式后导航恢复", back_keys == normal_keys, str(back_keys))

    # ------------------------------------------------------------ 员工合同 --
    window.navigate("employees", None)
    root = window.contentItem()
    page = _find_by_class(root, "EmployeesPage")
    if page is None:
        _pump(1500)
        root = window.contentItem()
        page = _find_by_class(root, "EmployeesPage")
    if page is None:
        check("contract", "员工页可打开", False, "找不到 EmployeesPage")
        bridge.shutdown()
        return
    check("contract", "员工页可打开", True)

    # 等名册真的到，而不是猜它多久到。见 `_pump_until`。
    _pump_until(lambda: bool(_as_list(page.property("employees"))), timeout_ms=20000)

    employees = _as_list(page.property("employees"))
    templates = _as_list(page.property("templates"))
    if not employees:
        check("contract", "有员工可用于渲染", False, "员工列表为空（等了 20 秒），无法验证投影")
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
    root = window.contentItem()
    dash = _find_by_class(root, "DashboardPage")
    if dash is None:
        _pump(1500)
        root = window.contentItem()
        dash = _find_by_class(root, "DashboardPage")
    if dash is None:
        check("dashboard", "首页可打开", False, "找不到 DashboardPage")
    else:
        # 等 summary 真的到。同一个理由：固定的 4000 ms 是对数据库延迟的猜测。
        _pump_until(
            lambda: bool(_as_list_or_dict(dash.property("summary"))), timeout_ms=20000
        )
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

        # 离线引擎的能力边界必须写在状态栏上，而不是只写在文档里。
        #
        # 内置引擎的 agent 工具序列里**没有 `file_write`**
        # （`backend/app/ai/providers/heuristic.py` 的 `_agent_step`），所以"让员工产出一个
        # 文件"在没配 Key 时**不可能达成**。状态栏原来说的是"功能可用"，一个没配 Key 的
        # 新用户会照它试一次，然后拿到一条笔记、和一个指向空处的"产出在下面"。
        #
        # **这里强制注入未配置状态**：这台机器上配了 Key，不注入就永远走不到这个分支 ——
        # 而它恰恰是新用户唯一会看到的那一支。断言的是**渲染出来的那句话**，不是源码里写了什么。
        offline = {"providers": {"llm": {"api_key_set": False, "model": ""}}}
        dash.setProperty("summary", offline)
        _pump(600)
        offline_line = next(
            (t for t in _visible_texts(root) if t.startswith("AI 服务")), ""
        )
        check("dashboard", "未配置时不宣称「功能可用」",
              "功能可用" not in offline_line, offline_line)
        check("dashboard", "未配置时说清写不出交付文件",
              "写不出交付文件" in offline_line, offline_line)
        dash.setProperty("summary", summary)
        _pump(300)

    # -------------------------------------------------- 失败提示里带不带原因 --
    #
    # 一条桥接失败走 `Main.qml::fail()` → `Errors.human()` → toast。兜底那一档的 `title`
    # 是「操作没有完成」，本身不含信息；**真正的原因在 `hint` 里**（`async_runner` 送的是
    # `f"{type(exc).__name__}: {exc}"`），而 `fail()` 会把 hint 接在标题后面。
    #
    # 这一条是被人质疑之后加的。一份验收报告说"toast 只显示 title，原因对用户不可见"，
    # 并建议把 hint 一并显示 —— **实测它已经显示了**，所以没有改代码，改成把"显示了"
    # 钉住：否则下一次有人删掉那一行，就真的会变成报告说的样子，而没有任何断言拦着。
    bridge.failed.emit("projection-toast", "TypeError: boom-marker-7f3a", "error")
    _pump(600)
    toast = window.property("toastText") or ""
    check("errors", "失败 toast 带上 hint（原因不是只剩标题）",
          "boom-marker-7f3a" in toast, toast)

    bridge.shutdown()


# =========================================================================== #
# 3. 交付卡片：把一份与后端同形的结果注入页面，断言它画出来的东西
# =========================================================================== #
#
# **这里注入的是手写数据，这是有意的。** 后端产出的形状由
# `backend/tests/integration/test_run_outcome.py` 钉住（真实数据库、真实文件、
# 真实删除），这一组只回答另一个问题：同一份数据，页面画对了吗？
#
# 两边合起来才是完整的契约 —— 少了任何一边，都可能是"数据对、画错了"（两次真实
# 事故的形态）或者"画得对、数据是假的"。
_DEMO_OUTCOME = {
    "build_run_id": "11111111-1111-1111-1111-111111111111",
    "status": "COMPLETED",
    "goal": "写一篇文章，输出 md",
    "summary": "产出 2 个文件，其中 1 个已经不在了：演示稿。",
    "artifacts": [
        {
            "id": "a1", "title": "季度报告", "kind": "md", "path": "C:/x/季度报告.md",
            "size_bytes": 2048, "version": 2, "verified": True, "accepted": None,
            "feedback": "",
            "file_state": {"exists": True, "size_bytes": 2048, "reason": ""},
            "actions": [
                {"id": "preview", "label": "预览", "enabled": True, "reason": ""},
                {"id": "open", "label": "打开", "enabled": True, "reason": ""},
                {"id": "reveal", "label": "在文件夹中显示", "enabled": True, "reason": ""},
                {"id": "continue", "label": "继续修改", "enabled": True, "reason": ""},
                {"id": "regenerate", "label": "重新生成", "enabled": True, "reason": ""},
            ],
        },
        {
            "id": "a2", "title": "演示稿", "kind": "pptx", "path": "C:/x/演示稿.pptx",
            "size_bytes": 51200, "version": 1, "verified": False, "accepted": False,
            "feedback": "图表太少",
            "file_state": {"exists": False, "size_bytes": 0,
                           "reason": "文件不在了：FileNotFoundError"},
            "actions": [
                {"id": "preview", "label": "预览", "enabled": False,
                 "reason": "文件不在了：FileNotFoundError"},
                {"id": "open", "label": "打开", "enabled": False,
                 "reason": "文件不在了：FileNotFoundError"},
                {"id": "reveal", "label": "在文件夹中显示", "enabled": False,
                 "reason": "文件不在了：FileNotFoundError"},
                {"id": "continue", "label": "继续修改", "enabled": True, "reason": ""},
                {"id": "regenerate", "label": "重新生成", "enabled": True, "reason": ""},
            ],
        },
    ],
    "verification": [
        {"artifact_id": "a1", "artifact_title": "季度报告", "passed": True,
         "check_count": 3, "failed_count": 0, "unverified_count": 0,
         "checks": [
             {"name": "file_exists", "outcome": "pass", "ok": True, "detail": "2.0 KB"},
             {"name": "encoding", "outcome": "pass", "ok": True, "detail": "UTF-8"},
             {"name": "content", "outcome": "pass", "ok": True, "detail": "1200 个字符"},
         ],
         "run_state": {"state": "COMPLETED", "ok": True, "detail": "这次运行走完了全部步骤。"}},
        {"artifact_id": "a2", "artifact_title": "演示稿", "passed": False,
         "check_count": 1, "failed_count": 1, "unverified_count": 0,
         "checks": [{"name": "file_exists", "outcome": "fail", "ok": False,
                     "detail": "文件不存在"}],
         "run_state": {"state": "COMPLETED", "ok": True, "detail": "这次运行走完了全部步骤。"}},
    ],
    "next_actions": [
        {"id": "verify", "label": "重新验证全部产出", "enabled": True, "reason": ""},
        {"id": "open_deliverables", "label": "查看全部交付物", "enabled": True, "reason": ""},
    ],
    "diagnostic_ref": {"build_run_id": "11111111-1111-1111-1111-111111111111",
                       "ai_run_id": None},
}


def run_delivery_checks() -> None:
    bridge, window = _load_shell()
    window.setProperty("width", 1280)
    window.setProperty("height", 900)
    _pump(600)

    window.navigate("agent", None)
    _pump(2500)
    root = window.contentItem()
    page = _find_by_class(root, "AgentWorkbenchPage")
    if page is None:
        check("delivery", "Agent 工作台可打开", False, "找不到 AgentWorkbenchPage")
        bridge.shutdown()
        return
    check("delivery", "Agent 工作台可打开", True)

    # 注入前：成果区不出现 —— 没有运行过就不该有"这次的成果"。
    before = _visible_texts(root)
    check("delivery", "没有运行时不显示成果区", "这次的成果" not in before,
          str([t for t in before if "成果" in t]))

    page.setProperty("outcomeData", _DEMO_OUTCOME)
    _pump(900)

    texts = _visible_texts(root)
    buttons = _visible_buttons(root)

    # ---- 成果区出现，状态与人话一致 ----
    check("delivery", "成果区出现", "这次的成果" in texts)
    check("delivery", "状态说「已交付」而不是 COMPLETED",
          "已交付" in texts and "COMPLETED" not in texts,
          str([t for t in texts if "COMPLETED" in t][:3]))
    check("delivery", "摘要是后端给的那句，不是页面自己编的",
          _DEMO_OUTCOME["summary"] in texts)

    # ---- 两条产物都渲染出真实标题与类型 ----
    check("delivery", "两个产物标题都渲染", "季度报告" in texts and "演示稿" in texts)
    check("delivery", "类型显示中文名而不是 pptx/md",
          any(t.startswith("Markdown") for t in texts)
          and any(t.startswith("演示文稿") for t in texts)
          and "pptx" not in texts and "md" not in texts,
          str([t for t in texts if t.startswith(("Markdown", "演示文稿"))]))
    check("delivery", "版本号渲染", "第 2 版" in texts and "第 1 版" in texts)
    check("delivery", "大小渲染成人话", any("2.0 KB" in t for t in texts),
          str([t for t in texts if "KB" in t or " B" in t]))

    #: 文件不在了就不报大小。"0 B" 会被读成"文件是空的"，而事实是文件不见了。
    check("delivery", "文件不在时不报大小",
          not any(t.startswith("演示文稿 · 0 B") for t in texts),
          str([t for t in texts if t.startswith("演示文稿")]))

    # ---- 验证证据：两个产物的摘要各自对应自己的检查 ----
    check("delivery", "通过的那条说 3/3",
          any("3/3 项检查通过" in t for t in texts),
          str([t for t in texts if "项检查通过" in t]))
    check("delivery", "失败的那条说未通过，且不与另一条串台",
          any("0/1 项检查通过，1 项未通过" in t for t in texts),
          str([t for t in texts if "项检查通过" in t]))

    # ---- 文件不在：必须明说，且按钮必须灰掉 ----
    check("delivery", "文件不在时显示原因",
          "文件不在了：FileNotFoundError" in texts)
    check("delivery", "驳回理由渲染", "驳回理由：图表太少" in texts)

    open_buttons = [enabled for text, enabled in buttons if text == "打开"]
    check("delivery", "两条产物各有一个「打开」", len(open_buttons) == 2, str(open_buttons))
    check("delivery", "文件在的那条「打开」可点、不在的那条不可点",
          open_buttons.count(True) == 1, str(open_buttons))

    preview_buttons = [enabled for text, enabled in buttons if text == "预览"]
    check("delivery", "「预览」按钮跟着文件状态走",
          len(preview_buttons) == 2 and preview_buttons.count(True) == 1,
          str(preview_buttons))

    # ---- 运行级动作 ----
    check("delivery", "运行级动作渲染",
          "重新验证全部产出" in texts and "查看全部交付物" in texts)
    # 运行级动作不得与产物级动作重名 —— 同一个按钮出现在两处迟早会漂移。
    artifact_labels = {a["label"] for art in _DEMO_OUTCOME["artifacts"] for a in art["actions"]}
    run_labels = {a["label"] for a in _DEMO_OUTCOME["next_actions"]}
    check("delivery", "运行级与产物级动作不重名",
          not (artifact_labels & run_labels), str(artifact_labels & run_labels))

    # ---- 内部字段不得泄漏 ----
    leaked = [t for t in texts
              if any(w in t for w in ("[object", "file_state", "artifact_id", "diagnostic_ref"))]
    check("delivery", "界面不泄漏内部字段名", not leaked, str(leaked[:3]))

    bridge.shutdown()


# =========================================================================== #
def main() -> int:
    only = sys.argv[1] if len(sys.argv) > 1 else ""
    print("投影层回归测试（断言渲染内容与数据一致）", flush=True)

    if not only or only == "mapping":
        print("\n=== 1. 纯映射（theme.js）===", flush=True)
        run_mapping_checks()
    if not only or only in ("contract", "dashboard"):
        print("\n=== 2. 渲染（真实界面）===", flush=True)
        run_render_checks()
    if not only or only == "delivery":
        print("\n=== 3. 交付卡片（注入同形数据）===", flush=True)
        run_delivery_checks()

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
