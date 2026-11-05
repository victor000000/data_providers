from unittest.mock import Mock, patch

import pytest
import requests

from data_providers.providers.czce import yearly_options


def test_annual_file_uses_https_and_bounded_timeout():
    response = Mock(text="CZCE annual options\n交易日期|合约代码\n2026-10-16|MA701C2500\n")
    with patch("data_providers.providers.czce.requests.get", return_value=response) as get:
        frame = yearly_options("ma", 2026)
    assert frame.iloc[0]["合约代码"] == "MA701C2500"
    assert get.call_args.args[0] == (
        "https://www.czce.com.cn/cn/DFSStaticFiles/Option/2026/"
        "OptionDataAllHistory/MAOPTIONS2026.txt")
    assert get.call_args.kwargs["timeout"] == (10, 60)
    response.raise_for_status.assert_called_once()


def test_exchange_challenge_is_not_option_data():
    with patch("data_providers.providers.czce.requests.get",
               return_value=Mock(text="challenge\n<html>Access denied</html>")):
        with pytest.raises(ValueError, match="invalid annual"):
            yearly_options("MA", 2026)


def test_http_failure_does_not_parse_or_publish_data():
    response = Mock()
    response.raise_for_status.side_effect = requests.HTTPError("502")
    with patch("data_providers.providers.czce.requests.get", return_value=response):
        with pytest.raises(requests.HTTPError):
            yearly_options("MA", 2026)
