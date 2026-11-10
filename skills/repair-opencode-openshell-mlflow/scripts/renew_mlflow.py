#!/usr/bin/env python3
"""Renew MLflow outside the sandbox; publish a new opaque reference without restarting OpenCode."""
import argparse
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time


def run(command,extra_env=None):
    environment=os.environ.copy()
    if extra_env:environment.update(extra_env)
    return subprocess.run(command,env=environment,text=True,check=True,capture_output=True,timeout=120).stdout


def oshell(*args,extra_env=None):
    return run([os.environ['OPENSHELL_BIN'],'--gateway',os.environ['GATEWAY'],
      '--workspace',os.environ.get('WORKSPACE','default'),*args],
      {**(extra_env or {}),'XDG_CONFIG_HOME':os.environ['OPENSHELL_CONFIG_HOME']})


def remote(sandbox,script,*args):
    result=oshell('sandbox','exec','-n',sandbox,'--no-login-shell','--',
                 'python3','/opt/opencode-launch/'+script,*args)
    return json.loads(result)


def token(ns,sa,duration):
    value=run(['oc','-n',ns,'create','token',sa,'--duration='+duration]).strip()
    parts=value.split('.')
    if len(parts)!=3:raise ValueError('Expected a Kubernetes TokenRequest JWT')
    claims=json.loads(base64.urlsafe_b64decode(parts[1]+'='*(-len(parts[1])%4)))
    if claims.get('sub')!='system:serviceaccount:'+ns+':'+sa:
        raise ValueError('TokenRequest returned the wrong service-account subject')
    expiry=claims.get('exp')
    if type(expiry) is not int or expiry<=time.time()+600:
        raise ValueError('TokenRequest lifetime too short; require more than ten minutes')
    return value,expiry


def renew(args):
    before=remote(args.sandbox,'server_identity.py')['server']
    if before is None and not args.bootstrap:
        raise ValueError('No live OpenCode server; use --bootstrap only for initial setup')
    value,expiry=token(args.namespace,args.service_account,args.duration)
    oshell('provider','update',args.provider,'--credential','MLFLOW_TRACKING_TOKEN',
      '--credential-expires-at','MLFLOW_TRACKING_TOKEN='+str(expiry*1000),
      '--wait','--timeout','60','--output','json',extra_env={'MLFLOW_TRACKING_TOKEN':value})
    del value
    published=remote(args.sandbox,'mlflow_reference.py','--expires-at',str(expiry))
    if published.get('published') is not True or published.get('expires_at')!=expiry:
        raise ValueError('New reference was not published')
    after=remote(args.sandbox,'server_identity.py')['server']
    if before is not None and before!=after:
        raise ValueError('Server identity changed; investigate; renewal did not request a restart')
    print(json.dumps({'event':'renewed','expires_at':expiry,
      'expires_utc':datetime.fromtimestamp(expiry,timezone.utc).isoformat(),
      'server_unchanged':before is not None and before==after,
      'bootstrap':args.bootstrap}),flush=True)
    return expiry


def create_provider(args):
    value,expiry=token(args.namespace,args.service_account,args.duration)
    oshell('provider','create','--name',args.provider,'--type','opencode-rhoai-mlflow',
      '--credential','MLFLOW_TRACKING_TOKEN',extra_env={'MLFLOW_TRACKING_TOKEN':value})
    del value
    oshell('provider','update',args.provider,'--credential-expires-at',
      'MLFLOW_TRACKING_TOKEN='+str(expiry*1000))
    print(json.dumps({'event':'provider_created','expires_at':expiry}),flush=True)


def delay(expiry):
    return max(60,min(1800,(expiry-time.time())/2))


@contextmanager
def writer_lock(args):
    # Prevent duplicate writers on this control host; deploy only one control loop overall.
    identity='|'.join((os.environ['GATEWAY'],os.environ.get('WORKSPACE','default'),args.sandbox,args.provider))
    path=Path(os.environ['OPENSHELL_CONFIG_HOME'])/('mlflow-renew-'+hashlib.sha256(identity.encode()).hexdigest()[:16]+'.lock')
    path.parent.mkdir(parents=True,exist_ok=True)
    fd=os.open(path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        yield
    finally:os.close(fd)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--namespace',default=os.environ.get('NS','opencode-agents'))
    p.add_argument('--sandbox',default=os.environ.get('SANDBOX','opencode-repaired'))
    p.add_argument('--provider',default='opencode-mlflow-hot')
    p.add_argument('--service-account',default='opencode-tracer')
    p.add_argument('--duration',default='2h')
    p.add_argument('--bootstrap',action='store_true')
    p.add_argument('--create-provider',action='store_true')
    p.add_argument('--loop',action='store_true')
    args=p.parse_args()
    if sum((args.bootstrap,args.create_provider,args.loop))>1:p.error('Use one of --create-provider, --bootstrap or --loop at a time')
    os.umask(0o077)
    for key in ('OPENSHELL_BIN','OPENSHELL_CONFIG_HOME','GATEWAY'):
        if not os.environ.get(key):raise ValueError('Missing '+key)
    with writer_lock(args):
        if args.create_provider:
            create_provider(args)
            return
        last_expiry=None
        while True:
            try:
                last_expiry=renew(args)
            except Exception as exc:
                # Never emit subprocess output, complete argv, tokens or HTTP bodies.
                print(json.dumps({'event':'renewal_failed','error_type':type(exc).__name__,
                                  'last_successful_expiry':last_expiry}),flush=True)
                if not args.loop:raise SystemExit(1)
                time.sleep(60)
                continue
            if not args.loop:return
            time.sleep(delay(last_expiry))


if __name__=='__main__':
    try:main()
    except KeyboardInterrupt:raise SystemExit(130)
    except Exception as exc:
        print(json.dumps({'event':'controller_failed','error_type':type(exc).__name__}),flush=True)
        raise SystemExit(1)
