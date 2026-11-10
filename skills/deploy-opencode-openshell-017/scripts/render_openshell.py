#!/usr/bin/env python3
"""Render nonsecret OpenShell profiles, policy and runtime settings offline."""
import argparse
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
import yaml

OPENCODE = '/opt/opencode/bin/opencode'


def render(out, namespace, model, mlflow_uri, python_binary, context=32768, output=8192):
    if not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', namespace):
        raise ValueError('Use an actual Kubernetes namespace name')
    if not model or any(c.isspace() for c in model):
        raise ValueError('Use the exact MiniMax model ID enabled for the account')
    if not python_binary.startswith(('/usr/', '/opt/')) or '..' in Path(python_binary).parts:
        raise ValueError('Use the observed Python executable in the read-only image')
    uri = urlsplit(mlflow_uri)
    if uri.scheme != 'https' or not uri.hostname or uri.username or uri.password or uri.query or uri.fragment:
        raise ValueError('MLflow URI must be HTTPS without credentials, query or fragment')
    if not re.fullmatch(r'/[A-Za-z0-9/_-]*', uri.path or '/') or '..' in uri.path:
        raise ValueError('Use an ordinary MLflow URL path prefix')
    if not 0 < output < context:
        raise ValueError('Require 0 < output < context')
    prefix = uri.path.rstrip('/')
    mini = {'host':'api.minimax.io','port':443,'path':'/anthropic/v1/**',
            'protocol':'rest','enforcement':'enforce','rules':[
                {'allow':{'method':'POST','path':'/anthropic/v1/messages'}},
                {'allow':{'method':'POST','path':'/anthropic/v1/messages/count_tokens'}}]}
    allowed = [('GET','/api/2.0/mlflow/experiments/get'),
               ('GET','/api/2.0/mlflow/experiments/get-by-name'),
               ('POST','/api/2.0/mlflow/experiments/create'),
               ('POST','/api/2.0/otel/v1/traces'),
               ('POST','/api/4.0/mlflow/traces/**')]
    for version in ('2.0','3.0'):
        for method in ('GET','POST','PATCH'):
            allowed.append((method, '/api/'+version+'/mlflow/traces'))
            allowed.append((method, '/api/'+version+'/mlflow/traces/**'))
    allowed += [('PUT','/api/2.0/mlflow-artifacts/artifacts/**'),
                ('GET','/api/2.0/mlflow-artifacts/artifacts/**')]
    tracing = {'host':uri.hostname,'port':uri.port or 443,'path':prefix+'/api/**',
               'protocol':'rest','enforcement':'enforce',
               'rules':[{'allow':{'method':m,'path':prefix+p}} for m,p in allowed]}
    def profile(pid, title, category, key, auth_style, header, endpoint, binaries):
        return {'id':pid,'display_name':title,'description':title+' for this OpenCode sandbox',
                'category':category,'inference_capable':category=='inference',
                'credentials':[{'name':'credential','env_vars':[key],'required':True,
                                'auth_style':auth_style,'header_name':header}],
                'discovery':{'credentials':['credential']},'endpoints':[endpoint],'binaries':binaries}
    profiles = {
        'minimax-profile.yaml':profile('opencode-minimax-global','MiniMax Global','inference',
            'MINIMAX_API_KEY','header','x-api-key',mini,[OPENCODE]),
        'mlflow-profile.yaml':profile('opencode-rhoai-mlflow','OpenShift AI MLflow','data',
            'MLFLOW_TRACKING_TOKEN','bearer','authorization',tracing,[OPENCODE,python_binary])}
    policy = {'version':1,'filesystem_policy':{'include_workdir':False,
              'read_only':['/bin','/usr','/lib','/lib64','/etc','/opt','/proc','/dev/urandom'],
              'read_write':['/sandbox','/tmp','/var/tmp','/dev/null']},
              'landlock':{'compatibility':'hard_requirement'},
              'network_policies':{
                  'minimax_global':{'endpoints':[mini],'binaries':[{'path':OPENCODE}]},
                  'rhoai_mlflow':{'endpoints':[tracing],
                                  'binaries':[{'path':OPENCODE},{'path':python_binary}]}}}
    runtime = {'BASE_URL':'https://api.minimax.io/anthropic/v1','MODEL_NAME':model,
               'MODEL_CONTEXT':context,'MODEL_OUTPUT':output,
               'MLFLOW_TRACKING_URI':mlflow_uri.rstrip('/'),'MLFLOW_WORKSPACE':namespace,
               'MLFLOW_EXPERIMENT_NAME':'opencode-openshell-traces'}
    files={**{k:yaml.safe_dump(v,sort_keys=False) for k,v in profiles.items()},
           'policy.yaml':yaml.safe_dump(policy,sort_keys=False),
           'runtime.json':json.dumps(runtime,indent=2)+'\n'}
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    for name,content in files.items():
        path=out/name
        if path.exists() and path.read_text()!=content:
            raise ValueError('Use a new output directory; refusing to overwrite '+str(path))
    for name,content in files.items():(out/name).write_text(content)
    return files


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',required=True);p.add_argument('--namespace',default='opencode-agents')
    p.add_argument('--model',required=True);p.add_argument('--mlflow-uri',required=True)
    p.add_argument('--python-binary',required=True)
    p.add_argument('--context',type=int,default=32768);p.add_argument('--output',type=int,default=8192)
    a=p.parse_args()
    render(a.out,a.namespace,a.model,a.mlflow_uri,a.python_binary,a.context,a.output)
    print('Rendered policy, two endpoint-bound profiles and nonsecret runtime settings')
