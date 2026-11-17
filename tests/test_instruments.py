from inside_screener.instruments import (
    discover_fo_stock_underlyings,
    discover_nse_equities,
    load_custom_symbols,
    load_nifty_constituents,
    parse_custom_symbols,
    resolve_custom_symbols,
)


def test_fo_equity_underlyings_map_to_nse_eq_and_indices_are_excluded():
    records = [
        {"segment": "NSE_EQ", "instrument_type": "EQ", "instrument_key": "NSE_EQ|INE1", "trading_symbol": "ALPHA"},
        {"segment": "NSE_FO", "instrument_type": "FUT", "underlying_type": "EQUITY", "underlying_key": "NSE_EQ|INE1", "underlying_symbol": "ALPHA"},
        {"segment": "NSE_FO", "instrument_type": "OPT", "underlying_type": "EQUITY", "underlying_key": "NSE_EQ|INE1", "underlying_symbol": "ALPHA"},
        {"segment": "NSE_FO", "instrument_type": "FUT", "underlying_type": "INDEX", "underlying_symbol": "NIFTY"},
        {"segment": "NSE_FO", "instrument_type": "FUT", "underlying_type": "EQUITY", "underlying_key": "NSE_EQ|MISSING", "underlying_symbol": "BETA"},
    ]
    instruments, unresolved = discover_fo_stock_underlyings(records)
    assert [(item.symbol, item.instrument_key) for item in instruments] == [("ALPHA", "NSE_EQ|INE1")]
    assert unresolved == {"BETA": "no NSE_EQ equity mapping in the Upstox BOD file"}


def test_all_equity_universe_excludes_non_equities_and_deduplicates():
    records = [
        {"segment": "NSE_EQ", "instrument_type": "EQ", "instrument_key": "NSE_EQ|INE1", "trading_symbol": "ALPHA", "isin": "INE000A01010"},
        {"segment": "NSE_EQ", "instrument_type": "EQ", "instrument_key": "NSE_EQ|INE2", "trading_symbol": "BETA", "isin": "INE000B01010"},
        {"segment": "NSE_EQ", "instrument_type": "EQ", "instrument_key": "NSE_EQ|ETF", "trading_symbol": "GOLDBEES", "isin": "INF000A01010"},
        {"segment": "NSE_EQ", "instrument_type": "BE", "instrument_key": "NSE_EQ|BE", "trading_symbol": "BE", "isin": "INE000C01010"},
        {"segment": "NSE_INDEX", "instrument_type": "INDEX", "instrument_key": "NSE_INDEX|NIFTY", "trading_symbol": "NIFTY"},
        {"segment": "NSE_FO", "instrument_type": "FUT", "instrument_key": "NSE_FO|1", "trading_symbol": "ALPHA FUT"},
        {"segment": "NSE_EQ", "instrument_type": "EQ", "instrument_key": "NSE_EQ|INE1", "trading_symbol": "ALPHA", "isin": "INE000A01010"},
        {"segment": "NSE_EQ", "instrument_type": "EQ", "instrument_key": "", "trading_symbol": "MISSING", "isin": "INE000D01010"},
    ]
    instruments, excluded = discover_nse_equities(records)
    assert [(item.symbol, item.instrument_key) for item in instruments] == [("ALPHA", "NSE_EQ|INE1"), ("BETA", "NSE_EQ|INE2")]
    assert excluded["ETF or fund unit"] == 1
    assert excluded["instrument type: BE"] == 1
    assert excluded["duplicate instrument key"] == 1
    assert excluded["missing instrument key"] == 1


def test_custom_symbols_are_validated_against_current_equity_universe(tmp_path):
    source = tmp_path / "symbols.csv"
    source.write_text("symbol\nalpha\nUNKNOWN\nALPHA\n", encoding="utf-8")
    records = [
        {"segment": "NSE_EQ", "instrument_type": "EQ", "instrument_key": "NSE_EQ|INE1", "trading_symbol": "ALPHA", "isin": "INE000A01010"},
    ]
    requested = load_custom_symbols(source)
    instruments, rejected = resolve_custom_symbols(records, requested)
    assert [(item.symbol, item.instrument_key) for item in instruments] == [("ALPHA", "NSE_EQ|INE1")]
    assert rejected == {
        "UNKNOWN": "not an eligible NSE_EQ ordinary equity in the current Upstox BOD file",
        "ALPHA": "duplicate symbol in custom file",
    }


def test_official_nifty_constituent_cache_and_mapping(tmp_path):
    (tmp_path / "nifty50_constituents.csv").write_text(
        "Company Name,Symbol\nAlpha Ltd,ALPHA\nBeta Ltd,BETA\nAlpha Ltd,ALPHA\n", encoding="utf-8"
    )
    constituents = load_nifty_constituents(tmp_path, "nifty50")
    assert constituents.symbols == ["ALPHA", "BETA"]
    records = [
        {"segment": "NSE_EQ", "instrument_type": "EQ", "instrument_key": "NSE_EQ|ALPHA", "trading_symbol": "ALPHA", "isin": "INE000A01010"},
    ]
    instruments, unresolved = resolve_custom_symbols(records, constituents.symbols)
    assert [(item.symbol, item.instrument_key) for item in instruments] == [("ALPHA", "NSE_EQ|ALPHA")]
    assert unresolved == {"BETA": "not an eligible NSE_EQ ordinary equity in the current Upstox BOD file"}


def test_official_nifty_download_retries_a_transient_connection_failure(tmp_path, monkeypatch):
    from inside_screener import instruments as module
    import requests

    class Response:
        content = b"Company Name,Symbol\nAlpha Ltd,ALPHA\n"

        def raise_for_status(self):
            return None

    attempts = []

    def fake_get(*args, **kwargs):
        attempts.append(kwargs)
        if len(attempts) == 1:
            raise requests.ConnectionError("connection closed")
        return Response()

    monkeypatch.setattr(module.requests, "get", fake_get)
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    constituents = load_nifty_constituents(tmp_path, "nifty500", refresh=True)
    assert constituents.symbols == ["ALPHA"]
    assert len(attempts) == 2
    assert attempts[0]["headers"]["User-Agent"].startswith("Mozilla/")


def test_uploaded_custom_symbol_parser_matches_file_behavior():
    assert parse_custom_symbols("symbol\n alpha\nBETA,ignored\n") == ["ALPHA", "BETA"]
