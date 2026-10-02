#!/usr/bin/env python3
"""Claude Code PreToolUse guard — blocks system-modifying Bash commands.

The Console's Claude CLI runs headless (`--print --permission-mode dontAsk`), so it
cannot ask for interactive approval and would otherwise execute write operations
autonomously (the reported incident: a `systemctl restart` on a production DB host
via SSH). This hook enforces read-only-by-default at the tool layer.

Policy — DENY when:
  * a system-level operation appears (service control, package mgmt, reboot,
    dd/mkfs, iptables, user mgmt, docker/kubectl mutations, git push, …) — anywhere,
    including inside `ssh host '…'`; OR
  * a file-modifying operation (rm/mv/cp/chmod/chown/sed -i/tee/`>` redirect/…)
    targets a REMOTE host (ssh/scp/rsync present) or a SYSTEM path (/etc, /var, …); OR
  * scp/rsync copies TO a remote destination (pulling from one stays allowed).
ALLOW: read-only diagnostics (df, cat, journalctl, `systemctl status`, docker ps,
ssh '<read cmd>'), local file writes inside the workspace (/home/yolo/workspaces,
/tmp, relative paths) so the agent can still save reports/scripts, and `pip install`
into the agent's own venv (/home/yolo/pip/venv) — but not over ssh, not via sudo and
not into the system Python.

File tools (Write/Edit/MultiEdit/NotebookEdit) are checked by target path: inside
the workspace or /tmp they are free — the same line the Bash rules draw for local
writes — anywhere else (the agent's own CLAUDE.md, ~/.ssh/config, /etc, …) they need
the write window like any other change.

Wired via managed-settings.json → hooks.PreToolUse (matcher "Bash|Write|Edit|…"),
admin-scoped.
"""
import json
import os
import re
import sys
import time

# System-level, always destructive — block regardless of path/target.
_SYSTEM_OP = re.compile(
    r"""(?ix)
    \b(systemctl|service)\s+\S*\s*(restart|stop|start|reload|enable|disable|mask|unmask|kill)\b
    | \b(reboot|shutdown|halt|poweroff|init)\b
    | \b(apt|apt-get|aptitude|yum|dnf|zypper|snap)\s+(install|remove|purge|upgrade|autoremove|dist-upgrade)\b
    | \b(npm|yarn|pnpm|gem|cargo)\s+(install|uninstall|add|remove)\b
    | \bdocker\s+(run|rm|stop|start|restart|kill|rmi|pull|push|compose)\b
    | \bkubectl\s+(apply|delete|edit|scale|patch|create|replace|rollout|cordon|drain)\b
    | \bgit\s+(push|reset\s+--hard|clean)\b
    | \b(useradd|userdel|usermod|groupadd|passwd|chpasswd)\b
    | \b(iptables|nft|ufw|firewall-cmd)\b
    | \b(mount|umount|swapoff|swapon|mkfs\w*|mkswap|fdisk|parted|dd)\b
    | \bsysctl\s+-w\b
    | \b(kill|pkill|killall)\b
    | \bcrontab\b
    """,
    re.VERBOSE,
)

# File-modifying operations — dangerous only against remote hosts or system paths.
_FILE_WRITE = re.compile(
    r"""(?ix)
    \b(rm|rmdir|shred|unlink|truncate|mv|cp|chmod|chown|chgrp|ln|tee|touch|mkdir|install|patch)\b
    | \bsed\s+-i | \bperl\s+-i | \bawk\s+-i\b
    | \b(nano|vim?|vi|emacs)\b
    | >>?\s*(?!(&\s*\d|/dev/null|/dev/stderr|/dev/stdout))\S
    """,
    re.VERBOSE,
)

_REMOTE = re.compile(r"(?ix)\b(ssh|scp|rsync|sshpass)\b")
_SUDO = re.compile(r"(?ix)\bsudo\b")

# Python-Pakete: der Agent braucht gelegentlich eine Bibliothek, um überhaupt
# auswerten zu können (pandas für eine CSV, ein Parser für ein Logformat). Das
# venv unter /home/yolo/pip/venv liegt auf einem eigenen Volume, gehört dem Agenten
# und betrifft kein System — eine Installation dorthin ist keine Änderung, vor der
# dieser Hook schützen soll. Sie ist deshalb erlaubt, ABER nur lokal: auf einem
# entfernten Host, über sudo oder in das System-Python bleibt sie gesperrt.
_PIP_INSTALL = re.compile(
    r"(?ix)\b(?:python3?\s+-m\s+pip|uv\s+pip|pip3?)\s+"
    r"(install|uninstall|download)\b"
)
_PIP_SYSTEM_TARGET = re.compile(
    r"""(?ix)
    /usr/bin/pip
    | --break-system-packages
    | --(target|prefix|root)[=\s]+/(usr|etc|opt|var|srv|boot|lib|bin|sbin)\b
    """,
    re.VERBOSE,
)
_SYSTEM_PATH = re.compile(
    r"(?i)(^|[\s'\":=])/(etc|var|usr|boot|root|bin|sbin|lib|lib64|sys|proc|opt|srv|run)(/|\b)"
)

# Copying TO a remote host is a write on that host, even though no rm/tee/> appears
# anywhere — scp and rsync carry the verb in their argument order. Pulling FROM a
# host is read-only and stays allowed, so the destination decides: the last operand.
_SCP_LIKE = re.compile(r"(?ix)(?:^|[;&|]|\s)(scp|rsync)\s")
_REMOTE_OPERAND = re.compile(r"^(?!/|\./|\.\./|-)[A-Za-z0-9_.+-]+(?:@[A-Za-z0-9_.-]+)?:")


def _copies_to_remote(cmd: str) -> bool:
    """True when an scp/rsync in `cmd` writes to a remote destination."""
    for segment in re.split(r"[;&|]+", cmd):
        if not _SCP_LIKE.search(" " + segment.strip()):
            continue
        operands = [t for t in segment.split() if not t.startswith("-")]
        # operands[0] is the scp/rsync binary itself; the destination is the last one.
        if len(operands) >= 3 and _REMOTE_OPERAND.match(operands[-1]):
            return True
    return False


# Fernausführung, deren Inhalt der Hook nicht sieht: ein Skript per stdin/Pipe/Heredoc
# an ssh, eine Shell auf der Gegenseite (`bash -s`), kodierte oder zusammengesetzte
# Befehle. Ohne diese Regel wäre `ssh host 'bash -s' < x.sh` ein Freibrief — x.sh darf
# lokal geschrieben werden, und der Hook sähe nur "ssh host bash -s".
_SSH = r"(?<![-\w])(?:ssh|sshpass)(?![-\w])"
_REMOTE_INDIRECT = re.compile(
    rf"""(?ix)
    \|\s*{_SSH}                                  # … | ssh host
    | {_SSH}[^|;&]*\s<(?!<)\s*\S                  # ssh host … < datei
    | {_SSH}[^|;&]*<<                             # ssh host <<EOF
    | {_SSH}.*\b(bash|sh|zsh|dash|ksh)\s+-s\b
    | {_SSH}.*(\$\(|`|\bbase64\b|\bxxd\b|\beval\b|\bpython3?\s+-c\b|\bperl\s+-e\b)
    """,
    re.VERBOSE,
)
#: Python-Bibliotheken, die selbst eine SSH-Verbindung aufbauen — damit sähe der Hook
#: den entfernten Befehl nie als Shell-Text. Lesen geht über `ssh host 'befehl'`.
_PY_SSH_LIB = re.compile(r"\b(paramiko|asyncssh|fabric|netmiko|pexpect)\b")
# `ssh host` ohne entfernten Befehl ist eine interaktive Shell: was danach über stdin
# hineingeht (process_manage submit), sieht kein Hook mehr.
_SSH_OPTS_WITH_ARG = set("bcDEeFIiJLlmOopQRSWw")
_CMD_WRAPPERS = {"sshpass", "timeout", "nohup", "exec", "command", "env", "stdbuf", "nice"}


def _bare_ssh(cmd: str) -> bool:
    for segment in re.split(r"[;&|]+", cmd):
        toks = segment.split()
        for i, t in enumerate(toks):
            if t != "ssh" and not t.endswith("/ssh"):
                continue
            # Nur an Befehlsposition — `grep ssh /var/log/auth.log` ist keine Verbindung.
            if i > 0 and toks[0] not in _CMD_WRAPPERS:
                continue
            rest = toks[i + 1:]
            k = 0
            while k < len(rest) and rest[k].startswith("-"):
                flag = rest[k]
                k += 2 if (len(flag) == 2 and flag[1] in _SSH_OPTS_WITH_ARG) else 1
            if k >= len(rest) - 1:            # nur Ziel, kein Befehl
                return True
    return False


# Lokale Skripte und Python-Bibliotheken, die selbst eine Verbindung aufbauen.
_SCRIPT_RUN = re.compile(
    r"(?:^|[;&|(]\s*|\bdo\s+|\bthen\s+)"
    r"(?:(?:bash|sh|zsh|python3?|perl)\s+(?:-\w+\s+)*)?"
    r"((?:\./|/|~/)?[\w./~-]+\.(?:sh|py|pl|bash))(?![\w.])")
_SCRIPT_MAX = 256 * 1024


def _script_is_write(cmd: str, cwd: str = "") -> bool:
    """Ein ausgeführtes Skript wird mit denselben Regeln gelesen wie ein Befehl.

    Ein unlesbares oder zu großes Skript gilt als Schreibzugriff: was der Hook nicht
    prüfen kann, lässt er nicht durch.
    """
    for m in _SCRIPT_RUN.finditer(cmd):
        path = os.path.expanduser(m.group(1))
        if not os.path.isabs(path):
            path = os.path.join(cwd or "/home/yolo/workspaces", path)
        try:
            if os.path.getsize(path) > _SCRIPT_MAX:
                return True
            with open(path, encoding="utf-8", errors="replace") as fh:
                body = fh.read()
        except FileNotFoundError:
            continue                          # kein Skript (z. B. Argument mit .py-Endung)
        except OSError:
            return True
        if _is_write(body, cwd, _depth=1):
            return True
    return False


def _is_write(cmd: str, cwd: str = "", _depth: int = 0) -> bool:
    if _REMOTE_INDIRECT.search(cmd) or _bare_ssh(cmd) or _PY_SSH_LIB.search(cmd):
        return True
    if _depth == 0 and _script_is_write(cmd, cwd):
        return True
    if _SYSTEM_OP.search(cmd):
        return True
    if _FILE_WRITE.search(cmd):
        # Remote target (production) or a local system path → block.
        if _REMOTE.search(cmd) or _SYSTEM_PATH.search(cmd):
            return True
    if _copies_to_remote(cmd):
        return True
    if _PIP_INSTALL.search(cmd):
        # Only the local user venv is free. Note that this branch never *unblocks*
        # anything: the rules above have already had their say on the whole command,
        # so `pip install x && rm -rf /etc` is still caught by the file-write rule.
        if _REMOTE.search(cmd) or _SUDO.search(cmd) or _PIP_SYSTEM_TARGET.search(cmd):
            return True
    return False


#: Where the backend records a user-granted, time-limited write approval. It lives in
#: /opt because that directory is root-owned and the agent runs as yolo — the agent
#: therefore cannot forge its own approval, which is the whole point: consent has to
#: come from the human through the CentralStation UI, not from the conversation the
#: agent itself controls.
APPROVAL_FILE = "/opt/cs-write-approval.json"


def _approval() -> dict | None:
    """Return the active write approval, or None when absent/expired/unreadable."""
    try:
        with open(APPROVAL_FILE) as fh:
            data = json.load(fh)
        if float(data.get("expires_at", 0)) > time.time():
            return data
    except Exception:
        pass
    return None


#: Claude's own file tools. They write without a shell, so the Bash rules never see
#: them; each names its target in one of these input keys.
FILE_TOOLS = {"Write": "file_path", "Edit": "file_path", "MultiEdit": "file_path",
              "NotebookEdit": "notebook_path"}

#: Where a file tool may write without approval. Same line as the Bash rules: the
#: shared workspace (reports, scripts, uploads) and scratch space.
FREE_WRITE_ROOTS = ("/home/yolo/workspaces", "/tmp")


def _file_write_outside(path: str, cwd: str = "") -> bool:
    """True when a file tool would write outside FREE_WRITE_ROOTS.

    realpath first, so neither `..` nor a symlink inside the workspace that points at
    ~/.claude or /etc slips through. An empty path is treated as outside — a tool
    call whose target cannot be named is not one to wave through.
    """
    if not path:
        return True
    if not os.path.isabs(path):
        path = os.path.join(cwd or "/home/yolo/workspaces", path)
    real = os.path.realpath(path)
    return not any(real == root or real.startswith(root + "/") for root in FREE_WRITE_ROOTS)


#: MCP-Werkzeuge: zugelassen ist, was mit einem Lese-Verb beginnt; alles andere
#: (quittieren, Downtime, Discovery, löschen …) braucht die Freigabe. Eine Liste der
#: ERLAUBTEN Verben, nicht der verbotenen — ein neues Werkzeug ist damit gesperrt,
#: bis jemand es bewusst als lesend einordnet.
MCP_READ_VERBS = {"get", "list", "search", "show", "find", "describe", "query", "read",
                  "check", "compare", "debug", "test", "validate", "wait", "baking",
                  "checkmk", "download", "count", "status", "fetch", "lookup", "explain"}
#: Playwright: lesen und blättern ja; klicken, texample, Formulare, Uploads, Skripte nein —
#: damit ließe sich jede Weboberfläche bedienen (CheckMK, Proxmox, Jira …).
BROWSER_READ = {"navigate", "navigate_back", "snapshot", "take_screenshot", "tabs",
                "console_messages", "network_requests", "network_request", "wait_for",
                "find", "hover", "resize", "close", "install"}
#: Der CentralStation-MCP-Server prüft die Freigabe selbst (mcp_server._require_write);
#: zweimal fragen hieße, dass eine Stelle die Regel nicht kennt.
SELF_GUARDED_SERVERS = {"centralstation"}


def _mcp_parts(tool: str) -> tuple[str, str] | None:
    """(server, werkzeug) aus "mcp__srv__tool" (Claude) oder "mcp_srv_tool" (Hermes)."""
    if tool.startswith("mcp__"):
        parts = tool[5:].split("__", 1)
        return (parts[0], parts[1]) if len(parts) == 2 else None
    if tool.startswith("mcp_"):
        rest = tool[4:]
        for srv in sorted(SELF_GUARDED_SERVERS | {"playwright", "vibemk", "awx-ng", "awx_ng"},
                          key=len, reverse=True):
            if rest.startswith(srv + "_"):
                return srv, rest[len(srv) + 1:]
        parts = rest.split("_", 1)
        return (parts[0], parts[1]) if len(parts) == 2 else None
    return None


def _mcp_is_write(server: str, name: str) -> bool:
    if server in SELF_GUARDED_SERVERS:
        return False
    if server == "playwright":
        return name.removeprefix("browser_") not in BROWSER_READ
    verbs = name.split("_")
    if verbs and verbs[0] == server.replace("-", "_").split("_")[0]:
        verbs = verbs[1:]                     # vibemk_get_… → get_…
    return not verbs or verbs[0] not in MCP_READ_VERBS


#: Jede Sperre und jede Freigabe-Nutzung mit dem Befehl — sonst sieht man im Nachhinein
#: nicht, WAS gesperrt wurde (Hermes fängt den Aufruf vor seiner eigenen Protokollzeile
#: ab), und ein Fehlalarm bliebe unerkannt.
AUDIT_LOG = "/tmp/cs-readonly-guard.log"
_current: dict = {}


def _audit(decision: str) -> None:
    try:
        with open(AUDIT_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                 "decision": decision, "tool": _current.get("tool"),
                                 "input": json.dumps(_current.get("input"),
                                                     ensure_ascii=False)[:600]},
                                ensure_ascii=False) + "\n")
    except Exception:
        pass


def _deny(reason: str) -> None:
    _audit("deny")
    # Block via BOTH mechanisms for cross-version compatibility:
    #  - JSON permissionDecision "deny" (newer Claude Code), and
    #  - exit code 2 with the reason on stderr (classic PreToolUse block).
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        },
        # Hermes (agent/shell_hooks.py) liest "decision"/"reason" — derselbe Hook dient
        # beiden Agenten, eine Regelstelle.
        "decision": "block",
        "reason": reason,
    }))
    sys.stderr.write(reason + "\n")
    sys.exit(2)


def _allow_if_approved() -> None:
    granted = _approval()
    if granted:
        _audit("approved")
        # The user opened a write window in the CentralStation console. Let the
        # call through and leave a trace of whose approval was used.
        sys.stderr.write(
            "cs-readonly-guard: Schreibfreigabe aktiv (erteilt von "
            f"{granted.get('granted_by', '?')}, gültig bis "
            f"{granted.get('expires_at_iso', '?')}) — zugelassen.\n"
        )
        sys.exit(0)


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)  # unreadable input → do not interfere
    tool = data.get("tool_name")
    tool_input = data.get("tool_input") or {}
    _current.update(tool=tool, input=tool_input)

    if tool in FILE_TOOLS:
        path = tool_input.get(FILE_TOOLS[tool], "") or ""
        if _file_write_outside(path, data.get("cwd", "")):
            _allow_if_approved()
            _deny(
                f"READ-ONLY-MODUS: {tool} auf {path or '(ohne Pfad)'} liegt außerhalb "
                "des Workspace und wurde BLOCKIERT. Frei beschreibbar sind "
                "/home/yolo/workspaces und /tmp — lege Berichte, Skripte und Notizen "
                "dort an. Soll wirklich eine Datei außerhalb geändert werden, beschreibe "
                "dem Nutzer Pfad und Änderung und bitte ihn, in der CentralStation-"
                "Konsole \"Schreibzugriff freigeben\" zu klicken; eine Zustimmung im "
                "Chat hebt die Sperre nicht auf."
            )
        sys.exit(0)

    mcp = _mcp_parts(tool or "")
    if mcp is not None:
        if _mcp_is_write(*mcp):
            _allow_if_approved()
            _deny(
                f"READ-ONLY-MODUS: Das Werkzeug {tool} verändert ein System und wurde "
                "BLOCKIERT. Lesende Werkzeuge (get/list/search/…) sind frei. Beschreibe dem "
                "Nutzer die geplante Aktion (Werkzeug, Ziel, Wirkung) und bitte ihn, in der "
                "CentralStation-Konsole \"Schreibzugriff freigeben\" zu klicken; eine "
                "Zustimmung im Chat hebt die Sperre nicht auf."
            )
        sys.exit(0)

    if tool == "process_manage":
        # Eingabe in einen laufenden Prozess (z. B. eine offene ssh-Sitzung): der Inhalt
        # wird wie ein Befehl auf einem ENTFERNTEN Host behandelt — welcher Prozess es
        # ist, sieht der Hook nicht.
        if (tool_input.get("action") in ("write", "submit")):
            data = tool_input.get("data", "") or ""
            if _SYSTEM_OP.search(data) or _FILE_WRITE.search(data) or _is_write(data):
                _allow_if_approved()
                _deny("READ-ONLY-MODUS: Diese Eingabe in einen laufenden Prozess würde ein "
                      "System verändern und wurde BLOCKIERT. Bitte den Nutzer, in der "
                      "CentralStation-Konsole \"Schreibzugriff freigeben\" zu klicken.")
        sys.exit(0)

    # Claude: Bash{command}; Hermes: terminal{command}.
    if tool not in ("Bash", "terminal"):
        sys.exit(0)
    cmd = tool_input.get("command", "") or ""
    if _is_write(cmd, data.get("cwd", "")):
        _allow_if_approved()
        _deny(
            "READ-ONLY-MODUS: Dieser Befehl verändert ein System "
            "(Schreiboperation, evtl. auf einem Produktionssystem via SSH) und wurde "
            "BLOCKIERT. Wichtig: eine Zustimmung im Chat hebt diese Sperre NICHT auf "
            "— der Hook sieht die Konversation nicht, und du kannst ihn nicht "
            "umgehen. Beschreibe dem Nutzer die geplante Änderung (genauer Befehl + "
            "Zielsystem + Wirkung) und bitte ihn, in der CentralStation-Konsole "
            "\"Schreibzugriff freigeben\" zu klicken; danach ist der Befehl für die "
            "Dauer des Zeitfensters erlaubt und du kannst ihn erneut ausführen. "
            "Reine Lese-Diagnose und lokale Workspace-Dateien sind immer erlaubt."
        )
    sys.exit(0)


if __name__ == "__main__":
    main()
