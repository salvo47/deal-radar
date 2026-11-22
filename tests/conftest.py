from __future__ import annotations

from pathlib import Path

import pytest

from dealradar.models import DealAnalysis

FIX = Path(__file__).parent / "fixtures"

CALC_CFG = {
    "stundensatz_eur": 20, "verfuegbares_kapital_eur": 100000, "referenz_zins_prozent": 2.0, "spread_prozent_default": 0.30,
    "handelsplatzentgelt_eur_default": 0.0, "sicherheitsabschlag_order_prozent": 15,
    "gutschein_faktor": 0.90, "sachpraemie_faktor": 0.60, "max_wiederholungen_ohne_limit": 10,
    "abgeltungsteuer_auf_zinsen": True, "abgeltungsteuer_prozent": 26.375,
}


def base_analysis(**over) -> DealAnalysis:
    d = dict(
        ist_finanz_oder_sparaktion=True, kategorie="konto_praemie", anbieter="Testbank",
        region=["DE"], frist="2099-12-31", frist_text="bis 31.12.2099", zielgruppe="Neukunden", wohnsitz_erforderlich=[],
        ausgeschlossen=[], praemie_art="bar", praemie_pro_ausfuehrung_eur=100.0,
        wiederholbarkeit="einmalig", max_wiederholungen=None, order=None, zins=None,
        weitere_kosten=[], kapitalbindung_eur=None, kapitalbindung_monate=None,
        zeitaufwand_minuten=60, klauseln=[],
        risiko=dict(rating="niedrig", begruendung="-", rueckforderung="-", kontosperre="-",
                    missbrauchsklausel="-", haltefrist="-", steuer="-"),
        seriositaet=dict(reguliert=True, aufsicht="BaFin", einlagensicherung="EU",
                         vorkasse_verlangt=False, krypto_ueberweisung_an_unbekannte=False,
                         gewinnversprechen_ohne_bedingungen=False, bewertung="serioes"),
        erfordert_unzulaessiges=False, unzulaessig_grund=None, bedingungen_gelesen=True,
        konfidenz=0.9, unsicher=[], zusammenfassung="Test.")
    d.update(over)
    return DealAnalysis.model_validate(d)


@pytest.fixture
def calc_cfg():
    return dict(CALC_CFG)
