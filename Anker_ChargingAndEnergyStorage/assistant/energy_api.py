import hashlib
import json
import math
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


class EnergyAPI:
    def __init__(self, base='http://127.0.0.1:19388', model_base='http://127.0.0.1:4178'):
        for url in (base, model_base):
            parsed = urlsplit(url)
            if parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', 'localhost'} or parsed.path or parsed.query:
                raise ValueError('Only fixed loopback services are allowed')
        self.base, self.model_base = base, model_base

    def request(self, path, data=None, model=False):
        request = Request((self.model_base if model else self.base) + path,
                          data=json.dumps(data, ensure_ascii=False).encode() if data is not None else None,
                          headers={'Content-Type': 'application/json'})
        try:
            with urlopen(request, timeout=15 if not model else 50) as response:
                return json.loads(response.read(4_000_000))
        except HTTPError as error:
            try:
                payload = json.loads(error.read(4000))
                raise RuntimeError(payload.get('sentence') or payload.get('error') or 'service_unavailable') from None
            except (ValueError, AttributeError):
                raise RuntimeError('service_unavailable') from None
        except (URLError, TimeoutError, OSError, ValueError):
            raise RuntimeError('service_unavailable') from None

    def comparison(self, household):
        payload = self.request('/api/value-comparison')
        if payload.get('schemaVersion') != 'fv01-comparison-v1' or payload.get('status') != 'valid':
            raise RuntimeError('comparison_unavailable')
        site = next((s for s in payload.get('sites', []) if s.get('id') == household), None)
        if not site or site.get('status') != 'valid' or not site.get('slots'):
            raise RuntimeError('comparison_unavailable')
        return payload, site

    @staticmethod
    def value(site, key):
        evidence = site.get('summary', {}).get(key, {})
        value = evidence.get('value')
        if evidence.get('status') != 'computed' or isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
            raise RuntimeError('comparison_unavailable')
        return value

    def plan(self, identity, action, **kwargs):
        key = hashlib.sha256('|'.join((identity['app'], identity['sender'], identity['household'])).encode()).hexdigest()[:24]
        return self.request('/api/plan', {'household': 'feishu_' + key, 'action': action, **kwargs})

    def explain(self, question, facts):
        return self.request('/api/explain', {'question': question[:300], 'context': facts}, model=True)['answer']
