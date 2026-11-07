"""ensure_container: gleichzeitige Aufrufe desselben Nutzers laufen nacheinander (gemessen 07.11.: 409-Konflikte)."""
import threading
import time

from app.services import userenv_manager as um


def test_gleichzeitige_aufrufe_eines_nutzers_laufen_nacheinander(monkeypatch):
    aktiv, hoechst = [0], [0]
    sperre = threading.Lock()

    def langsam(user_id):
        with sperre:
            aktiv[0] += 1
            hoechst[0] = max(hoechst[0], aktiv[0])
        time.sleep(0.02)
        with sperre:
            aktiv[0] -= 1
        return "ok"

    monkeypatch.setattr(um, "_ensure_container", langsam)
    t = [threading.Thread(target=um.ensure_container, args=("u1",)) for _ in range(6)]
    [x.start() for x in t]
    [x.join() for x in t]
    assert hoechst[0] == 1
    assert um._ensure_lock("u1") is um._ensure_lock("u1") and um._ensure_lock("u1") is not um._ensure_lock("u2")
