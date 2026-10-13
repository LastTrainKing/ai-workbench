"""界面偏好 —— 一个属于人的设置，不是一个属于机器的设置。

只有一项：`expert_mode`。它决定窗口显示"任务和结果"还是"任务、结果加上步骤、工具调用、
token 和延迟"。

为什么单独一个文件，而不是塞进 `ai-config`
------------------------------------------------
`ai-config` 里是**密钥**，它有一整套校验（ASCII、不许有空白、不许回显）。界面偏好是
另一类东西：它会被频繁改写，值都是布尔，泄漏了也不构成任何风险。把两者放在一个文件里，
等于让"切换一下显示模式"这条路经过读密钥的那段代码 —— 一条本来不存在的路径。

为什么按账号存
--------------
跟 `ai-config` 同一个理由，只是后果更轻：同一台机器上的两个账号不该互相决定对方看到
什么。技术上不危险，但它是同一个错误的小号版本 —— 一个属于个人的设置躺在属于机器的
文件里。目录形状因此和 `ai-config` 保持一致。

为什么白名单
------------
`save` 只接受 `OVERRIDABLE` 里的键。这个文件会在下一次启动时被读回来，一个"存下调用方
给的一切"的接口，就是一条把任意数据带进下一次启动的路径。默认值也只从 `DEFAULTS` 里取，
所以文件里多出来的键永远不会变成界面上的状态。
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from app.core.config import RUNTIME_ROOT
from app.core.logging import get_logger

logger = get_logger(__name__)

#: 按账号的偏好目录，与 `ai-config` 并列。
PREFERENCES_DIR: Path = RUNTIME_ROOT / "ui-preferences"

#: 界面允许改的键。
OVERRIDABLE: tuple[str, ...] = ("expert_mode",)

#: 没有文件、文件读不动、或者键不在白名单里时，用的值。
DEFAULTS: dict[str, Any] = {"expert_mode": False}


def _path(owner_id: uuid.UUID | str) -> Path:
    return PREFERENCES_DIR / f"{owner_id}.json"


def load(owner_id: uuid.UUID | str) -> dict[str, Any]:
    """这个账号的偏好，缺的用默认值补上。

    读不动不算错误：一个坏掉的首选项文件不该让界面打不开。它只该让这个账号回到默认
    显示，并且在日志里留一条。
    """
    values = dict(DEFAULTS)
    path = _path(owner_id)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return values
    except Exception as exc:
        logger.warning(
            "ui_preferences_unreadable",
            extra={"owner": str(owner_id), "error": f"{type(exc).__name__}: {exc}"},
        )
        return values

    if not isinstance(raw, dict):
        logger.warning("ui_preferences_not_an_object", extra={"owner": str(owner_id)})
        return values

    for key in OVERRIDABLE:
        if key not in raw:
            continue
        default = DEFAULTS[key]
        value = raw[key]
        # 类型必须跟默认值一致。一个字符串 "false" 在 QML 里是真值 —— 静默地把"普通
        # 模式"读成"专家模式"，只因为文件被手改过。
        if isinstance(default, bool) and not isinstance(value, bool):
            logger.warning(
                "ui_preferences_bad_type",
                extra={"owner": str(owner_id), "key": key, "got": type(value).__name__},
            )
            continue
        values[key] = value
    return values


def save(owner_id: uuid.UUID | str, values: dict[str, Any]) -> dict[str, Any]:
    """合并写入，返回写完之后完整的一份。

    合并而不是覆盖：界面可能只送来它改过的那一项，而把没送来的键清掉会让"切换模式"
    顺手重置别的偏好。
    """
    current = load(owner_id)
    for key in OVERRIDABLE:
        if key not in values:
            continue
        value = values[key]
        default = DEFAULTS[key]
        if isinstance(default, bool) and not isinstance(value, bool):
            continue
        current[key] = value

    PREFERENCES_DIR.mkdir(parents=True, exist_ok=True)
    path = _path(owner_id)
    path.write_text(
        json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    logger.info("ui_preferences_saved", extra={"owner": str(owner_id), "keys": sorted(values)})
    return current
