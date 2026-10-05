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
| 0 | **Observe only** | read files, search, list processes, read the web, screenshot | create, modify, delete, run |
| 1 | **Workspace** *(default)* | create/modify files in granted roots, run the project's tests, lint, typecheck, build | run arbitrary shell commands, install anything, reach the network |
| 2 | **System** | PowerShell, cmd, Python, Node, install dependencies, manage processes, download | bypass the sandbox; high-risk commands still prompt |
| 3 | **Full control** | broad system access; off by default and user-enabled only | — |

Tool count by level, measured: **0 → 15 tools, 1 → 32, 2 → 42, 3 → 42.**

---

## 3. Modes

| Mode | Behaviour |
|---|---|
| **Assist** | analysis and proposals only. 14 tools, **zero** that mutate anything. |
| **Supervised** *(default)* | executes; anything risky asks first. |
| **Autonomous** | completes the task within the granted scope. |

Autonomous is **not** a bypass. Critical commands (disk format, registry, privilege
escalation) still stop and ask, because autonomy is not a licence for the handful
of operations that can destroy the machine.

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
confirmation dialog can explain itself.

| Pattern | Risk | Reason |
|---|---|---|
| `rm -rf`, `rmdir /s`, `del /f/s/q`, `Remove-Item -Recurse` | high | recursive or forced delete |
| `format C:` | critical | disk format |
| `git reset --hard` | high | discards uncommitted work |
| `git clean -fd` | high | deletes untracked files |
| `git push --force` | high | rewrites remote history |
| `reg add/delete`, `Set-ItemProperty HKLM` | high | modifies the registry |
| `icacls`, `takeown` | high | changes file permissions |
| `net user`, `net localgroup` | critical | changes user accounts |
| `shutdown`, `Restart-Computer` | critical | powers off the machine |
| `runas`, `-Verb RunAs` | critical | requests elevation |
| `iwr ... \| iex` | critical | executes downloaded code |
| `curl`, `wget`, `npm install`, `pip install` | medium | downloads from the network |

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
