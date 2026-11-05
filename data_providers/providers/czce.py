"""CZCE's original annual option file over its working HTTPS endpoint."""
from io import StringIO
import re

import pandas as pd
import requests


def yearly_options(symbol, year):
    symbol, year = str(symbol).upper(), str(year)
    if not re.fullmatch(r"[A-Z]+", symbol) or not re.fullmatch(r"\d{4}", year):
        raise ValueError("Invalid CZCE product or year")
    url = (f"https://www.czce.com.cn/cn/DFSStaticFiles/Option/{year}/"
           f"OptionDataAllHistory/{symbol}OPTIONS{year}.txt")
    response = requests.get(url, timeout=(10, 60))
    response.raise_for_status()
    frame = pd.read_table(StringIO(response.text), skiprows=1, sep="|", low_memory=False)
    if not {"交易日期", "合约代码"}.issubset(set(frame.columns.str.strip())):
        raise ValueError("CZCE returned an invalid annual option file")
    return frame
