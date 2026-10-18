from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from drone.loaders.yolo_models_loader import YoloModelConfig, load_models_registry


def _catalogo(contenuto) -> str:
    radice = tempfile.mkdtemp()
    cartella = Path(radice) / "vision" / "models"
    cartella.mkdir(parents=True)
    if contenuto is not None:
        testo = contenuto if isinstance(contenuto, str) else json.dumps(contenuto)
        (cartella / "catalog.json").write_text(testo, encoding="utf-8")
    return radice


DEFAULT = {
    "safety_net": "collective_protection",
    "person_fall": "person_fall",
    "restricted_area": "restricted_areas",
    "dpi": "personal_protection",
}


def test_il_catalogo_vero_del_progetto_ha_i_quattro_ruoli_e_i_quattro_modelli():
    radice = Path(__file__).resolve().parents[2]
    registro = load_models_registry(radice)
    assert len(registro["models"]) == 4
    for ruolo, atteso in DEFAULT.items():
        assert registro[ruolo] == atteso
    nomi = {m.name for m in registro["models"]}
    assert set(DEFAULT.values()) <= nomi
    for modello in registro["models"]:
        assert modello.path.exists()


def test_senza_catalogo_niente_modelli_e_ruoli_predefiniti():
    registro = load_models_registry(_catalogo(None))
    assert registro["models"] == ()
    for ruolo, atteso in DEFAULT.items():
        assert registro[ruolo] == atteso


def test_catalogo_non_json():
    registro = load_models_registry(_catalogo("{ questo non e' json"))
    assert registro["models"] == ()
    assert registro["safety_net"] == DEFAULT["safety_net"]


def test_catalogo_che_non_e_un_oggetto():
    registro = load_models_registry(_catalogo([1, 2, 3]))
    assert registro["models"] == ()


def test_senza_la_chiave_models_non_si_carica_niente():
    registro = load_models_registry(_catalogo({"roles": {"safety_net": "X"}}))
    assert registro["models"] == ()
    assert registro["safety_net"] == "X"


def test_il_nome_si_ricava_dalla_cartella_dei_pesi():
    registro = load_models_registry(_catalogo({
        "models": [{"weights": "Reti_di_sicurezza/weights/best.pt"}],
    }))
    modello = registro["models"][0]
    assert modello.name == "Reti_di_sicurezza"
    assert modello.label == "Reti_di_sicurezza"
    assert modello.path.name == "best.pt"
    assert modello.path.parent.parent.name == "Reti_di_sicurezza"


def test_il_nome_esplicito_vince_su_quello_della_cartella():
    registro = load_models_registry(_catalogo({
        "models": [{"weights": "Cartella/weights/best.pt", "name": "Scelto", "label": "Etichetta"}],
    }))
    modello = registro["models"][0]
    assert modello.name == "Scelto"
    assert modello.label == "Etichetta"


def test_le_voci_senza_pesi_o_malformate_vengono_saltate():
    registro = load_models_registry(_catalogo({
        "models": [
            "non e' un oggetto",
            {"name": "Senza pesi"},
            {"weights": ""},
            {"weights": "Buono/weights/best.pt"},
        ],
    }))
    assert [m.name for m in registro["models"]] == ["Buono"]


def test_il_colore_valido_si_conserva():
    registro = load_models_registry(_catalogo({
        "models": [{"weights": "A/weights/best.pt", "color": [10, 20, 30]}],
    }))
    assert registro["models"][0].color == (10, 20, 30)


def test_il_colore_sbagliato_ripiega_sul_verde():
    for colore in ([1, 2], [1, 2, 3, 4], ["a", "b", "c"], [True, False, True], "verde", 5):
        registro = load_models_registry(_catalogo({
            "models": [{"weights": "A/weights/best.pt", "color": colore}],
        }))
        assert registro["models"][0].color == (0, 255, 0), colore


def test_senza_colore_si_usa_il_verde():
    registro = load_models_registry(_catalogo({
        "models": [{"weights": "A/weights/best.pt"}],
    }))
    assert registro["models"][0].color == (0, 255, 0)


def test_i_ruoli_del_catalogo_vincono_sui_predefiniti():
    registro = load_models_registry(_catalogo({
        "models": [{"weights": "A/weights/best.pt"}],
        "roles": {"safety_net": "Mio_modello", "dpi": "Altro"},
    }))
    assert registro["safety_net"] == "Mio_modello"
    assert registro["dpi"] == "Altro"
    assert registro["person_fall"] == DEFAULT["person_fall"]


def test_i_ruoli_malformati_ripiegano_sui_predefiniti():
    for ruoli in ("non un oggetto", {"safety_net": 5}, {"safety_net": ""}, {}):
        registro = load_models_registry(_catalogo({
            "models": [{"weights": "A/weights/best.pt"}],
            "roles": ruoli,
        }))
        assert registro["safety_net"] == DEFAULT["safety_net"], ruoli


def test_i_pesi_pendono_dalla_cartella_dei_modelli():
    radice = _catalogo({"models": [{"weights": "A/weights/best.pt"}]})
    registro = load_models_registry(radice)
    atteso = Path(radice) / "vision" / "models" / "A" / "weights" / "best.pt"
    assert registro["models"][0].path == atteso


def test_l_etichetta_vuota_diventa_il_nome():
    modello = YoloModelConfig(name="Uno", path=Path("x.pt"))
    assert modello.label == "Uno"
    assert YoloModelConfig(name="Uno", path=Path("x.pt"), label="Due").label == "Due"


def test_le_classi_tradotte_arrivano_dal_catalogo():
    registro = load_models_registry(_catalogo({
        "models": [{
            "weights": "Cartella/weights/best.pt",
            "classes": {"Safety_Net": "Rete di sicurezza"},
        }],
    }))
    assert registro["models"][0].classes == {"Safety_Net": "Rete di sicurezza"}


def test_le_classi_non_testuali_vengono_ignorate():
    registro = load_models_registry(_catalogo({
        "models": [{
            "weights": "Cartella/weights/best.pt",
            "classes": {"Buona": "Va bene", "Rotta": 3},
        }],
    }))
    assert registro["models"][0].classes == {"Buona": "Va bene"}


def test_senza_classi_il_modello_non_ne_ha():
    registro = load_models_registry(_catalogo({
        "models": [{"weights": "Cartella/weights/best.pt"}],
    }))
    assert registro["models"][0].classes == {}


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
