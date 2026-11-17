from inside_screener.auth import extract_authorisation_code


def test_extract_authorisation_code_from_redirect_or_raw_value():
    assert extract_authorisation_code("http://127.0.0.1/callback?code=abc123&state=state") == "abc123"
    assert extract_authorisation_code("abc123") == "abc123"
