"""Create clearly labelled UI fixtures through the isolated local Manager API.

No model calls are made. The disabled model uses a reserved .invalid address.
Run explicitly after local.sh; this is never part of normal server startup.
"""
import json
from urllib.request import Request, urlopen
from urllib.parse import urlencode, quote

BASE = 'http://127.0.0.1:18765/api/v1'


def request(url, body=None, token=None, form=False):
    headers = {}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    data = None
    if body is not None:
        headers['Content-Type'] = 'application/x-www-form-urlencoded' if form else 'application/json'
        data = (urlencode(body) if form else json.dumps(body)).encode()
    with urlopen(Request(url, data=data, headers=headers), timeout=15) as response:
        result = json.load(response)
        return result['data'] if isinstance(result, dict) and 'data' in result and 'code' in result else result


def main():
    token = request('http://127.0.0.1:18770/v1/auth/token',
                    {'username': 'admin', 'password': 'admin'}, form=True)['access_token']
    def ensure(path, body):
        rows = request(f'{BASE}{path}?search={quote(body["template_name"])}', token=token)['items']
        existing = next((row for row in rows if row['template_name'] == body['template_name']), None)
        return existing or request(f'{BASE}{path}', body, token)
    model = ensure('/model-templates', {
        'template_name': '[本地演示] 不可调用模型', 'description': '仅供 Manager 界面验证，不连接真实模型。',
        'api_base': 'https://manager-demo.invalid/v1', 'api_key': 'local-demo-not-a-secret',
        'model_id': 'local-ui-demo', 'model_provider': 'OpenAI', 'enabled': False,
    })
    agent = ensure('/agent-templates/', {
        'template_name': '[本地演示] 客服助手', 'description': '仅验证资源引用、详情布局和绑定草稿。模型已停用，不具备执行能力。',
        'agent_tags': ['本地演示', '不可运行'], 'enabled': True,
        'template_ref': {'default_model': [model['template_id']]},
    })
    print(f'Local UI fixture: http://127.0.0.1:5273/manager/agent-templates/{agent["template_id"]}')


if __name__ == '__main__':
    main()
