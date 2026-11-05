"""A yfinance-compatible session that paces every Yahoo HTTP request."""
from curl_cffi import requests
from curl_cffi.const import CurlHttpVersion
from data_providers.shared.net import throttle


class PacedSession(requests.Session):
    def get(self, *args, **kwargs):
        throttle('yfinance')
        return super().get(*args, **kwargs)

    def post(self, *args, **kwargs):
        throttle('yfinance')
        return super().post(*args, **kwargs)


def create_session():
    return PacedSession(impersonate='chrome', http_version=CurlHttpVersion.V1_1)
