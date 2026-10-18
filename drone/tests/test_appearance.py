from __future__ import annotations

import ast
import os
import pathlib
import sys

_RADICE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _RADICE)

from drone.ui import appearance


_SCRIPT_STANDALONE = (
    "drone/scripts/joystick_diagnostics.py",
    "drone/scripts/camera_calibration.py",
)

_APERTURE_DI_FINESTRA = ("pygame.display.set_caption", "cv2.imshow")

_WINDOW_TITLES = (
    "WINDOW_TITLE",
    "SCENARIO_WINDOW_TITLE",
    "VIDEO_WINDOW_TITLE",
    "CONTROLLER_WINDOW_TITLE",
)


def _titolo(nome: str) -> str:
    return getattr(appearance, nome)


def test_the_window_titles_are_pure_ascii():
    for nome in _WINDOW_TITLES:
        titolo = _titolo(nome)
        assert titolo.isascii(), (
            f"{nome} = {titolo!r}: su Windows pygame e OpenCV storpiano i caratteri "
            "non-ASCII e la ricerca della finestra video per titolo fallisce in silenzio"
        )


def test_the_window_titles_use_the_plain_separator():
    for nome in _WINDOW_TITLES:
        titolo = _titolo(nome)
        assert " - " in titolo, f"{nome} = {titolo!r}: il separatore è ' - '"
        assert "·" not in titolo, f"{nome} = {titolo!r}: il punto mediano non è ASCII"


def test_the_window_titles_carry_the_project_name():
    for nome in _WINDOW_TITLES:
        titolo = _titolo(nome)
        assert titolo.startswith(appearance.PROJECT_TITLE), (
            f"{nome} = {titolo!r}: le finestre portano il nome del progetto, "
            "come il titolo in cima al video e l'intestazione del terminale"
        )


def test_the_window_titles_are_distinct():
    titoli = [_titolo(nome) for nome in _WINDOW_TITLES]
    assert len(set(titoli)) == len(titoli), titoli
    assert all(titolo.strip() for titolo in titoli)


def _script_window_titles() -> list[tuple[str, str]]:
    trovati = []
    for relativo in _SCRIPT_STANDALONE:
        percorso = pathlib.Path(_RADICE) / relativo
        for nodo in ast.walk(ast.parse(percorso.read_text(encoding="utf-8"))):
            if not isinstance(nodo, ast.Call) or not nodo.args:
                continue
            if ast.unparse(nodo.func) not in _APERTURE_DI_FINESTRA:
                continue
            primo = nodo.args[0]
            if isinstance(primo, ast.Constant) and isinstance(primo.value, str):
                trovati.append((relativo, primo.value))
    return trovati


def test_the_standalone_tools_name_their_windows_the_same_way():
    titoli = _script_window_titles()
    assert len(titoli) == len(_SCRIPT_STANDALONE), (
        f"attesa una finestra per strumento, trovate {len(titoli)}: {titoli}"
    )
    for percorso, titolo in titoli:
        assert titolo.isascii(), f"{percorso}: {titolo!r} non è ASCII"
        assert " - " in titolo, f"{percorso}: {titolo!r} non usa il separatore ' - '"
        assert titolo.startswith(appearance.PROJECT_TITLE), (
            f"{percorso}: {titolo!r} non porta il nome del progetto. Gli strumenti "
            "stanno fuori dal package e lo scrivono a mano, quindi lo controlla questo test"
        )


def _run_all() -> int:
    tests = sorted(
        (name, obj)
        for name, obj in globals().items()
        if name.startswith("test_") and callable(obj)
    )
    passed = 0
    failed = []
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            failed.append((name, exc))
            print(f"[FAIL] {name}: {type(exc).__name__}: {exc}")
        else:
            passed += 1
            print(f"[ OK ] {name}")

    print("-" * 60)
    print(f"Totale: {len(tests)}  |  passati: {passed}  |  falliti: {len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_all())
