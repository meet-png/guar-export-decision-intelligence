"""The personalised brief must carry live numbers AND the non-removable
honesty (no-forecast line + caveat + the lead move)."""

from src.product.exporter_roi import ExporterProfile
from src.product.pilot_brief import render_brief


def test_brief_has_live_numbers_and_structure():
    md = render_brief(ExporterProfile(annual_tonnes=600))
    assert "Your #1 move" in md
    assert "600 t/yr" in md or "600 t" in md
    assert "WHEN" in md and "WHERE" in md
    assert "L/yr" in md  # rupee figures rendered
    assert "historical analogue, NOT a forecast" in md.replace("\n", " ")


def test_brief_cannot_drop_the_honesty():
    md = render_brief()
    assert "does **not** forecast the guar price" in md
    assert "do NOT forecast" in md  # the caveat line from the lead move


def test_brief_reflects_the_profile():
    big = render_brief(ExporterProfile(annual_tonnes=1500))
    assert "1,500 t/yr" in big or "1500 t" in big.replace(",", "")
