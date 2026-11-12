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

Wired for all three Console agents, always from a root-owned place the agent cannot edit:
  * Claude CLI: /etc/claude-code/managed-settings.json → hooks.PreToolUse
  * Codex CLI:  /etc/codex/requirements.toml → hooks.PreToolUse (tool names Bash,
    apply_patch, mcp__srv__tool — same dialect as Claude)
  * Hermes:     Python pre_tool_call hook in userenv/main.py (_write_guard_hook)
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
    | \b(apt|apt-get|aptitude|yum|dnf|zypper|snap)\s+(install|remove|purge|upgrade|autoremove|dist-upgrade)\b
    | \b(npm|yarn|pnpm|gem|cargo)\s+(install|uninstall|add|remove)\b
    | \bdocker\s+(run|rm|stop|start|restart|kill|rmi|pull|push|compose)\b
    | \bkubectl\s+(apply|delete|edit|scale|patch|create|replace|rollout|cordon|drain)\b
    | \bgit\s+(push|reset\s+--hard|clean)\b
    | \bsysctl\s+-w\b
    """,
    re.VERBOSE,
)
#: Systembefehle, die schon als einzelnes WORT gefährlich sind. Geprüft wortgenau über
#: _has_word (12.11.: `/etc/crontab`, `/etc/cron.d/aim-reboot` in Lese-Diagnosen auf
#: aim012 sperrten, weil der Teilstring-Ausdruck den Pfad als Befehl las).
_SYSTEM_WORD_RE = re.compile(
    r"(?i)\b(reboot|shutdown|halt|poweroff|init|useradd|userdel|usermod|groupadd|passwd|"
    r"chpasswd|iptables|nft|ufw|firewall-cmd|mount|umount|swapoff|swapon|mkfs\w*|mkswap|"
    r"fdisk|parted|dd|kill|pkill|killall|crontab)\b")
_SYSTEM_OP_STRICT = re.compile(_SYSTEM_OP.pattern + r" | " + _SYSTEM_WORD_RE.pattern[4:],
                               re.VERBOSE | re.IGNORECASE)

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

# Dieselben Regeln getrennt: Schreib-WÖRTER (Befehlsnamen) und Schreib-OPERATIONEN.
# Die Wörter prüft _write_word wortgenau (`ls install/` ist kein install), die
# Operationen bleiben ein Ausdruck über den Text ohne träge Argumente.
_WRITE_WORD_RE = re.compile(
    r"(?i)\b(rm|rmdir|shred|unlink|truncate|mv|cp|chmod|chown|chgrp|ln|tee|touch|mkdir|"
    r"install|patch|nano|vim?|emacs)\b")
_FILE_WRITE_OPS = re.compile(
    r"""(?ix)
    \bsed\s+-i | \bperl\s+-i | \bawk\s+-i\b
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

# Befehlsersetzung in einem ssh-Befehl: `for f in $(ls -tr x.log*); do grep … $f; done`
# ist reine Diagnose — das Ergebnis wird nur als ARGUMENT gelesen. Gefährlich ist sie,
# wenn das Ergebnis selbst AUSGEFÜHRT wird (`$(cat cmd.txt)`, `sudo $(…)`) oder der
# Inhalt schreibt. Deshalb: Inhalt mit denselben Regeln prüfen, Befehlsposition sperren,
# dann durch einen Platzhalter ersetzen und den Rest wie gehabt prüfen. Anlass 13.10.:
# eine Paperless-Diagnose auf sammelpdf wurde nur wegen `$(ls -tr …)` gesperrt.
#: Davor stehend wird das Folgende ausgeführt (Befehlsposition), nicht als Argument gelesen.
_EXEC_WORDS = {"do", "then", "else", "elif", "if", "while", "until", "!", "{", "(",
               "sudo", "exec", "command", "builtin", "env", "nohup", "nice", "time",
               "timeout", "xargs", "stdbuf", "watch", "eval", "source", ".", "su",
               "bash", "sh", "zsh", "dash", "ksh", "-c", "--", "-n", "-u", "-i", "-E"}
_ASSIGN = re.compile(r"^[A-Za-z_]\w*=")
_SEGMENT_SPLIT = re.compile(r"\|\||&&|[;&|\n(]")


def _command_position(before: str) -> bool:
    """Steht etwas nach `before` an der Stelle eines Befehls (statt eines Arguments)?"""
    seg = _SEGMENT_SPLIT.split(before)[-1]
    words = [w.strip("'\"") for w in seg.split()]
    words = [w for w in words if w and not _ASSIGN.match(w)]
    # `ssh [optionen] host …`: der entfernte Befehl beginnt nach dem Host.
    for i, w in enumerate(words):
        if w in ("ssh", "sshpass") or w.endswith("/ssh"):
            k = i + 1
            while k < len(words) and words[k].startswith("-"):
                f = words[k]
                k += 2 if (len(f) == 2 and f[1] in _SSH_OPTS_WITH_ARG) else 1
            words = words[k + 1:]
            break
    # Nur Wrapper/Schlüsselwörter/Optionen/Zahlen davor → das Ergebnis wird ausgeführt.
    return all(w in _EXEC_WORDS or w.startswith("-") or w.isdigit() for w in words)


def _substitutions(cmd: str) -> list[tuple[int, int, str | None]] | None:
    """Alle $(…), `…` und $((…)) der obersten Ebene als (start, ende, inhalt); Inhalt None =
    reine Rechnung. None = unbalanciert oder Befehl in einer Rechnung."""
    out, i, n = [], 0, len(cmd)
    while i < n:
        if cmd.startswith("$((", i):            # Arithmetik — kein Befehl
            depth, j = 0, i + 1
            while j < n:
                depth += cmd[j] == "("
                depth -= cmd[j] == ")"
                j += 1
                if depth == 0:
                    break
            inner = cmd[i + 3:j - 2]
            if "$(" in inner or "`" in inner:   # Befehl in der Rechnung: wie Ersetzung
                return None
            out.append((i, j, None))
            i = j
            continue
        if cmd.startswith("$(", i):
            depth, j = 1, i + 2
            while j < n and depth:
                depth += cmd[j] == "("
                depth -= cmd[j] == ")"
                j += 1
            if depth:
                return None
            out.append((i, j, cmd[i + 2:j - 1]))
            i = j
            continue
        if cmd[i] == "`":
            j = cmd.find("`", i + 1)
            if j < 0:
                return None
            out.append((i, j + 1, cmd[i + 1:j]))
            i = j + 1
            continue
        i += 1
    return out


_SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}
_INTERPRETERS = re.compile(r"^(?:python[\d.]*|perl|ruby|node)$")
_PIPE_TARGET = re.compile(r"(?<!\|)\|(?!\|)\s*([^;&|\n]*)")


def _pipe_into_shell(cmd: str) -> bool:
    """`ssh h 'cat x | sh'`: die Gegenseite führt Text aus, den der Hook nie sieht.

    Shell: nur Optionen bis zum Trenner (`sh`, `sh -x`, `bash -s`) liest das Skript von
    stdin; `sh -c '…'` nicht (dessen Text prüfen die übrigen Regeln). Interpreter: nackt
    oder `-` liest stdin; `python3 -m json.tool` bleibt lesend.
    """
    if not re.search(_SSH, cmd):
        return False
    for m in _PIPE_TARGET.finditer(cmd):
        words = [w.strip("'\")") for w in m.group(1).split()]
        words = [w for w in words if w]
        if words and words[0] == "sudo":
            words = words[1:]
            while words and words[0].startswith("-"):
                words = words[1:]
        if not words:
            continue
        prog, args = words[0].rsplit("/", 1)[-1], words[1:]
        if prog in _SHELLS and all(a.startswith("-") and a != "-c" and "c" not in a[1:2]
                                   for a in args):
            return True
        if _INTERPRETERS.match(prog) and (not args or args == ["-"]):
            return True
    return False


#: `$var` / `${var}` an Befehlsposition führt aus, was in der Variablen steht —
#: `c=$(echo rm); $c -rf /x` wäre sonst der Umweg um die Prüfung der Ersetzung.
_VAR_AT_CMD = re.compile(r"(?:^|\|\||&&|[;&|\n(]|\b(?:do|then|else|sudo|exec|xargs|env)\b)"
                         r"\s*(?:[A-Za-z_]\w*=\S*\s+)*[\"']?\$\{?[A-Za-z_]")


def _ssh_substitution_write(cmd: str, cwd: str, depth: int) -> tuple[bool, str]:
    """(schreibend?, Befehl mit Platzhaltern statt geprüfter Ersetzungen)."""
    if not re.search(_SSH, cmd) or not ("$(" in cmd or "`" in cmd or "$" in cmd):
        return False, cmd
    if _VAR_AT_CMD.search(cmd):
        return True, cmd
    subs = _substitutions(cmd)
    if subs is None or depth > 3:
        return True, cmd
    out, last = [], 0
    for start, end, inner in subs:
        if inner is None:                       # $((…)) ohne Befehl: nur eine Zahl
            out.append(cmd[last:start] + "0")
            last = end
            continue
        if _command_position(cmd[:start]):
            return True, cmd
        # Als entfernter Befehl prüfen: `rm x.txt` ist dort kein Workspace-Schreiben.
        if _is_write(f"ssh SUBSTHOST {inner}", cwd, _depth=depth + 1):
            return True, cmd
        out.append(cmd[last:start] + "SUBST")
        last = end
    out.append(cmd[last:])
    return False, "".join(out)
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


#: Die eigene Konfiguration der Agenten. Wer sie ändert, ändert, was der Agent darf
#: (MCP-Server, Zugangsdaten, Anweisungen) — das ist eine Systemänderung, auch wenn der
#: Pfad im Home liegt.
_AGENT_CONFIG_PATH = re.compile(
    r"(?:^|[\s'\":=])(?:~|\$HOME|\$\{HOME\}|/home/yolo)/"
    r"\.(?:codex|claude|hermes|ssh|profile|bashrc|bash_profile|config/claude|npmrc|pip)(?:/|\b)")


# ── Träge Argumente ausblenden (Anlass 11./12.11.2026) ────────────────────────────────
# Die Regeln oben sind reguläre Ausdrücke über den ganzen Befehlstext. Sie sehen deshalb
# auch das `>` in einem sed-Muster (`sed -E 's/(pass=)[^ ]+/\1<…>/'`) oder in einem
# grep-Muster (`grep -oE "^<user:[^>]*>"`) als Umleitung — und mit `ssh` davor als
# Schreiben auf Produktion. Gesperrt wurden so reine Lese-Diagnosen mit Maskierung der
# Geheimnisse (Audit-Log /tmp/cs-readonly-guard.log, 12.11. 11:35:32 und 11:36:36).
# Abhilfe: ein kleiner Shell-Lexer ersetzt die GEQUOTETEN Argumente von Programmen, die
# nicht schreiben können, durch einen Platzhalter, bevor die Regeln laufen. Nur diese:
_INERT_PROGS = {"grep", "egrep", "fgrep", "zgrep", "zegrep", "rg", "echo", "printf", "jq"}
#: sed und awk sind Programme mit eigener Sprache — ausgeblendet nur, wenn das Skript
#: nachweislich nicht schreibt/ausführt (_sed_script_writes, _awk_program_writes).
_SCRIPT_PROGS = {"sed", "gsed", "awk", "gawk", "mawk", "nawk"}
_WRAPPERS = {"sudo", "env", "nohup", "nice", "stdbuf", "command", "exec", "time", "timeout",
             "xargs"}
_SEPARATORS = {"|", "||", "&&", ";", "&", "(", ")", "\n"}


class _LexError(ValueError):
    pass


def _lex(cmd: str) -> list[tuple[str, int, int, str]]:
    """Shell-Wörter als (art, start, ende, wert): art word | op | redir; wert ohne Quotes.

    Wie bash für einfache Befehle: '…' wörtlich, "…" mit \\-Escapes, $'…', \\ außerhalb.
    Unbalancierte Quotes → _LexError (der Aufrufer bleibt dann beim strengen Text).
    """
    out, i, n = [], 0, len(cmd)
    while i < n:
        c = cmd[i]
        if c in " \t":
            i += 1
            continue
        if c == "\n":
            out.append(("op", i, i + 1, "\n"))
            i += 1
            continue
        two = cmd[i:i + 2]
        if two in ("||", "&&"):
            out.append(("op", i, i + 2, two))
            i += 2
            continue
        if c in "|;&()":
            out.append(("op", i, i + 1, c))
            i += 1
            continue
        if c in "<>":
            j = i + 1
            while j < n and cmd[j] in "<>&|":
                j += 1
            out.append(("redir", i, j, cmd[i:j]))
            i = j
            continue
        start, val = i, []
        while i < n and cmd[i] not in " \t\n|;&()<>":
            c = cmd[i]
            if c == "\\":
                if i + 1 < n:
                    val.append(cmd[i + 1])
                i += 2
            elif c == "'" or (c == "$" and cmd[i + 1:i + 2] == "'"):
                i += 2 if c == "$" else 1
                j = cmd.find("'", i)
                while c == "$" and j > 0 and cmd[j - 1] == "\\":
                    j = cmd.find("'", j + 1)
                if j < 0:
                    raise _LexError("offenes '")
                val.append(cmd[i:j])
                i = j + 1
            elif c == '"':
                i += 1
                while i < n and cmd[i] != '"':
                    if cmd[i] == "\\" and i + 1 < n and cmd[i + 1] in '"\\$`\n':
                        val.append(cmd[i + 1])
                        i += 2
                    else:
                        val.append(cmd[i])
                        i += 1
                if i >= n:
                    raise _LexError('offenes "')
                i += 1
            else:
                val.append(c)
                i += 1
        # Ziffer direkt vor > / < gehört zur Umleitung (2>, 1>>)
        if i < n and cmd[i] in "<>" and cmd[start:i].isdigit():
            continue_from = i
            j = continue_from + 1
            while j < n and cmd[j] in "<>&|":
                j += 1
            out.append(("redir", start, j, cmd[start:j]))
            i = j
            continue
        out.append(("word", start, i, "".join(val)))
    return out


def _sed_script_writes(script: str) -> bool:
    """Schreibt/ausführt ein sed-Skript? w/W-Befehl, e-Befehl, s///w oder s///e. Was der
    kleine Parser nicht versteht, gilt als schreibend."""
    i, n = 0, len(script)

    def skip_delim(i: int, d: str) -> int:
        while i < n and script[i] != d:
            i += 2 if script[i] == "\\" else 1
        if i >= n:
            raise _LexError("sed: offenes Trennzeichen")
        return i + 1

    try:
        while i < n:
            c = script[i]
            if c in " \t\n;{}!,0123456789$~+":
                i += 1
                continue
            if c == "/":
                i = skip_delim(i + 1, "/")
                while i < n and script[i] in "IM":
                    i += 1
                continue
            if c == "\\" and i + 1 < n:             # \cREGEXc
                i = skip_delim(i + 2, script[i + 1])
                continue
            if c in "wWe":
                return True
            if c in "sy":
                if i + 1 >= n:
                    return True
                d = script[i + 1]
                i = skip_delim(i + 2, d)
                i = skip_delim(i, d)
                j = i
                while j < n and script[j] not in ";\n}":
                    j += 1
                if c == "s" and re.search(r"[we]", script[i:j]):
                    return True
                i = j
                continue
            if c in "aicrRbtTv:#lLqQ":
                if c == "#":                       # Kommentar bis Zeilenende
                    j = script.find("\n", i)
                    i = n if j < 0 else j
                    continue
                j = i + 1
                stop = "\n" if c in "aic" else ";\n}"      # a/i/c: Text bis Zeilenende
                while j < n and script[j] not in stop:
                    j += 1
                i = j
                continue
            if c in "pdDnNPhHgGxzF=":
                i += 1
                continue
            return True
    except _LexError:
        return True
    return False


_AWK_WRITE = re.compile(r"\bsystem\s*\(|(?<!\|)\|(?!\|)|\b(?:print|printf)\b[^;}\n]*>|\bclose\s*\(")


def _awk_program_writes(prog: str) -> bool:
    """Schreibt/ausführt ein awk-Programm? system(), Pipes (`| "cmd"`, `"cmd" | getline`),
    print/printf mit Umleitung. `>=`-Vergleiche in Bedingungen bleiben lesend."""
    return bool(_AWK_WRITE.search(prog))


def _mask_simple(words: list[tuple[str, int, int, str]], depth: int,
                 repl: list[tuple[int, int, str]], cmd: str, flags: list[str]) -> None:
    """Ein einfacher Befehl: träge Argumente in `repl` als (start, ende, ersatz) vormerken.
    Ein sed/awk-Skript, das schreibt oder ausführt, landet als Grund in `flags`."""
    k = 0
    # Zuweisungen und Wrapper (mit Optionen) vorne überspringen
    while k < len(words):
        kind, s, e, v = words[k]
        if kind == "word" and _ASSIGN.match(v):
            k += 1
            continue
        if kind == "word" and v.rsplit("/", 1)[-1] in _WRAPPERS:
            k += 1
            while k < len(words) and words[k][0] == "word" and (
                    words[k][3].startswith("-") or words[k][3].isdigit()):
                k += 1
            continue
        break
    if k >= len(words) or words[k][0] != "word":
        return
    prog = words[k][3].rsplit("/", 1)[-1]
    args = words[k + 1:]

    def quoted(w) -> bool:
        raw = cmd[w[1]:w[2]]
        return ("'" in raw or '"' in raw) and "$(" not in raw and "`" not in raw

    def plain_args():
        """Argumente ohne Umleitungsziele (das Wort nach > bleibt sichtbar)."""
        skip = False
        for w in args:
            if w[0] == "redir":
                skip = True
                continue
            if w[0] != "word":
                continue
            if skip:
                skip = False
                continue
            yield w

    if prog in ("ssh", "sshpass"):
        rest = list(plain_args())
        j = 0
        if prog == "sshpass":                     # sshpass -p x ssh …
            while j < len(rest) and rest[j][3] != "ssh":
                j += 1
            j += 1
        while j < len(rest) and rest[j][3].startswith("-"):
            f = rest[j][3]
            j += 2 if (len(f) == 2 and f[1] in _SSH_OPTS_WITH_ARG) else 1
        for w in rest[j + 1:]:                    # nach dem Host: der entfernte Befehl
            if quoted(w):
                inner = _mask_inert(w[3], depth + 1, flags)
                if inner != w[3]:
                    repl.append((w[1], w[2], "'" + inner.replace("'", "'\\''") + "'"))
        return
    if prog in _SHELLS:
        rest = list(plain_args())
        for i, w in enumerate(rest[:-1]):
            if w[3].startswith("-") and "c" in w[3][1:] and quoted(rest[i + 1]):
                inner = _mask_inert(rest[i + 1][3], depth + 1, flags)
                if inner != rest[i + 1][3]:
                    t = rest[i + 1]
                    repl.append((t[1], t[2], "'" + inner.replace("'", "'\\''") + "'"))
                break
        return
    if prog in _INERT_PROGS:
        for w in plain_args():
            if quoted(w):
                repl.append((w[1], w[2], "'Q'"))
        return
    if prog in _SCRIPT_PROGS:
        rest = list(plain_args())
        opts = [w[3] for w in rest if w[3].startswith("-")]
        is_sed = prog.endswith("sed")
        if is_sed:
            if any(o == "--in-place" or o.startswith("--in-place=") or
                   (not o.startswith("--") and "i" in o[1:]) for o in opts):
                flags.append("sed -i")            # schreibt in die Datei
                return
            if any(o in ("-f", "--file") or o.startswith("--file=") or
                   (not o.startswith("--") and "f" in o[1:]) for o in opts):
                flags.append("sed -f")            # Skript aus Datei: nicht prüfbar
                return
            check = _sed_script_writes
        else:
            if any(o in ("-i", "--include", "-f", "--file", "-E", "--exec", "-l", "--load")
                   or o.startswith(("--include=", "--file=", "--exec=", "--load="))
                   for o in opts):
                flags.append(f"{prog} {' '.join(opts)}")   # inplace/-f/Erweiterung
                return
            check = _awk_program_writes
        # Die Skript-Wörter bestimmen (auch ungequotet: sed -n -e p -e 'w /x' f):
        # sed: jedes Argument von -e/--expression, sonst das erste Nicht-Options-Wort;
        # awk: das erste Wort, das nicht Argument von -F/-v ist.
        scripts, explicit, j = [], False, 0
        while j < len(rest):
            v = rest[j][3]
            if v == "--":
                j += 1
                break
            if not v.startswith("-") or v == "-":
                break
            if is_sed and v == "--expression":
                explicit = True
                if j + 1 < len(rest):
                    scripts.append(rest[j + 1][3])
                j += 2
                continue
            if is_sed and not v.startswith("--") and "e" in v[1:]:
                # GNU: `-ne SKRIPT` oder `-neSKRIPT` — nach dem e beginnt das Skript
                explicit = True
                tail = v[v.index("e", 1) + 1:]
                if tail:
                    scripts.append(tail)
                    j += 1
                else:
                    if j + 1 < len(rest):
                        scripts.append(rest[j + 1][3])
                    j += 2
                continue
            if is_sed and v.startswith("--expression="):
                explicit = True
                scripts.append(v.split("=", 1)[1])
            elif is_sed and v in ("-l", "--line-length"):
                j += 1
            elif not is_sed and v in ("-F", "-v"):
                j += 1
            j += 1
        if not explicit and j < len(rest):
            scripts.append(rest[j][3])
        if any(check(sc) for sc in scripts):
            flags.append(f"{prog}-Skript schreibt/führt aus")
            return
        for w in rest:
            if quoted(w) and not w[3].startswith("-"):
                repl.append((w[1], w[2], "'Q'"))


def _mask_inert(cmd: str, depth: int = 0, flags: list[str] | None = None) -> str:
    """`cmd` mit Platzhaltern statt der gequoteten Argumente träger Programme.

    Bei Lexfehlern, zu tiefer Verschachtelung oder Heredocs bleibt der Text unverändert
    (die Regeln bleiben dann so streng wie bisher)."""
    if flags is None:
        flags = []
    if depth > 3 or "<<" in cmd:
        return cmd
    try:
        toks = _lex(cmd)
    except _LexError:
        return cmd
    repl: list[tuple[int, int, str]] = []
    cur: list = []
    for t in toks + [("op", len(cmd), len(cmd), ";")]:
        if t[0] == "op" and t[3] in _SEPARATORS:
            if cur:
                _mask_simple(cur, depth, repl, cmd, flags)
            cur = []
        else:
            cur.append(t)
    for s, e, r in sorted(repl, reverse=True):
        cmd = cmd[:s] + r + cmd[e:]
    return cmd


_META = re.compile(r"[\s;|&()<>$`{}]")


def _write_word(cmd: str, depth: int = 0) -> bool:
    return _has_word(cmd, _WRITE_WORD_RE, depth)


def _system_word(cmd: str, depth: int = 0) -> bool:
    return _has_word(cmd, _SYSTEM_WORD_RE, depth)


def _has_word(cmd: str, word_re: re.Pattern, depth: int = 0) -> bool:
    """Steht ein Schreib-Befehl (rm, cp, tee, install, vim …) als WORT im Befehl?

    Wortgenau statt Teilstring: `rm`, `/bin/rm`, `\\rm`, `"rm"` zählen, `install/`,
    `install.old/x.xml`, `rm.log` nicht (Anlass 12.11.: `ls -la install/` in einer
    Diagnose auf cue0174 sperrte). Gequotete Befehlstexte (ssh-Fernbefehl, `sh -c`,
    `$(…)`) werden rekursiv geprüft. Was der Lexer nicht versteht (Backticks, Heredoc,
    offene Quotes, Klammer-Expansion), prüft der alte Teilstring-Ausdruck — streng.
    """
    if depth > 3 or "<<" in cmd or "`" in cmd:
        return bool(word_re.search(cmd))
    try:
        toks = _lex(cmd)
    except _LexError:
        return bool(word_re.search(cmd))
    at_cmd = True                                  # nächstes Wort steht an Befehlsposition
    ssh_skip = -1                                  # >=0: Wörter bis zum ssh-Host
    for kind, _s, _e, v in toks:
        if kind == "op":
            at_cmd, ssh_skip = v in _SEPARATORS, -1
            continue
        if kind != "word":
            continue
        here, at_cmd = at_cmd, (at_cmd and (_ASSIGN.match(v) is not None
                                            or v.rsplit("/", 1)[-1] in _WRAPPERS))
        if here and v.rsplit("/", 1)[-1] in ("ssh", "sshpass"):
            ssh_skip = 0
        elif ssh_skip >= 0:                        # ssh-Optionen, dann der Host
            if ssh_skip > 0:
                ssh_skip -= 1
            elif v.startswith("-"):
                ssh_skip = 1 if (len(v) == 2 and v[1] in _SSH_OPTS_WITH_ARG) else 0
            else:                                  # Host: danach der entfernte Befehl
                ssh_skip, at_cmd = -1, True
                continue
        if not word_re.search(v):
            continue
        if _META.search(v):
            if v == cmd or "{" in v or "}" in v:   # Klammer-Expansion {rm,-rf,x}: streng
                return True
            if _has_word(v, word_re, depth + 1):
                return True
            continue
        if word_re.fullmatch(v):                   # rm, "rm", \rm — überall (xargs rm, -exec rm)
            return True
        # Pfad: /bin/rm, /usr/sbin/reboot oder ein Pfad an Befehlsposition (./rm);
        # /etc/crontab oder /etc/cron.d/aim-reboot als ARGUMENT ist nur ein Dateiname.
        d, _, base = v.rpartition("/")
        if base and word_re.fullmatch(base) and (here or re.search(r"(^|/)s?bin$", d)):
            return True
    return False


def _is_write(cmd: str, cwd: str = "", _depth: int = 0) -> bool:
    sub_write, cmd = _ssh_substitution_write(cmd, cwd, _depth)
    if sub_write:
        return True
    if (_REMOTE_INDIRECT.search(cmd) or _bare_ssh(cmd) or _PY_SSH_LIB.search(cmd)
            or _pipe_into_shell(cmd)):
        return True
    if _depth == 0 and _script_is_write(cmd, cwd):
        return True
    # Systembefehle und Schreib-Muster nur außerhalb träger, gequoteter Argumente suchen
    # (grep-/sed-Muster, echo-Text); Fern-, Pfad- und Konfig-Prüfung bleiben am vollen Text.
    script_writes: list[str] = []
    inert = _mask_inert(cmd, flags=script_writes)
    if _SYSTEM_OP.search(inert) or _system_word(inert):
        return True
    if _write_word(inert) or _FILE_WRITE_OPS.search(inert) or script_writes:
        # Remote target (production), a local system path or the agents' own config → block.
        if _REMOTE.search(cmd) or _SYSTEM_PATH.search(cmd) or _AGENT_CONFIG_PATH.search(cmd):
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

#: Ziele in einem Codex-Patch ("*** Add File: x", "*** Update File: x", "*** Delete
#: File: x", "*** Move to: x").
_PATCH_TARGET = re.compile(r"^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+)$", re.M)

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
#: Das Gedächtnis der Konsole (lokale VI, vi-mcp): es handelt nie selbst in der Welt, schreibt nur in sich selbst,
#: und die Rechte der Instanz (kein Reflex-, kein Freigabe-Recht) prüft vi-mcp selbst. Ohne diese Zeile wäre
#: schon vi_erinnere gesperrt („erinnere“ ist kein Lese-Verb).
MEMORY_SERVERS = {"vi"}


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
    if server in SELF_GUARDED_SERVERS or server in MEMORY_SERVERS:
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
                                 # Codex meldet gesperrte Aufrufe nicht im exec-Stream; die
                                 # Konsole findet sie über diese Sitzungs-ID (= thread_id).
                                 "session": _current.get("session"),
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
    _current.update(tool=tool, input=tool_input, session=data.get("session_id"))

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

    if tool == "apply_patch":
        # Codex' Dateiwerkzeug: der Patch nennt seine Ziele selbst. Frei ist, was
        # vollständig im Workspace oder /tmp liegt — wie bei Claudes Write/Edit.
        patch = tool_input.get("command") or tool_input.get("input") or ""
        if isinstance(patch, list):
            patch = "\n".join(map(str, patch))
        targets = _PATCH_TARGET.findall(str(patch))
        outside = [t for t in targets if _file_write_outside(t.strip(), data.get("cwd", ""))]
        if outside or not targets:
            _allow_if_approved()
            _deny(
                "READ-ONLY-MODUS: apply_patch auf "
                f"{', '.join(outside) or '(kein erkennbares Ziel)'} liegt außerhalb des "
                "Workspace und wurde BLOCKIERT. Frei beschreibbar sind /home/yolo/workspaces "
                "und /tmp. Bitte den Nutzer, in der CentralStation-Konsole "
                "\"Schreibzugriff freigeben\" zu klicken; eine Zustimmung im Chat hebt die "
                "Sperre nicht auf."
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

    if tool == "write_stdin":            # Codex: Eingabe in eine laufende exec-Sitzung
        tool_input = {"action": "write", "data": tool_input.get("chars") or tool_input.get("input") or ""}
        tool = "process_manage"

    if tool == "process_manage":
        # Eingabe in einen laufenden Prozess (z. B. eine offene ssh-Sitzung): der Inhalt
        # wird wie ein Befehl auf einem ENTFERNTEN Host behandelt — welcher Prozess es
        # ist, sieht der Hook nicht.
        if (tool_input.get("action") in ("write", "submit")):
            data = tool_input.get("data", "") or ""
            if _SYSTEM_OP_STRICT.search(data) or _FILE_WRITE.search(data) or _is_write(data):
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
