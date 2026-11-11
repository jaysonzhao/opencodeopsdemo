#!/usr/bin/env python3
"""Control-host credentials and client access for the OpenShell 0.1.7 server track."""
import argparse
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import time
from render_server import validate

MODEL_PROVIDER = 'opencode-model'
MODEL_PROFILE = 'opencode-openai-compatible'
TRACE_PROVIDER = 'opencode-mlflow'
TRACE_PROFILE = 'opencode-rhoai-mlflow'
AUTH_SECRET = 'opencode-server-auth'
TRACER = 'opencode-tracer'


def run(command, extra_env=None, input=None):
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    return subprocess.run(command, env=env, input=input, text=True, check=True,
                          capture_output=True, timeout=120).stdout


def oc(*args, **kw):
    return run(['oc', *args], **kw)


def oshell(*args, extra_env=None, **kw):
    return run([os.environ['OPENSHELL_BIN'], '--gateway', os.environ['GATEWAY'],
                '--workspace', os.environ.get('WORKSPACE','default'), *args],
               extra_env={**(extra_env or {}), 'XDG_CONFIG_HOME':os.environ['OPENSHELL_CONFIG_HOME']}, **kw)


def secret(ns, name):
    obj = json.loads(oc('-n',ns,'get','secret',name,'-o','json'))
    return {k:base64.b64decode(v).decode() for k,v in obj['data'].items()}


def create_secret(ns, name, values):
    doc = {'apiVersion':'v1','kind':'Secret','type':'Opaque',
           'metadata':{'namespace':ns,'name':name},
           'data':{k:base64.b64encode(v.encode()).decode() for k,v in values.items()}}
    oc('create','-f','-',input=json.dumps(doc))


def init_secrets(c):
    for name in (c['model_secret'],AUTH_SECRET):
        if oc('-n',c['namespace'],'get','secret',name,'--ignore-not-found','-o','name').strip():
            print(name+': retained; values not displayed')
            continue
        if name == c['model_secret']:
            key = getpass.getpass('OpenAI-compatible API key (hidden): ')
            if not key or '\n' in key or '\r' in key:
                raise ValueError('Empty or invalid API key')
            values = {c['model_secret_key']:key}
        else:
            values = {'OPENCODE_SERVER_USERNAME':'opencode','OPENCODE_SERVER_PASSWORD':secrets.token_urlsafe(32)}
        create_secret(c['namespace'],name,values)
        print(name+': created')


def model_key(c):
    value = secret(c['namespace'],c['model_secret'])[c['model_secret_key']]
    if not value or '\n' in value or '\r' in value:
        raise ValueError('Invalid model Secret key')
    return value


def token(c):
    value = oc('-n',c['namespace'],'create','token',TRACER,'--duration=2h').strip()
    parts = value.split('.')
    if len(parts) != 3:
        raise ValueError('Expected TokenRequest JWT')
    claims = json.loads(base64.urlsafe_b64decode(parts[1]+'='*(-len(parts[1])%4)))
    if claims.get('sub') != 'system:serviceaccount:'+c['namespace']+':'+TRACER:
        raise ValueError('Unexpected TokenRequest subject')
    expiry = claims.get('exp')
    if type(expiry) is not int or expiry <= time.time()+600:
        raise ValueError('Token lifetime must exceed ten minutes')
    return value, expiry


def check_profile(name, env_key):
    doc = json.loads(oshell('profile','export',name,'-o','json'))
    creds = [v for v in doc.get('credentials',[]) if env_key in v.get('env_vars',[])]
    if (len(creds) != 1 or creds[0].get('stable_placeholder') is not True
            or creds[0].get('refresh') or creds[0].get('token_grant') or not doc.get('endpoints')):
        raise ValueError('Require the dedicated endpoint-bound stable credential profile')


def create_providers(c):
    check_profile(MODEL_PROFILE,'MODEL_API_KEY')
    check_profile(TRACE_PROFILE,'MLFLOW_TRACKING_TOKEN')
    oshell('provider','create','--name',MODEL_PROVIDER,'--type',MODEL_PROFILE,
           '--credential','MODEL_API_KEY',extra_env={'MODEL_API_KEY':model_key(c)})
    print('Created model provider; inspect partial state before retrying after any later failure')
    value, expiry = token(c)
    oshell('provider','create','--name',TRACE_PROVIDER,'--type',TRACE_PROFILE,
           '--credential','MLFLOW_TRACKING_TOKEN',extra_env={'MLFLOW_TRACKING_TOKEN':value})
    oshell('provider','update',TRACE_PROVIDER,'--credential-expires-at','MLFLOW_TRACKING_TOKEN='+str(expiry*1000))
    print('MLflow credential expiry UTC:',datetime.fromtimestamp(expiry,timezone.utc).isoformat())


def stage(c, sandbox):
    auth = secret(c['namespace'],AUTH_SECRET)
    payload = json.dumps({'runtime.json':c,'server-auth.json':{
        k:auth[k] for k in ('OPENCODE_SERVER_USERNAME','OPENCODE_SERVER_PASSWORD')}})
    code = """import json,os,pathlib,sys,tempfile
os.umask(0o077)
p=pathlib.Path('/sandbox/.opencode-server');p.mkdir(parents=True,exist_ok=True)
data=json.load(sys.stdin)
assert set(data)=={'runtime.json','server-auth.json'}
for name,value in data.items():
 with tempfile.NamedTemporaryFile(mode='w',dir=p,delete=False) as f:
  json.dump(value,f);temp=f.name
 os.replace(temp,p/name)
print('Runtime and application login staged')
"""
    oshell('sandbox','exec','-n',sandbox,'--no-login-shell','--','python3','-c',code,input=payload)
    print('Staged nonsecret runtime and application login; model/MLflow values remain outside')


def identity(sandbox):
    try:
        server = json.loads(oshell('sandbox','exec','-n',sandbox,'--no-login-shell','--',
            'python3','/opt/opencode-server/server_identity.py')).get('server')
    except Exception:
        server = None
    if server is not None:
        return {'mode':'process','server':server}

    observation = json.loads(oshell('sandbox','provider','status',sandbox,TRACE_PROVIDER,'--output','json'))
    targets = observation.get('targets',[])
    if len(targets) != 1:
        raise ValueError('Cannot identify the sandbox provider target')
    target = targets[0]
    observed = target.get('observed',{})
    desired = target.get('receipt',{}).get('desired',{})
    fields = {
        'sandbox_id':desired.get('sandbox_id'),
        'session_id':observed.get('session_id'),
        'process_instance_id':observed.get('process_instance_id'),
    }
    if not all(fields.values()):
        return None
    return {'mode':'sandbox-instance',**fields}


def application_health(c, port=4096):
    import urllib.request
    auth = secret(c['namespace'],AUTH_SECRET)
    credentials = auth['OPENCODE_SERVER_USERNAME']+':'+auth['OPENCODE_SERVER_PASSWORD']
    header = 'Basic '+base64.b64encode(credentials.encode()).decode()
    request = urllib.request.Request(f'http://127.0.0.1:{port}/global/health',
                                    headers={'Authorization':header})
    with urllib.request.urlopen(request,timeout=8) as response:
        if response.status != 200 or json.load(response).get('healthy') is not True:
            raise ValueError('Authenticated OpenCode health check failed')


@contextmanager
def writer_lock(sandbox):
    ident = '|'.join((os.environ['GATEWAY'],os.environ.get('WORKSPACE','default'),sandbox,TRACE_PROVIDER))
    path = Path(os.environ['OPENSHELL_CONFIG_HOME'])/('trace-renew-'+hashlib.sha256(ident.encode()).hexdigest()[:16]+'.lock')
    path.parent.mkdir(parents=True,exist_ok=True)
    fd = os.open(path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def renew(c, sandbox, port=4096):
    check_profile(TRACE_PROFILE,'MLFLOW_TRACKING_TOKEN')
    before = identity(sandbox)
    if before is None:
        raise ValueError('Cannot identify the running OpenShell sandbox instance')
    fallback = before['mode'] == 'sandbox-instance'
    if fallback:
        application_health(c,port)
    value, expiry = token(c)
    oshell('provider','update',TRACE_PROVIDER,'--credential','MLFLOW_TRACKING_TOKEN',
           '--credential-expires-at','MLFLOW_TRACKING_TOKEN='+str(expiry*1000),
           '--wait','--timeout','60','--output','json',extra_env={'MLFLOW_TRACKING_TOKEN':value})
    status = json.loads(oshell('sandbox','provider','status',sandbox,TRACE_PROVIDER,
                               '--wait','--timeout','60','--output','json'))
    targets = status.get('targets',[])
    if len(targets) != 1:
        raise ValueError('Cannot verify the updated sandbox provider target')
    observed = targets[0].get('observed',{})
    if observed.get('credentials_installed') is not True or observed.get('reason') == 'credential_expired':
        raise ValueError('MLflow provider credential did not activate')
    after = identity(sandbox)
    if after != before:
        raise ValueError('OpenShell sandbox identity changed during renewal')
    if fallback:
        application_health(c,port)
        evidence = {'identity_mode':'sandbox-instance','sandbox_instance_unchanged':True,
                    'server_health_verified':True,'process_identity_verified':False}
    else:
        evidence = {'identity_mode':'process','server_process_unchanged':True}
    print(json.dumps({'event':'mlflow_renewed','expires_at':expiry,**evidence,
                      'trace_delivery_verified':False}),flush=True)
    return expiry


def client_env(c):
    auth = secret(c['namespace'],AUTH_SECRET)
    return {**os.environ, **{k:auth[k] for k in ('OPENCODE_SERVER_USERNAME','OPENCODE_SERVER_PASSWORD')}}


def smoke(c, directory, port=4096):
    import urllib.request
    import urllib.error
    import urllib.parse
    env = client_env(c)
    base = f'http://127.0.0.1:{port}'
    query = '?'+urllib.parse.urlencode({'directory':directory})
    creds = env['OPENCODE_SERVER_USERNAME']+':'+env['OPENCODE_SERVER_PASSWORD']
    headers = {'Authorization':'Basic '+base64.b64encode(creds.encode()).decode(),'Content-Type':'application/json'}
    def api(path, data=None):
        req = urllib.request.Request(base+path+query,headers=headers,
              data=None if data is None else json.dumps(data).encode())
        with urllib.request.urlopen(req,timeout=240) as r:
            return json.load(r)
    if not api('/global/health').get('healthy'):
        raise ValueError('Server health failed')
    if api('/path')['directory'] != directory:
        raise ValueError('Remote directory mismatch')
    try:
        urllib.request.urlopen(base+'/session'+query,timeout=10)
        raise ValueError('Anonymous session access succeeded')
    except urllib.error.HTTPError as exc:
        if exc.code != 401:
            raise
    marker = 'opencode-internal-'+secrets.token_hex(8)
    session = api('/session',{'title':marker})
    response = api('/session/'+session['id']+'/message',
                   {'parts':[{'type':'text','text':'Do not use tools. Reply exactly: '+marker}]})
    reply = '\n'.join(x.get('text','') for x in response.get('parts',[]) if x.get('type') == 'text')
    if marker not in reply:
        raise ValueError('Model response did not contain the marker')
    print(json.dumps({'session_id':session['id'],'marker':marker,'model_response_verified':True}))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['init-secrets','create-providers','stage','attach','smoke','renew-mlflow','renew-loop','rotate-model'])
    p.add_argument('--runtime',default=str(Path(os.environ.get('LAB_DIR','.'))/'generated/runtime.json'))
    p.add_argument('--sandbox',default=os.environ.get('SANDBOX','opencode-server'))
    p.add_argument('--dir',default='/sandbox/workspace')
    p.add_argument('--port',type=int,default=int(os.environ.get('LOCAL_OPENCODE_PORT','4096')))
    a = p.parse_args()
    os.umask(0o077)
    if not 1 <= a.port <= 65535:
        raise ValueError('Port must be between 1 and 65535')
    c = validate(json.loads(Path(a.runtime).read_text()))
    if os.environ.get('NS') and os.environ['NS'] != c['namespace']:
        raise ValueError('Runtime namespace differs from the selected deployment namespace')
    # Static MLflow credential mode guard
    mode_path = Path(a.runtime).resolve().with_name('mlflow-credential-mode.json')
    if mode_path.exists():
        mode = json.loads(mode_path.read_text())
        if mode.get('provider') != TRACE_PROVIDER:
            raise ValueError('MLflow mode record targets another provider')
        if mode.get('mode') not in ('static-serviceaccount-secret', 'short-lived'):
            raise ValueError('Unknown MLflow credential mode')
        if mode['mode'] == 'static-serviceaccount-secret' and a.action in (
                'create-providers', 'renew-mlflow', 'renew-loop'):
            raise ValueError('Static MLflow mode: use the static migration/rotation workflow')
    if a.action in ('create-providers','renew-mlflow','renew-loop','rotate-model'):
        if not re.search(r'\b0\.1\.7\b',run([os.environ['OPENSHELL_BIN'],'--version'])):
            raise ValueError('Use matching OpenShell 0.1.7 trusted components')
    if a.action == 'init-secrets':
        init_secrets(c)
    elif a.action == 'create-providers':
        create_providers(c)
    elif a.action == 'stage':
        stage(c,a.sandbox)
    elif a.action == 'attach':
        subprocess.run(['opencode','attach',f'http://127.0.0.1:{a.port}','--dir',a.dir],env=client_env(c),check=True)
    elif a.action == 'smoke':
        smoke(c,a.dir,a.port)
    elif a.action == 'rotate-model':
        check_profile(MODEL_PROFILE,'MODEL_API_KEY')
        oshell('provider','update',MODEL_PROVIDER,'--credential','MODEL_API_KEY','--wait','--timeout','60',
               extra_env={'MODEL_API_KEY':model_key(c)})
        print('Activated model value rotation; verify real inference using the same server')
    else:
        with writer_lock(a.sandbox):
            while True:
                try:
                    expiry = renew(c,a.sandbox,a.port)
                except Exception as exc:
                    print(json.dumps({'event':'renewal_failed','error_type':type(exc).__name__}),flush=True)
                    if a.action != 'renew-loop':
                        raise SystemExit(1)
                    time.sleep(60)
                    continue
                if a.action != 'renew-loop':
                    return
                time.sleep(max(60,min(1800,(expiry-time.time())/2)))


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:
        # Subprocess output, argv, URLs, keys and auth-bearing responses stay private.
        print(json.dumps({'event':'operation_failed','error_type':type(exc).__name__}))
        raise SystemExit(1)
