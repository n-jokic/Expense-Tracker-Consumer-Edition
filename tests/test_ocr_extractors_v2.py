"""
Phase E regression suite (item 4 / E7): extractor rework acceptance.

Pins the ticket behaviours:
  * totals: mandatory decimal cents, tel/fax/url exclusion, grand-total
    tier over plain UKUPNO, subtotal penalty, equal-value dedup, top
    candidate >= HIGH_CONF (pass-2 gate reachable);
  * currency: boundary-safe pattern (symbols glued to digits), locale /
    default-currency 0.3-conf fallback;
  * dates: single structurally-valid candidate lands at/above LOW_CONF;
  * shared confidence normalizer replaces three drifted copies;
  * line items: trailing-amount grammar, phone/header rows excluded,
    qty rows and discounts intact, reconcile() reports mismatches.
"""
import inspect

import pytest

from ingestion.receipt.confidence import (
    HIGH_CONF, LOW_CONF, normalize_confidences)
from ingestion.receipt.date_extractor import extract_date_candidates
from ingestion.receipt.line_item_extractor import (
    extract_line_items, reconcile)
from ingestion.receipt.total_extractor import extract_total_candidates


# ---------------------------------------------------------------- fixtures

TICKET = """
PIK MARKET D.O.O.
Bulevar Oslobodjenja 12, Novi Sad
PIB: 100234567
Tel: 021 123 45 67
www.pikmarket.rs
Racun br.: 2024/5567
Datum: 31.12.2024 14:32
--------------------------------
Mleko 2 x 289,99        579,98
Hleb                    120,00
Kafa                    450,00
Sok 2 x 149,99          299,98
Telefon punjac          890,00
--------------------------------
Medjuzbirka             76,50
PDV 20%                 12,75
Ukupno robа            76,80 EUR
Naknada                 8,00
GRAND TOTAL             84,80 EUR
Gotovina                100,00
Kusur                   15,20
Hvala na poseti!
""".strip()


# ----------------------------------------------------------------- totals

def test_totals_ticket_ranking_and_conf_gate():
    cands = extract_total_candidates(None, TICKET)
    vals = [round(c.value, 2) for c in cands]
    assert vals[0] == 84.80
    assert vals[1] == 76.80
    # Pass-2 gate must be reachable from pass-1 output.
    assert cands[0].confidence >= HIGH_CONF


def test_totals_phones_and_noise_excluded():
    cands = extract_total_candidates(None, TICKET)
    vals = {round(c.value, 2) for c in cands}
    for phoneish in (21123.45, 21123.0, 2024.55, 202455.67):
        assert phoneish not in vals


def test_totals_mandatory_cents_drops_bare_integers():
    cands = extract_total_candidates(None, "Kafa 450\nHleb 120")
    assert cands == [], "bare integers must never become total candidates"


def test_totals_subtotal_penalised_below_grand():
    cands = extract_total_candidates(None, TICKET)
    sub = next(c for c in cands if abs(c.value - 76.50) < 0.005)
    grand = cands[0]
    assert sub.confidence < grand.confidence
    assert any("SUBTOTAL" in r or "MEDJUZBIR" in r.upper()
               for r in sub.reasons)


def test_totals_equal_values_dedup_with_merged_reasons():
    text = "Total         25,00\nUKUPNO        25,00"
    cands = extract_total_candidates(None, text)
    twentyfive = [c for c in cands if abs(c.value - 25.0) < 0.005]
    assert len(twentyfive) == 1, "equal-value candidates must merge"
    joined = " ".join(twentyfive[0].reasons)
    assert "TOTAL" in joined.upper()


# --------------------------------------------------------------- currency

def test_currency_symbol_glued_to_digits_matches():
    from ingestion.receipt.currency_extractor import (
        extract_currency_candidates as _ecc)
    cands = _ecc(None, "UKUPNO €84.80")
    assert cands and cands[0].value == "EUR"
    assert cands[0].confidence >= 0.9


def test_currency_locale_fallback_low_conf():
    from ingestion.receipt.currency_extractor import (
        extract_currency_candidates as _ecc)
    cands = _ecc(None, "Mleko 579,98\nHleb 120,00",
                 user_locale="de-DE")
    assert cands, "fallback must activate instead of []"
    assert cands[0].value == "EUR"
    assert cands[0].confidence == pytest.approx(0.3)
    assert any("fallback" in r.lower() for r in cands[0].reasons)


def test_currency_default_when_locale_unknown():
    from ingestion.receipt.currency_extractor import (
        extract_currency_candidates as _ecc)
    cands = _ecc(None, "no money marks here", user_locale=None,
                 default_currency="CHF")
    assert cands[0].value == "CHF"


def test_service_threads_currency_kwargs():
    from ingestion.receipt import service
    sig = inspect.signature(service.analyze_receipt)
    assert "user_locale" in sig.parameters
    assert sig.parameters["default_currency"].default == "EUR"
    bsig = inspect.signature(service._build_receipt_result)
    assert "default_currency" in bsig.parameters


# ------------------------------------------------------------------ dates

def test_single_structurally_valid_date_meets_low_conf():
    text = "PARAGON br 123\nDatum: 14.03.2025\nMleko 289,99"
    cands = extract_date_candidates(None, text)
    assert len(cands) == 1
    assert cands[0].confidence >= LOW_CONF, (
        f"lone date collapsed under LOW_CONF: {cands[0].confidence}")


def test_future_date_still_clamped():
    text = "Datum: 01.01.2099"
    cands = extract_date_candidates(None, text)
    assert cands and cands[0].confidence <= 0.45


# --------------------------------------------- shared confidence helper

def test_normalize_single_candidate_uses_explicit_value():
    assert normalize_confidences([7.0], single_value=0.75) == [0.75]
    assert normalize_confidences([]) == []


def test_normalize_span_and_clamps():
    confs = normalize_confidences([0.0, 10.0])
    assert confs[0] == pytest.approx(0.2)
    assert confs[1] == pytest.approx(0.95)
    tiny = normalize_confidences([5.0, 5.0000001])
    assert all(0.1 <= c <= 0.95 for c in tiny)


# ------------------------------------------------------------- line items

def test_line_items_grammar_qty_rows_and_phone_excluded():
    text = ("Mleko 2 x 289,99   579,98\n"
            "Tel: 011 123 45 67\n"
            "Kafa 120,00")
    items = extract_line_items(text)
    names = [i["description"] for i in items]
    assert names == ["Mleko", "Kafa"]
    milk = items[0]
    assert milk["quantity"] == 2
    assert milk["unit_price"] == 289.99
    assert milk["line_total"] == 579.98


def test_line_items_reject_row_without_trailing_amount():
    text = "Kafa 120,00\nNapomena: 120 dinara ukupno"
    items = extract_line_items(text)
    names = [i["description"] for i in items]
    assert names == ["Kafa"], (
        f"row lacking trailing amount leaked in: {names}")


def test_line_items_discount_rows_signed():
    items = extract_line_items("Popust kupon -50,00\nKafa 120,00")
    disc = [i for i in items if i["line_total"] < 0]
    assert disc and disc[0]["line_total"] == -50.0


def test_reconcile_reports_mismatch_not_clamp():
    items = [{"description": "A", "quantity": 1, "unit_price": 10.0,
              "line_total": 10.0}]
    res = reconcile(items, 12.0)
    assert res["ok"] is False and res["delta"] == 2.0
    res_ok = reconcile(items, 10.0)
    assert res_ok["ok"] is True
