import configparser
import shlex
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_company_runner_cannot_inherit_enabled_background_threads():
    unit = configparser.ConfigParser(interpolation=None, strict=False)
    unit.read(ROOT / "deploy/gajiku-company-lifecycle.service")
    command = shlex.split(unit["Service"]["ExecStart"])
    assert command[:3] == ["/usr/bin/env", "START_INLINE_WORKER=0", "EXPORT_CSV_ENABLED=0"]
    assert command[3].endswith("/bin/python")
    assert command[4:] == ["-m", "flask", "--app", "wsgi:app", "sync-company-lifecycle"]


def test_company_timer_runs_once_per_minute_and_is_persistent():
    timer = configparser.ConfigParser(interpolation=None)
    timer.read(ROOT / "deploy/gajiku-company-lifecycle.timer")
    assert timer["Timer"]["OnCalendar"] == "*-*-* *:*:00"
    assert timer["Timer"].getboolean("Persistent")
    assert timer["Timer"]["Unit"] == "gajiku-company-lifecycle.service"
