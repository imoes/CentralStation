"""Positivliste der SSH-Schleuse: Diagnose geht durch, alles andere braucht eine Freigabe."""
from __future__ import annotations

import pytest

from cs_policy.remote_allowlist import check, is_read_only
from cs_policy.templates import PolicyError, TEMPLATES, render

READS = [
    "df -h",
    "uptime; free -m",
    "docker ps --format '{{.Names}}\\t{{.Status}}'",
    "sudo -n docker ps",
    "docker logs --tail 200 web 2>&1 | tail -n 50",
    "docker compose ps",
    "systemctl status nginx --no-pager",
    "journalctl -u nginx --since -1h -n 200 --no-pager",
    "grep -c ERROR /var/log/syslog",
    "grep '^ERROR$' /var/log/app.log",
    "tail -n 100 /var/log/nginx/error.log 2>/dev/null",
    "ps aux --sort=-%cpu | head -n 20",
    "zpool list; zpool status -x",
    "zfs list -o name,used -r data",
    "ip addr show",
    "ss -tlnp",
    "find /var/log -name '*.gz' -mtime +30",
    "sed -n 10,20p /etc/nginx/nginx.conf",
    "curl -s http://localhost:8080/health",
    "timeout 10 dig example.org",
    "ping -c 3 10.0.0.1",
    "top -bn1 | head -n 15",
    "pvesh get /nodes",
    "qm list",
    "dpkg -l nginx",
    "cat /etc/os-release && uname -a",
]


@pytest.mark.parametrize("cmd", READS)
def test_diagnosis_passes(cmd):
    check(cmd)


DENIED = [
    "systemctl restart nginx",
    "rm -rf /tmp/x",
    "touch /tmp/probe",
    "echo hi > /tmp/x",
    "docker restart web",
    "docker exec web sh",
    "docker logs -f web",
    "docker inspect web",
    "sudo docker ps",                    # sudo nur mit -n
    "sudo -s",
    "sudo -n bash",
    "find / -name x -delete",
    "find / -exec rm {} ;",
    "sed -i s/a/b/ /etc/hosts",
    "awk 'BEGIN{print 1}'",
    "curl -X POST http://x/api",
    "curl -d a=1 http://x",
    "curl -o /tmp/x http://x",
    "tar xf a.tar",
    "mysql -e 'select 1'",
    "python3 -c 'print(1)'",
    "bash -c 'id'",
    "cat $(echo /etc/hosts)",
    "cat `echo /etc/hosts`",
    "cat /etc/hosts &",
    "(id)",
    "cat < /etc/hosts",
    "cat /etc/shadow",
    "cat /root/.ssh/id_rsa",
    "cat /opt/app/.env",
    "kubectl get secrets",
    "zfs destroy data/x",
    "zpool scrub data",
    "qm stop 100",
    "dmesg -C",
    "journalctl --vacuum-size=1M",
    "tail -f /var/log/syslog",
    "uniq in.txt out.txt",
    "sort -o out.txt in.txt",
    "ip link set eth0 down",
    "/tmp/evil",
    "ls\nreboot",
    "echo a\;id",
]


@pytest.mark.parametrize("cmd", DENIED)
def test_writes_and_detours_are_refused(cmd):
    with pytest.raises(PolicyError):
        check(cmd)
    assert not is_read_only(cmd)


def test_every_probe_template_passes_the_allowlist():
    """Was der Prüf-Container ausführt, muss auch die Schleuse als lesend erkennen — eine Regel."""
    samples = {"query": "rsync", "page": "rsync", "unit": "nginx.service", "name": "web-1",
               "package": "openssh-server", "pool": "data"}
    skip = {"host_capabilities", "man_search", "man_read"}   # nutzen for/command -v bzw. apropos/man/col
    for tid, tpl in TEMPLATES.items():
        if tid in skip:
            continue
        params = {n: samples[n] for n, p in tpl.params.items() if p.required and p.default is None}
        assert is_read_only(render(tid, params)), tid
