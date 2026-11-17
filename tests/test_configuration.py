import pytest

from inside_screener.configuration import validate_count_filter
from inside_screener.models import CountFilter


def test_web_configuration_count_validation():
    assert validate_count_filter(7, 10) == CountFilter(7, 10)
    assert validate_count_filter(7, None) == CountFilter(7)
    with pytest.raises(ValueError, match="at least 1"):
        validate_count_filter(0, None)
    with pytest.raises(ValueError, match="at least the minimum"):
        validate_count_filter(7, 6)
