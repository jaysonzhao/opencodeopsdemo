#!/usr/bin/env python3
"""Render a deferred OpenAI-compatible deployment for OpenShell 0.1.7."""
import argparse
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
import yaml

OPENCODE = '/opt/opencode/bin/opencode'
FIELDS = {'namespace', 'model_base_url', 'model_id', 'model_context', 'model_output',
          'model_secret', 'model_secret_key', 'allow_internal_http_model', 'mlflow_uri',
          'mlflow_workspace', 'mlflow_experiment', 'python_binary'}


def url(value, label, allow_http=False):
    if not isinstance(value, str) or not value or any(c.isspace() for c in value):
        raise ValueError('Define '+label+' before rendering deployment files')
    u = urlsplit(value)
    if (not u.hostname or u.username or u.password or u.query or u.fragment
            or u.scheme not in ('https', 'http')):
        raise ValueError(label+' must be an HTTP(S) URL without credentials/query/fragment')
    if not re.fullmatch(r'[A-Za-z0-9.-]+', u.hostname):
        raise ValueError(label+' must use an exact DNS hostname')
    if u.hostname.endswith(('.invalid', '.example')) or 'YOUR_' in value:
        raise ValueError('Replace the '+label+' placeholder')
    if u.scheme == 'http' and not (allow_http and u.hostname.endswith(('.svc', '.svc.cluster.local'))):
        raise ValueError(label+' requires HTTPS; internal model HTTP needs explicit opt-in and Service DNS')
    if '..' in u.path or not re.fullmatch(r'/[A-Za-z0-9/_-]*', u.path or '/'):
        raise ValueError('Unsupported URL path in '+label)
    port = u.port or (443 if u.scheme == 'https' else 80)
    return u, port, u.path.rstrip('/')


def validate(c):
    if set(c) != FIELDS:
        raise ValueError('Use exactly the fields in settings.example.json; never put an API key here')
    for key in ('namespace', 'mlflow_workspace', 'model_secret'):
        if not isinstance(c[key], str) or not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', c[key]):
            raise ValueError('Invalid '+key)
    if not isinstance(c['model_secret_key'], str) or not re.fullmatch(r'[A-Za-z0-9_.-]+', c['model_secret_key']):
        raise ValueError('Invalid model_secret_key')
    if c['model_secret'] == 'opencode-server-auth':
        raise ValueError('Keep the model Secret separate from the application login Secret')
    if not isinstance(c['model_id'], str) or not c['model_id'] or any(x.isspace() for x in c['model_id']):
        raise ValueError('Set the exact served model_id')
    if any(type(c[k]) is not int for k in ('model_context', 'model_output')) or not 0 < c['model_output'] < c['model_context']:
        raise ValueError('Set verified model budgets: 0 < output < context')
    if type(c['allow_internal_http_model']) is not bool:
        raise ValueError('allow_internal_http_model must be a boolean')
    if not isinstance(c['python_binary'], str) or not c['python_binary'].startswith(('/usr/', '/opt/')) or '..' in Path(c['python_binary']).parts:
        raise ValueError('Set the resolved read-only Python executable from the image')
    if not isinstance(c['mlflow_experiment'], str) or not c['mlflow_experiment'].strip():
        raise ValueError('Set mlflow_experiment')
    url(c['model_base_url'], 'model_base_url', c['allow_internal_http_model'])
    if urlsplit(c['model_base_url']).path.rstrip('/').endswith(('/chat/completions','/responses')):
        raise ValueError('model_base_url must be the API prefix, not an operation endpoint')
    url(c['mlflow_uri'], 'mlflow_uri')
    return c


def endpoint(uri, operations, allow_http=False):
    u, port, prefix = url(uri, 'endpoint', allow_http)
    return {'host':u.hostname, 'port':port, 'path':prefix+'/**',
            'protocol':'rest', 'enforcement':'enforce',
            'rules':[{'allow':{'method':m, 'path':prefix+p}} for m,p in operations]}


def profile(pid, category, env, ep, binaries):
    return {'id':pid, 'display_name':pid, 'description':'Dedicated OpenCode internal server',
            'category':category, 'inference_capable':category == 'inference',
            'credentials':[{'name':'credential', 'env_vars':[env], 'required':True,
                            'auth_style':'bearer', 'header_name':'authorization',
                            'stable_placeholder':True}],
            'discovery':{'credentials':['credential']}, 'endpoints':[ep], 'binaries':binaries}


def render(settings, out):
    c = validate(settings)
    model = endpoint(c['model_base_url'], [('POST','/chat/completions'), ('GET','/models')], c['allow_internal_http_model'])
    ops = [('GET','/api/2.0/mlflow/experiments/get'), ('GET','/api/2.0/mlflow/experiments/get-by-name'),
           ('POST','/api/2.0/mlflow/experiments/create'), ('POST','/api/2.0/otel/v1/traces'),
           ('POST','/api/4.0/mlflow/traces'), ('POST','/api/4.0/mlflow/traces/**'),
           ('PUT','/api/2.0/mlflow-artifacts/artifacts/**'), ('GET','/api/2.0/mlflow-artifacts/artifacts/**')]
    for version in ('2.0','3.0'):
        for method in ('GET','POST','PATCH'):
            ops.extend([(method, '/api/'+version+'/mlflow/traces'), (method, '/api/'+version+'/mlflow/traces/**')])
    trace = endpoint(c['mlflow_uri'], ops)
    trace['path'] = url(c['mlflow_uri'], 'mlflow_uri')[2]+'/api/**'
    policy = {'version':1, 'filesystem_policy':{'include_workdir':False,
              'read_only':['/bin','/usr','/lib','/lib64','/etc','/opt','/proc','/dev/urandom'],
              'read_write':['/sandbox','/tmp','/var/tmp','/dev/null']},
              'landlock':{'compatibility':'hard_requirement'}, 'network_policies':{
                  'model_api':{'endpoints':[model], 'binaries':[{'path':OPENCODE}]},
                  'rhoai_mlflow':{'endpoints':[trace], 'binaries':[{'path':OPENCODE},{'path':c['python_binary']}]}}}
    documents = {'policy.yaml':policy,
        'model-profile.yaml':profile('opencode-openai-compatible','inference','MODEL_API_KEY',model,[OPENCODE]),
        'mlflow-profile.yaml':profile('opencode-rhoai-mlflow','data','MLFLOW_TRACKING_TOKEN',trace,[OPENCODE,c['python_binary']])}
    files = {name:yaml.safe_dump(value, sort_keys=False) for name,value in documents.items()}
    files['runtime.json'] = json.dumps(c, indent=2)+'\n'
    out = Path(out)
    for name, content in files.items():
        path = out/name
        if path.exists() and path.read_text() != content:
            raise ValueError('Use a new output directory; refusing to overwrite '+str(path))
    out.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (out/name).write_text(content)
    return files


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--settings', required=True)
    p.add_argument('--out', required=True)
    a = p.parse_args()
    render(json.loads(Path(a.settings).read_text()), a.out)
    print('Rendered nonsecret settings, exact model/MLflow policies and stable credential profiles')
