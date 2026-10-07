# Permissions

What the agent may do to your machine, and why the defaults are what they are.

---

## 1. The shape of the model

Three ideas carry the whole design.

**Levels, not booleans.** A user does not want to choose between "the agent can do
nothing" and "the agent can do anything". Four levels map onto how people actually
think about trust, and the default sits in the middle: work freely inside the
project, ask before going further.

**Deny beats allow, always.** A path is checked against the denied list *before*
the allowed list, and the denied list cannot be overridden by a broader allow
entry. That is what keeps `~/.ssh` out of reach even when the whole user directory
has been granted.

**Friction is proportional to consequence.** Reading a file in the workspace and
running the test suite never prompt — that is the entire point of having an agent.
Deleting a directory tree or rewriting git history always prompt, and say what,
where, why and what it will cost.

---

## 2. Levels

| Level | Name | Can | Cannot |
|---|---|---|---|
| 0 | **Observe only** | read files, search, list processes, screenshot | create, modify, delete, run, reach the network |
| 1 | **Workspace** *(default)* | create/modify files in granted roots, run the project's tests, lint, typecheck, build | run arbitrary shell commands, install anything, reach the network |
| 2 | **System** | PowerShell, cmd, Python, Node, install dependencies, manage processes, download | bypass the sandbox; high-risk commands still prompt |
| 3 | **Full control** | broad system access; off by default and user-enabled only | — |

Tool count by level, measured by building the registry at each level
(`AgentPolicy(mode=SUPERVISED, level=N)`, default capability flags):
**0 → 16 tools, 1 → 34, 2 → 44, 3 → 44.** The step from 1 to 2 is the ten
terminal, process and network tools; 2 and 3 differ in reach, not in count —
no capability is gated above `SYSTEM`.

The counts move whenever a tool is added, so they are dated rather than
absolute: these were measured at `c6bad76`. The two tools that separate them
from the previous numbers (`0 → 15, 1 → 32, 2 → 42, 3 → 42`) are
`document_read` and `document_write`.

---

## 3. Modes

| Mode | Behaviour |
|---|---|
| **Assist** | analysis and proposals only. 15 tools, **zero** that mutate anything. |
| **Supervised** *(default)* | executes; anything risky asks first. |
| **Autonomous** | completes the task within the granted scope. |

Autonomous is **not** a bypass, but it is a wider licence than it looks. It
allows **medium** and **high** risk commands without asking — recursive deletes,
`git reset --hard`, registry writes — because that is the scope the user just
granted. Only **critical** still stops: disk format, account changes, powering
off the machine, requesting elevation, and piping a download into an interpreter.

The honest reading: granting autonomous mode is a decision about the machine, not
a formality. Registry changes, for instance, are classified `high`, so in
autonomous mode they proceed without a prompt.

---

## 4. Protected locations

These are merged in from the built-in policy on **every** load. A settings file can
add protections; it can never remove them.

```
~/.ssh              ~/.aws              ~/.azure            ~/.kube
~/.gnupg            ~/.docker/config.json
%LOCALAPPDATA%\Google\Chrome\User Data
%LOCALAPPDATA%\Microsoft\Edge\User Data
%APPDATA%\Mozilla\Firefox\Profiles
%LOCALAPPDATA%\Microsoft\Credentials
%APPDATA%\Microsoft\Credentials
%LOCALAPPDATA%\Microsoft\Vault
%WINDIR%            %ProgramFiles%      %ProgramFiles(x86)%   %ProgramData%
<project>/.env      <project>/.runtime/ai-config.json
```

The last two matter: an agent working inside this repository must not be able to
read the API key that pays for it.

**Verified.** A request to clear the denied list still leaves `~/.ssh` unreachable
— the settings endpoint is not a privilege-escalation path.

---

## 5. Path checking

Two checks, at different granularities, and both matter:

1. the **executor** checks the tool — is `file_read` permitted at all?
2. the **tool** checks the concrete path — is *this* file permitted?

The second is what stops `file_read("~/.ssh/id_rsa")` from a permitted tool.

Path comparison is done on **resolved path components**, not string prefixes, so
`C:\work\app-secrets` is not treated as being inside `C:\work\app`.

Tools taking two paths (`file_copy`, `file_move`) check **both ends** — otherwise
the agent could read a secret and write it somewhere readable.

---

## 6. Command risk classification

Matched before anything runs. The reason travels with the verdict so the
confirmation dialog can explain itself. The table is the whole of
`_DANGEROUS_COMMANDS` plus the network fallback (`permissions.py`), in the order
the code checks them:

| Pattern | Risk | Reason |
|---|---|---|
| `rm -rf`, `rmdir /s`, `del /f/s/q`, `Remove-Item -Recurse` | high | recursive or forced delete |
| `Remove-Item -Force` | medium | forced delete |
| `format C:` | critical | disk format |
| `mkfs` | critical | filesystem creation |
| `diskpart` | critical | disk partitioning |
| `git reset --hard` | high | discards uncommitted work |
| `git clean -fd` | high | deletes untracked files |
| `git push --force` | high | rewrites remote history |
| `git filter-branch` | high | rewrites history |
| `git branch -D` | medium | force-deletes a branch |
| `reg add/delete/import`, `Set-ItemProperty HKLM` | high | modifies the registry |
| `setx` | medium | changes persistent environment variables |
| `icacls`, `takeown`, `cacls` | high | changes file permissions |
| `net user`, `net localgroup` | critical | changes user accounts |
| `schtasks /create` | high | creates a scheduled task |
| `sc create/delete/config` | high | changes a Windows service |
| `shutdown`, `Restart-Computer`, `Stop-Computer` | critical | powers off the machine |
| `runas` | critical | runs as another user |
| `-Verb RunAs`, `Start-Process -Verb RunAs` | critical | requests administrator elevation |
| `iwr`/`curl`/`wget` piped into `iex`/`bash`/`sh`/`python` | critical | executes downloaded code |
| `Invoke-Expression` | high | evaluates a string as code |
| `curl`, `wget`, `Invoke-WebRequest`, `Invoke-RestMethod`, `git clone`, `npm install`, `pip install` | medium | downloads from the network |

The last row is a **fallback**: it applies only when no pattern above matched, so
a command that is both a download and a recursive delete is reported as the
delete, with its reason, rather than as a download.

Classification is **textual and therefore advisory, not a sandbox**. It exists to
raise the right prompt, not to make an arbitrary command safe. The real limits are
the permission level, the workspace boundary and the timeout.

---

## 7. Confirmation

When a verdict is `CONFIRM`, the executor blocks on a broker and the UI shows a
dialog with five things:

| Field | Example |
|---|---|
| operation | `Run a PowerShell command` |
| target | `git reset --hard` |
| reason | `Flagged as high risk: discards uncommitted work` |
| risk | `high` |
| impact | `This can permanently destroy data.` |

The wait is bounded. **An unanswered request is a timeout, not an implicit yes** —
the safe default for "nobody was looking" is to do nothing.

---

## 8. Network

The agent may only reach hosts on the allowed list:

```
github.com  raw.githubusercontent.com  pypi.org  files.pythonhosted.org
registry.npmjs.org  npmjs.com  api.deepseek.com  docs.python.org
developer.mozilla.org  stackoverflow.com  localhost  127.0.0.1
```

A host that is not on the list prompts. A blocked host is refused outright.
Downloads are size-capped **while streaming**, so a 4 GB file cannot fill the disk
before anyone notices, and an existing file is never overwritten without
permission.

---

## 9. What the agent still cannot do

- reach the database directly — business tools go through Service → Repository
- read the API keys it runs on
- remove a built-in protection through any settings endpoint
- bypass UAC, disable Defender, or hide its execution
- act without a record: every invocation, allowed or refused, is audited

---

*Checked against the source at `c6bad76` (2026-10-07): the four levels and their
defaults, the per-level tool counts (measured by building the registry, not read off
the code), the mode table, the 18 built-in protected paths, the command classification
table, the five confirmation fields and the network allow-list were each compared with
the implementation. The tool counts, the autonomous-mode paragraph and the risk table
are the corrections that check produced. The rest of the document was compared item by
item and matched.*
