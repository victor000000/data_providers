from unittest.mock import patch

import pytest

from data_providers.data import symbols


def test_class_share_uses_ibkr_identity_and_keeps_option_root_separate():
    mapping = {"BRK B": {"ibkr": "BRK B", "yahoo": "BRK-B",
                         "massive": "BRK.B", "thetadata_option": "BRKB"}}
    with patch.object(symbols, "_MAP", mapping), patch.object(symbols, "_REVERSE", None):
        assert symbols.translate("BRK-B", "yahoo", "massive") == ("BRK B", "BRK.B")
        assert symbols.translate("BRKB", "thetadata_option", "ibkr") == ("BRK B", "BRK B")
        with pytest.raises(symbols.SymbolError):
            symbols.translate("BRK.B", "thetadata_option", "ibkr")


def test_unmapped_symbol_fails_before_request_instead_of_guessing():
    with patch.object(symbols, "_MAP", {}), patch.object(symbols, "_REVERSE", None):
        with pytest.raises(symbols.SymbolError):
            symbols.translate("BF-A", "yahoo", "massive")


def test_occ_fallback_strips_ibkr_internal_class_share_space():
    with patch.object(symbols, "_MAP", {"CRD A": {"ibkr": "CRD A"}}):
        assert symbols.occ_root("CRD A") == "CRDA"
