.pragma library

// What a failure says to the person who hit it.
//
// **A raw exception is not a message.** The interface used to print whatever the runner
// caught — `UnicodeEncodeError: 'ascii' codec can't encode character '\u4e3a' in position
// 10: ordinal not in range(128)` — which names a Python codec, not a problem the reader
// can do anything about. That string is still kept (`raw`) and is still reachable: the
// panels that show it put it behind a "技术详情" toggle. Hiding it would leave nobody able
// to diagnose a report; leading with it is what made the product feel unfinished.
//
// Returns `{ title, hint, raw }`. `title` is one line, in the user's terms. `hint` says
// what to do about it, and is empty when there is nothing useful to say. `raw` is what was
// actually raised.

function human(message, code) {
    var raw = String(message === undefined || message === null ? "" : message)
    var c = String(code || "")
    var text = raw.toLowerCase()

    if (c === "timeout" || text.indexOf("timeout") >= 0) {
        return {
            title: "等待超时",
            hint: "模型或本地服务响应太慢。稍后重试；如果总是超时，换一个更小的目标。",
            raw: raw
        }
    }
    if (text.indexOf("unicodeencodeerror") >= 0 || text.indexOf("unicodedecodeerror") >= 0) {
        return {
            title: "内容里有无法保存的字符",
            hint: "API Key、文件名或输入内容里混进了不能用的字符。检查最近填写的那一项。",
            raw: raw
        }
    }
    if (text.indexOf("connectionrefused") >= 0 || text.indexOf("connection refused") >= 0
            || text.indexOf("cannot connect") >= 0) {
        return {
            title: "连不上本地服务",
            hint: "本地数据库或缓存还没起来。等几秒重试；一直不行就重启应用。",
            raw: raw
        }
    }
    if (text.indexOf("aiautherror") >= 0 || text.indexOf("401") >= 0
            || text.indexOf("api key") >= 0 || text.indexOf("api_key") >= 0
            || text.indexOf("unauthorized") >= 0) {
        return {
            title: "API Key 无效或没有配置",
            hint: "到「设置」重新填写。没有 Key 也能用，走的是内置离线引擎。",
            raw: raw
        }
    }
    if (text.indexOf("ratelimit") >= 0 || text.indexOf("429") >= 0
            || text.indexOf("insufficient") >= 0 || text.indexOf("quota") >= 0) {
        return {
            title: "模型服务暂时不接受请求",
            hint: "额度用完了或被限流了。看服务商后台，或过一会儿再试。",
            raw: raw
        }
    }
    if (text.indexOf("aihttperror") >= 0 || text.indexOf("aiprovidererror") >= 0
            || text.indexOf("http 5") >= 0 || text.indexOf("502") >= 0
            || text.indexOf("503") >= 0) {
        return {
            title: "模型服务返回了错误",
            hint: "通常是服务端的问题，稍后重试。",
            raw: raw
        }
    }
    if (text.indexOf("permissionerror") >= 0 || text.indexOf("没有权限") >= 0) {
        return {
            title: "没有权限访问这个位置",
            hint: "到「权限」里把对应的路径放开。",
            raw: raw
        }
    }
    if (text.indexOf("filenotfounderror") >= 0 || text.indexOf("no such file") >= 0) {
        return {
            title: "找不到这个文件",
            hint: "它可能被移动或删除了。重新选择一次。",
            raw: raw
        }
    }
    if (text.indexOf("notfounderror") >= 0 || text.indexOf("not found") >= 0
            || text.indexOf("no longer exists") >= 0) {
        return {
            title: "这条记录已经不在了",
            hint: "刷新一下列表。",
            raw: raw
        }
    }
    if (text.indexOf("validationerror") >= 0) {
        return {
            title: "填写的内容不符合要求",
            hint: raw.replace(/^.*validationerror[:\s]*/i, ""),
            raw: raw
        }
    }
    if (text.indexOf("jsondecodeerror") >= 0 || text.indexOf("non-json") >= 0) {
        return {
            title: "服务返回的内容无法解析",
            hint: "多半是模型服务返回了非预期的内容。重试一次。",
            raw: raw
        }
    }
    if (text.indexOf("rediserror") >= 0 || text.indexOf("redis") >= 0) {
        return {
            title: "缓存服务不可用",
            hint: "功能会变慢，其他都能继续用。",
            raw: raw
        }
    }
    if (text.indexOf("asyncpg") >= 0 || text.indexOf("sqlalchemy") >= 0
            || text.indexOf("operationalerror") >= 0 || text.indexOf("database") >= 0
            || text.indexOf("postgres") >= 0) {
        return {
            title: "数据库出错了",
            hint: "重启应用通常能恢复。",
            raw: raw
        }
    }
    if (text.indexOf("memoryerror") >= 0) {
        return {
            title: "内存不够了",
            hint: "关掉一些别的程序，或换一个更小的目标。",
            raw: raw
        }
    }
    if (text.indexOf("embedding_error") >= 0 || text.indexOf("未能生成向量") >= 0) {
        return {
            title: "资料没能建立索引",
            hint: "Embedding 服务不可用。关键词检索仍然可用。",
            raw: raw
        }
    }

    // Nothing recognised. Say the honest general thing and keep the original text one
    // toggle away, rather than putting a traceback on the screen.
    var firstLine = raw.split("\n")[0]
    if (firstLine.length > 160) firstLine = firstLine.substring(0, 160) + "…"
    return {
        title: "操作没有完成",
        hint: firstLine,
        raw: raw
    }
}

//: A one-line summary for a list row. Multi-line model output in a fixed-height row is
//: what made the run history overlap itself: a `Text` renders every line it is given, and
//: the row did not clip.
function firstLine(text, limit) {
    var s = String(text === undefined || text === null ? "" : text)
    var cut = s.indexOf("\n")
    if (cut >= 0) s = s.substring(0, cut)
    // Markdown that reads as noise at the start of a summary line.
    s = s.replace(/^[#>*\-\s]+/, "").replace(/\*\*/g, "")
    var max = limit || 120
    if (s.length > max) s = s.substring(0, max) + "…"
    return s
}
