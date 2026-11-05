"""Bounded Sina contracts/chain transport with immutable response receipts."""
import ast
import hashlib
import json
import time
from datetime import date
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from data_providers.shared.net import throttle

BASE = 'https://stock.finance.sina.com.cn'


class SinaFutures:
    def __init__(self, raw_dir, *, tries=4, timeout=(10,35)):
        self.tries = tries
        self.timeout = timeout
        self.raw_dir=Path(raw_dir);self.raw_dir.mkdir(parents=True,exist_ok=True)
        self.session=requests.Session()
        self.session.headers['User-Agent']='Mozilla/5.0'

    def get(self,url,params=None):
        last=None
        for attempt in range(self.tries):
            throttle('akshare')
            try:
                if attempt<2:
                    response=self.session.get(url,params=params,timeout=self.timeout)
                else:
                    from curl_cffi import requests as curl_requests
                    from curl_cffi.const import CurlHttpVersion
                    response=curl_requests.get(url,params=params,timeout=self.timeout[1],impersonate='chrome',
                                               http_version=CurlHttpVersion.V1_1)
                request_url=getattr(getattr(response,'request',None),'url',None) or url+str(params)
                digest=hashlib.sha256(request_url.encode()).hexdigest()+'-'+hashlib.sha256(response.content).hexdigest()
                (self.raw_dir/(digest+'.response')).write_bytes(response.content)
                response.raise_for_status()
                return response
            except Exception as exc:  # noqa: BLE001 - failed source requests remain explicit  # Record a failed transport, never parse it as empty data.
                last=exc
                time.sleep(min(attempt+1,3))
        raise RuntimeError(f'Sina transport failed: {type(last).__name__}') from last

    def products(self):
        page=self.get(BASE+'/futures/view/optionsDP.php/pg_o/dce')
        soup=BeautifulSoup(page.content,'lxml')
        return {li.a.text.strip():urljoin(BASE,li.a['href']) for li in soup.find_all('li',class_='active') if li.a}

    def months(self,page):
        soup=BeautifulSoup(self.get(page).content,'lxml')
        suffix=soup.find(id='option_suffix')
        if suffix is None:
            raise ValueError('Source did not return a contract month list')
        return [li.text.strip() for li in suffix.find_all('li') if li.text.strip()]

    def chain(self,product,venue,month):
        data=self.get(BASE+'/futures/api/openapi.php/OptionService.getOptionData',
                      {'type':'futures','product':product,'exchange':venue,'pinzhong':month}).json()
        return data['result']['data']

    def daily(self,contract):
        text=self.get('https://stock2.finance.sina.com.cn/futures/api/jsonp.php/var%20_daily=/InnerFuturesNewService.getDailyKLine',
                      {'symbol':contract.upper(),'type':date.today().strftime('%Y_%m_%d')}).text
        begin=text.find('=(')
        if begin<0:
            raise ValueError('Source did not return a daily series')
        data=json.loads(text[begin+2:text.rfind(');')])
        return data or []


    def option_daily(self,contract):
        text=self.get(BASE+'/futures/api/jsonp.php/var%20_option_daily=/FutureOptionAllService.getOptionDayline',
                      {'symbol':contract}).text
        begin,end=text.find('['),text.rfind(']')
        if begin<0 or end<begin:
            if 'null' in text:
                return []
            raise ValueError('Source did not return option history')
        payload=text[begin:end+1]
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return ast.literal_eval(payload)  # Data literals only; never eval source code.


SINA_NAMES = {
    'CF':'棉花期权','FG':'玻璃期权','MA':'甲醇期权','OI':'菜籽油期权',
    'PK':'花生期权','PX':'二甲苯期权','RM':'菜籽粕期权','SA':'纯碱期权',
    'SH':'烧碱期权','SR':'白糖期权','TA':'PTA期权','UR':'尿素期权','ZC':'动力煤期权',
    'AL':'沪铝期权','CU':'沪铜期权','RU':'橡胶期权','ZN':'沪锌期权',
}


def source_name(product,name):
    return SINA_NAMES.get(product,name if name.endswith('期权') else name+'期权')
