#!/usr/bin/env python3
"""Bootstrap and rotate OpenCode credentials without secrets in command arguments."""
import argparse
import base64
from datetime import datetime, timezone
import getpass
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time

MODEL_SECRET='opencode-minimax'
AUTH_SECRET='opencode-openshell-auth'


def oc(*args, **kw):
    return subprocess.run(['oc',*args],check=True,text=True,**kw)


def read_secret(ns,name):
    obj=json.loads(oc('-n',ns,'get','secret',name,'-o','json',stdout=subprocess.PIPE).stdout)
    return {k:base64.b64decode(v).decode() for k,v in obj['data'].items()}


def oshell(*args, extra_env=None, **kw):
    env=os.environ.copy()
    env['XDG_CONFIG_HOME']=os.environ['OPENSHELL_CONFIG_HOME']
    if extra_env:env.update(extra_env)
    command=[os.environ['OPENSHELL_BIN'],'--gateway',os.environ['GATEWAY'],
             '--workspace',os.environ.get('WORKSPACE','default'),*args]
    return subprocess.run(command,env=env,check=True,text=True,**kw)


def write_secret(ns,name,values):
    obj={'apiVersion':'v1','kind':'Secret','type':'Opaque',
         'metadata':{'namespace':ns,'name':name},
         'data':{k:base64.b64encode(str(v).encode()).decode() for k,v in values.items()}}
    # Create-only: a rerun never rotates existing credentials implicitly.
    oc('create','-f','-',input=json.dumps(obj))


def ensure_secrets(ns,runtime):
    for name in (MODEL_SECRET,AUTH_SECRET):
        present=oc('-n',ns,'get','secret',name,'--ignore-not-found','-o','name',stdout=subprocess.PIPE).stdout.strip()
        if present:
            print(name+': present; retained')
            continue
        if name==MODEL_SECRET:
            key=getpass.getpass('MiniMax Global API key (hidden): ')
            if not key:raise ValueError('API key must not be empty')
            values={k:runtime[k] for k in ('BASE_URL','MODEL_NAME','MODEL_CONTEXT','MODEL_OUTPUT')}
            values['MINIMAX_API_KEY']=key
        else:
            values={'OPENCODE_SERVER_USERNAME':'opencode','OPENCODE_SERVER_PASSWORD':secrets.token_urlsafe(32)}
        write_secret(ns,name,values)


def check_model(ns,runtime):
    model=read_secret(ns,MODEL_SECRET)
    for key in ('BASE_URL','MODEL_NAME','MODEL_CONTEXT','MODEL_OUTPUT'):
        if str(model.get(key))!=str(runtime[key]):
            raise ValueError('Secret/runtime mismatch for '+key+'; inspect and update intentionally')
    if not model.get('MINIMAX_API_KEY'):raise ValueError('MiniMax key is empty')
    return model


def mlflow_token(ns):
    token=oc('-n',ns,'create','token','opencode-tracer','--duration=2h',stdout=subprocess.PIPE).stdout.strip()
    parts=token.split('.')
    if len(parts)!=3:raise ValueError('Expected a Kubernetes TokenRequest JWT')
    payload=json.loads(base64.urlsafe_b64decode(parts[1]+'='*(-len(parts[1])%4)))
    expiry=int(payload['exp'])
    if expiry<=time.time()+300:raise ValueError('Token lifetime is too short')
    return token,expiry


def store_mlflow(ns,create=False):
    token,expiry=mlflow_token(ns)
    env={'MLFLOW_TRACKING_TOKEN':token}
    if create:
        oshell('provider','create','--name','opencode-mlflow','--type','opencode-rhoai-mlflow',
               '--credential','MLFLOW_TRACKING_TOKEN',extra_env=env)
        oshell('provider','update','opencode-mlflow','--credential-expires-at',
               'MLFLOW_TRACKING_TOKEN='+str(expiry*1000))
    else:
        oshell('provider','update','opencode-mlflow','--credential','MLFLOW_TRACKING_TOKEN',
               '--credential-expires-at','MLFLOW_TRACKING_TOKEN='+str(expiry*1000),
               extra_env=env)
    print('MLflow token expires:',datetime.fromtimestamp(expiry,timezone.utc).isoformat())
    print('Renew before expiry; stop/start the idle sandbox to refresh the running server reference.')


def stage(ns,sandbox,runtime):
    check_model(ns,runtime)
    auth=read_secret(ns,AUTH_SECRET)
    auth={k:auth[k] for k in ('OPENCODE_SERVER_USERNAME','OPENCODE_SERVER_PASSWORD')}
    # Only application login data enters the sandbox; provider keys and real MLflow tokens do not.
    payload=json.dumps({'runtime.json':runtime,'server-auth.json':auth})
    program="""import json,os,pathlib,sys,tempfile
os.umask(0o077)
p=pathlib.Path('/sandbox/.opencode-setup');p.mkdir(parents=True,exist_ok=True)
data=json.load(sys.stdin)
assert set(data)=={'runtime.json','server-auth.json'}
for name,value in data.items():
 with tempfile.NamedTemporaryFile(mode='w',dir=p,delete=False) as f:
  json.dump(value,f);temp=f.name
 os.replace(temp,p/name)
print('Runtime and server-login files staged')
"""
    oshell('sandbox','exec','-n',sandbox,'--no-login-shell','--','python3','-c',program,input=payload)


def client_env(ns):
    auth=read_secret(ns,AUTH_SECRET)
    env=os.environ.copy()
    env.update({k:auth[k] for k in ('OPENCODE_SERVER_USERNAME','OPENCODE_SERVER_PASSWORD')})
    return env


def smoke(env,directory):
    import urllib.request,urllib.error,urllib.parse
    base='http://127.0.0.1:4096'
    query='?'+urllib.parse.urlencode({'directory':directory})
    creds=env['OPENCODE_SERVER_USERNAME']+':'+env['OPENCODE_SERVER_PASSWORD']
    auth='Basic '+base64.b64encode(creds.encode()).decode()
    def api(path,data=None):
        req=urllib.request.Request(base+path+query,data=None if data is None else json.dumps(data).encode(),
              headers={'Authorization':auth,'Content-Type':'application/json'})
        with urllib.request.urlopen(req,timeout=240) as response:return json.load(response)
    assert api('/global/health')['healthy']
    assert api('/path')['directory']==directory
    try:
        urllib.request.urlopen(base+'/session'+query,timeout=10)
        raise ValueError('Anonymous session access unexpectedly succeeded')
    except urllib.error.HTTPError as exc:
        if exc.code!=401:raise
    marker='opencode-openshell-'+str(int(time.time()))
    session=api('/session',{'title':marker})
    response=api('/session/'+session['id']+'/message',
                 {'parts':[{'type':'text','text':'Do not use tools. Reply exactly: '+marker}]})
    reply='\n'.join(p.get('text','') for p in response.get('parts',[]) if p.get('type')=='text')
    if marker not in reply:raise ValueError('Model did not return the expected marker')
    print(json.dumps({'session_id':session['id'],'marker':marker,'reply':reply}))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['init-secrets','create-providers','rotate-mlflow','rotate-minimax','stage','attach','smoke'])
    p.add_argument('--runtime',default='generated/runtime.json')
    p.add_argument('--namespace',default=os.environ.get('NS','opencode-agents'))
    p.add_argument('--sandbox',default=os.environ.get('SANDBOX','opencode'))
    p.add_argument('--dir',default='/sandbox/workspace/app')
    a=p.parse_args();runtime=json.loads(Path(a.runtime).read_text())
    if runtime['MLFLOW_WORKSPACE']!=a.namespace:raise ValueError('Namespace and MLflow workspace must match this baseline')
    if a.action=='init-secrets':ensure_secrets(a.namespace,runtime)
    elif a.action=='create-providers':
        model=check_model(a.namespace,runtime)
        oshell('provider','create','--name','opencode-minimax','--type','opencode-minimax-global',
               '--credential','MINIMAX_API_KEY',extra_env={'MINIMAX_API_KEY':model['MINIMAX_API_KEY']})
        store_mlflow(a.namespace,create=True)
    elif a.action=='rotate-mlflow':store_mlflow(a.namespace)
    elif a.action=='rotate-minimax':
        model=check_model(a.namespace,runtime)
        oshell('provider','update','opencode-minimax','--credential','MINIMAX_API_KEY',
               extra_env={'MINIMAX_API_KEY':model['MINIMAX_API_KEY']})
        print('Stop/start the idle sandbox after updating the provider')
    elif a.action=='stage':stage(a.namespace,a.sandbox,runtime)
    elif a.action=='attach':
        subprocess.run(['opencode','attach','http://127.0.0.1:4096','--dir',a.dir],env=client_env(a.namespace),check=True)
    elif a.action=='smoke':smoke(client_env(a.namespace),a.dir)


if __name__=='__main__':
    try:main()
    except (ValueError,KeyError,subprocess.CalledProcessError) as exc:
        # Do not dump captured subprocess output or credential-bearing objects.
        print('Operation failed: '+str(exc),file=sys.stderr)
        raise SystemExit(1)
