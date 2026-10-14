"""Kritische Kernelereignisse (14.10.2026): ein stündlicher OOM auf docker0209 blieb 25+ h
unbemerkt — 1–3 Kernelzeilen, für die mengenbasierten Regeln unsichtbar."""
from __future__ import annotations

from types import SimpleNamespace as NS

from app.services.error_clusters.collector import Line
from app.services.error_clusters.critical import (
    critical_key, match, reason_text, scan, should_escalate,
)
from app.services.error_clusters.notify import digest_group, render_html, subject
from app.services.error_clusters.rules import DEFAULT

OOM = ("sammelpdf kernel: Out of memory: Killed process 2046866 ([celeryd: celer) "
       "total-vm:18895452kB, anon-rss:14936576kB")
INVOKED = ("sammelpdf kernel: dockerd invoked oom-killer: gfp_mask=0x140cca(GFP_HIGHUSER_MOVABLE"
           "|__GFP_COMP), order=0, oom_score_adj=-500")


def _l(msg, source="docker0209.example.com", container="", app=""):
    return Line("2026-10-13T11:21:49Z", source, container, msg, app)


def test_oom_lines_of_one_window_are_one_event_per_host():
    found = scan([_l(OOM), _l(INVOKED), _l(OOM, source="cue0101.example.com")])
    assert set(found) == {("oom_kill", "docker0209.example.com"), ("oom_kill", "cue0101.example.com")}
    f = found[("oom_kill", "docker0209.example.com")]
    assert f.lines == 2 and len(f.examples) == 2


def test_only_kernel_lines_count():
    # sudo-Protokoll einer Diagnose: kein OOM (13.10. wurde genau so eine Zeile als critical markiert)
    sudo = ("sammelpdf sudo:   marvin : PWD=/home/marvin ; USER=root ; COMMAND=/usr/bin/grep -E "
            "'invoked oom-killer|Out of memory: Killed' /var/log/kern.log")
    assert match(_l(sudo)) is None
    # Container-Logs sind keine Kernelzeilen
    assert match(_l("Out of memory: Killed process 1", container="app-1")) is None
    # Syslog/GELF: Programm im Feld statt im Text
    assert match(_l("Out of memory: Killed process 1 (java)", app="kernel")).id == "oom_kill"


def test_signatures_measured_in_the_fleet():
    """Beispiele aus 7 Tagen Graylog (14.10.)."""
    cases = {
        "veeam-proxy018 kernel: [5437319.019738] Buffer I/O error on dev dm-10, logical block 1": "io_error",
        "vpp0177 kernel: mce: [Hardware Error]: Machine check events logged": "mce",
        "cue0101 kernel: coroot-node-age invoked oom-killer: gfp_mask=0x6200ca, order=0": "oom_kill",
        "U7-Lite kernel: EXT4-fs error (device mmcblk0p7): ext4_read_block_bitmap": "fs_error",
        "h kernel: INFO: task jbd2/sda1-8:345 blocked for more than 120 seconds.": "hung_task",
        "h kernel: Kernel panic - not syncing: Fatal exception": "kernel_panic",
        "h kernel: watchdog: BUG: soft lockup - CPU#3 stuck for 22s!": "lockup",
    }
    for msg, sig in cases.items():
        assert match(_l(msg)).id == sig, msg
    assert match(_l("h kernel: eth0: link up, 1000 Mbps")) is None


def test_escalation_threshold_and_key():
    assert DEFAULT.critical_repeat_count == 3 and DEFAULT.critical_repeat_hours == 6
    assert not should_escalate(2) and should_escalate(3)
    assert critical_key("oom_kill", "a") == critical_key("oom_kill", "a")
    assert critical_key("oom_kill", "a") != critical_key("oom_kill", "b")
    assert len(critical_key("oom_kill", "a")) == 16          # wie LogTemplate.key
    assert "3× in den letzten 6 h" in reason_text("OOM-Kill", "docker0209", 3)


def test_mail_names_kind_host_and_repetition_not_a_line_rate():
    ctx = {"kind": "critical", "label": "OOM-Kill (Arbeitsspeicher erschöpft)", "events": 3,
           "window_hours": 6, "hosts": ["docker0209.example.com"],
           "containers": [{"name": "docker0209.example.com", "count": 3}],
           "template": "OOM-Kill auf docker0209", "reasons": ["3× in 6 h"], "examples": [OOM],
           "started_at": "2026-10-13T09:21:00+00:00", "until": "2026-10-13T11:21:00+00:00",
           "count15": 2, "total": 3}
    subj = subject(ctx, "Kritisches Ereignis: OOM-Kill auf docker0209 · 3× in 6 h")
    assert subj == ("[CentralStation] Kritisches Ereignis: OOM-Kill (Arbeitsspeicher erschöpft) "
                    "auf docker0209.example.com · 3× in 6 h")
    html = render_html(ctx, None, None, pending=False)
    assert "3×</b> in 6 h" in html and "Betroffen (1 Host)" in html
    assert "Zeilen gesamt" not in html


def test_daily_overview_separates_kinds():
    assert digest_group(NS(source="critical", rules=["critical"])) == "critical"
    assert digest_group(NS(source="checkmk_fleet", rules=["fleet", "recurring"])) == "recurring"
    assert digest_group(NS(source="checkmk_fleet", rules=["fleet"])) == "log"
    assert digest_group(NS(source="log", rules=["new"])) == "log"
