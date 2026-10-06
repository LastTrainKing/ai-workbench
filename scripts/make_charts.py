# -*- coding: utf-8 -*-
"""为毕业设计答辩 PPT 生成三部分图表（数据全部来自 samples/demo1-sources 与 backend/app 源码）。"""
import os, re, json
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

ROOT = r"C:\Users\user\Desktop\ai-workbench"
OUT = os.path.join(ROOT, "charts")
MIRROR = os.path.abspath(os.path.join(os.getcwd(), "charts"))
for d in (OUT, MIRROR):
    os.makedirs(d, exist_ok=True)

avail = {f.name for f in font_manager.fontManager.ttflist}
cjk = next((c for c in ["Microsoft YaHei", "SimHei", "SimSun", "Noto Sans CJK SC", "Source Han Sans SC"] if c in avail), None)
plt.rcParams["font.sans-serif"] = ([cjk] if cjk else []) + ["DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False
C = {"blue": "#2b5c9b", "teal": "#2e8b8b", "orange": "#d98324", "gray": "#8a8f98", "red": "#b4463c", "green": "#4b8b54"}

# ---------- 真实数据采集 ----------
md = open(os.path.join(ROOT, "samples", "demo1-sources", "系统总体设计.md"), encoding="utf-8").read()
dom = {}
for ln in md.split("\n"):
    m = re.match(r"\|\s*\d+\s*\|\s*`(\w+)`\s*\|\s*`(\w+)`\s*\|\s*`[\w.]+`\s*\|\s*([^|]+?)\s*\|", ln)
    if m:
        dom.setdefault(m.group(3).replace("（关联）", ""), []).append(m.group(1))

mdir = os.path.join(ROOT, "backend", "app", "models")
cols = {}
for f in os.listdir(mdir):
    if f.endswith(".py"):
        for b in re.split(r"(?m)^class ", open(os.path.join(mdir, f), encoding="utf-8").read())[1:]:
            tn = re.search(r'__tablename__\s*=\s*"(\w+)"', b)
            if tn:
                body = b.split("\n\n\n")[0]
                cols[tn.group(1)] = len(re.findall(r"mapped_column\(", body))

app = os.path.join(ROOT, "backend", "app")
pf = {d: sum(1 for dp, dn, fs in os.walk(os.path.join(app, d)) for f in fs if f.endswith(".py"))
      for d in os.listdir(app) if os.path.isdir(os.path.join(app, d)) and d != "__pycache__"}
assert sum(len(v) for v in dom.values()) == 22, dom
assert len(cols) == 22, cols
print("数据校验通过：业务域 %d 个 / 表 %d 张 / 已解析字段数表的数量 %d" % (len(dom), sum(len(v) for v in dom.values()), len(cols)))

def finish(fig, name):
    p = os.path.join(OUT, name)
    fig.savefig(p, dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return p

# ---------- 图 1：架构分层 + 模块占比 ----------
fig = plt.figure(figsize=(15, 7.2))
gs = fig.add_gridspec(1, 2, width_ratios=[1.35, 1])
ax = fig.add_subplot(gs[0, 0]); ax.axis("off")
layers = [
    ("L1 接入层 API", "路由 / 鉴权依赖 / 请求响应模型", pf["api"], C["blue"]),
    ("L2 业务层 Service", "编排 / 事务 / 业务规则 / 调用 AI", pf["services"], C["teal"]),
    ("L3 数据访问层 Repository", "唯一编写 SQL 的层次", pf["repositories"], C["teal"]),
    ("L4 模型与基础设施层", "22 张表 / 引擎 / 文件存储 / 向量库", pf["models"] + pf["infrastructure"], C["gray"]),
    ("L5 AI 层", "Provider / Prompt / Parser / RAG / Agent", pf["ai"], C["orange"]),
    ("L6 后台任务 Worker", "消费任务 / 调用 Service", pf["worker"], C["green"]),
]
top, h, gap = 5.6, 0.72, 0.26
for i, (t, sub, n, col) in enumerate(layers):
    y = top - i * (h + gap)
    ax.add_patch(plt.Rectangle((0.06, y), 0.88, h, facecolor=col, alpha=0.16, edgecolor=col, lw=1.8))
    ax.text(0.11, y + h * 0.62, t, fontsize=12.5, color=col, fontweight="bold", va="center")
    ax.text(0.11, y + h * 0.24, sub, fontsize=9.5, color="#333333", va="center")
    ax.text(0.90, y + h * 0.5, "%d 个模块文件" % n, fontsize=9.5, ha="right", va="center", color="#555555")
    if i < len(layers) - 1:
        ax.annotate("", xy=(0.50, y - gap + 0.02), xytext=(0.50, y - 0.02),
                    arrowprops=dict(arrowstyle="-|>", color="#999999", lw=1.4))
ax.text(0.50, 0.20, "依赖方向严格自上而下：接入层不写 SQL，Repository 是唯一写 SQL 的层，Agent 不直接访问数据库",
        fontsize=9.5, ha="center", color="#444444")
ax.text(0.50, 6.62, "图 1-a 五层架构与单向依赖（标注为各层 Python 模块文件数）", fontsize=12, ha="center", fontweight="bold")
ax.set_xlim(0, 1); ax.set_ylim(0, 7)

ax2 = fig.add_subplot(gs[0, 1])
labels = [k for k, _ in sorted(pf.items(), key=lambda x: -x[1])]
vals = [pf[k] for k in labels]
palette = plt.cm.Blues([0.75, 0.62, 0.55, 0.45, 0.35, 0.25, 0.18, 0.14, 0.12, 0.10, 0.08])
wedges, *_ = ax2.pie(vals, startangle=90, colors=palette, wedgeprops=dict(width=0.42, edgecolor="white"))
for w, l, v in zip(wedges, labels, vals):
    if v / sum(vals) < 0.035:
        continue
    ang = (w.theta2 + w.theta1) / 2
    import math
    x = 1.18 * math.cos(math.radians(ang)); y = 1.18 * math.sin(math.radians(ang))
    ax2.text(x, y, "%s\n%d" % (l, v), ha="center", va="center", fontsize=9)
ax2.text(0, 0, "共 %d 个\nPython 模块" % sum(vals), ha="center", va="center", fontsize=11.5, color="#2b5c9b", fontweight="bold")
ax2.set_title("图 1-b 后端 app/ 模块文件占比", fontsize=12, fontweight="bold")
finish(fig, "01_架构分层与模块占比.png")

# ---------- 图 2：数据库表结构与表数量 ----------
fig, axes = plt.subplots(1, 2, figsize=(15, 6.4), gridspec_kw={"width_ratios": [1, 1.25]})
ax = axes[0]
dk = sorted(dom.items(), key=lambda x: -len(x[1]))
names = [k for k, v in dk]; cnt = [len(v) for k, v in dk]
bars = ax.bar(names, cnt, color=C["blue"], width=0.6, alpha=0.85)
for b, v in zip(bars, cnt):
    ax.text(b.get_x() + b.get_width() / 2, v + 0.08, str(v), ha="center", fontsize=11, fontweight="bold", color=C["blue"])
ax.set_ylabel("表数量（张）"); ax.set_ylim(0, max(cnt) + 1.4)
ax.set_title("图 2-a 22 张表按业务域分布", fontsize=12, fontweight="bold")
ax.grid(axis="y", ls=":", alpha=0.4); ax.set_axisbelow(True)
ax.tick_params(axis="x", labelsize=10)
ax.text(0.5, max(cnt) + 0.95, "7 个模型模块 · 22 张表 · PostgreSQL 16 + Qdrant 1.12 + Redis 5",
        ha="center", fontsize=9.5, color="#555555")

ax = axes[1]
ck = sorted(cols.items(), key=lambda x: x[1])
y = range(len(ck))
ax.barh(list(y), [v for k, v in ck], color=C["teal"], alpha=0.85, height=0.68)
ax.set_yticks(list(y)); ax.set_yticklabels([k for k, v in ck], fontsize=8.4)
for i, (k, v) in enumerate(ck):
    ax.text(v + 0.6, i, str(v), va="center", fontsize=8.4, color="#2e6e6e")
ax.set_xlabel("字段数（含 mixin 提供的 id / created_at / updated_at）")
ax.set_xlim(0, max(v for k, v in ck) + 5)
ax.set_title("图 2-b 逐表字段规模（ai / context / delivery 为字段最多的三张域）", fontsize=12, fontweight="bold")
ax.grid(axis="x", ls=":", alpha=0.4); ax.set_axisbelow(True)
finish(fig, "02_数据库表结构与表数量.png")

# ---------- 图 3：AI 模块关键参数对比 ----------
fig, axes = plt.subplots(1, 3, figsize=(15, 5.6))
ax = axes[0]
ax.bar(["稠密腿（Qdrant）", "稀疏腿（ILIKE+BM25）"], [0.6, 0.4], color=[C["orange"], C["gray"]], width=0.55, alpha=0.9)
for i, v in enumerate([0.6, 0.4]):
    ax.text(i, v + 0.02, "α=%.1f" % v, ha="center", fontsize=11, fontweight="bold")
ax.set_ylim(0, 0.8); ax.set_ylabel("混合融合权重")
ax.set_title("图 3-a 混合检索融合权重\nscore = α·dense + (1−α)·sparse", fontsize=11.5, fontweight="bold")
ax.grid(axis="y", ls=":", alpha=0.4); ax.set_axisbelow(True)

ax = axes[1]
lf = ["查询词覆盖", "精确短语命中", "标题匹配", "长度先验"]
lv = [0.45, 0.25, 0.20, 0.10]
ax.bar(lf, lv, color=C["blue"], width=0.55, alpha=0.9)
for i, v in enumerate(lv):
    ax.text(i, v + 0.012, "%.2f" % v, ha="center", fontsize=10.5, fontweight="bold")
ax.set_ylim(0, 0.56); ax.set_ylabel("词法重排特征权重")
ax.set_title("图 3-b 词法重排权重\nlexical = Σ wᵢ·featureᵢ", fontsize=11.5, fontweight="bold")
ax.grid(axis="y", ls=":", alpha=0.4); ax.set_axisbelow(True)
ax.tick_params(axis="x", labelsize=9.5)

ax = axes[2]
ax.bar(["混合检索分", "词法重排分"], [0.65, 0.35], color=[C["teal"], C["green"]], width=0.55, alpha=0.9)
for i, v in enumerate([0.65, 0.35]):
    ax.text(i, v + 0.02, "%.2f" % v, ha="center", fontsize=11, fontweight="bold")
ax.set_ylim(0, 0.85); ax.set_ylabel("最终排序融合权重")
ax.set_title("图 3-c 最终排序融合\nscore = 0.65·fused + 0.35·lexical", fontsize=11.5, fontweight="bold")
ax.grid(axis="y", ls=":", alpha=0.4); ax.set_axisbelow(True)

fig.suptitle("AI 模块关键参数（源码默认值 / 设计权重，非实测基准）", fontsize=13.5, fontweight="bold", y=1.06)
fig.text(0.5, -0.06, "其余设计要点：BM25 k1=1.5、b=0.75；标题感知分块目标长度 200–1600 字符；"
                     "提供商三级降级（OpenAI 兼容 → Ollama → 离线启发式）；每次调用落库 ai_runs（Token/延迟/成本）。\n"
                     "注：系统测试记录明确「未做吞吐、并发与内存基准」，因此本图不填任何实测延迟或准确率数字。",
         ha="center", fontsize=9.2, color="#555555")
finish(fig, "03_AI模块关键参数对比.png")

# ---------- 产出清单 + 镜像 ----------
import shutil
out = []
for f in sorted(os.listdir(OUT)):
    if f.endswith(".png"):
        p = os.path.join(OUT, f)
        shutil.copy2(p, os.path.join(MIRROR, f))
        out.append((f, os.path.getsize(p)))
print("OUT:", OUT)
for f, s in out:
    print("  %s  %d bytes" % (f, s))
print("MIRROR:", MIRROR)
