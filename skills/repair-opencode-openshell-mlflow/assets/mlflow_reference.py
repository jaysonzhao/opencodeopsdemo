"""Publish only an opaque OpenShell reference after validating its MLflow access."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time

SETUP = Path('/sandbox/.opencode-setup')
REFERENCE_PATH = SETUP/'mlflow-reference.json'
PATTERN = re.compile(r'openshell:resolve:env:(?:v[0-9]+_)?MLFLOW_TRACKING_TOKEN\Z')


def valid(document, minimum_seconds=10):
    if not isinstance(document,dict) or not isinstance(document.get('reference'),str):
        raise ValueError('Malformed MLflow reference document')
    expiry=document.get('expires_at')
    if not PATTERN.fullmatch(document['reference']) or type(expiry) is not int:
        raise ValueError('Expected an opaque OpenShell MLflow reference and expiry')
    if expiry<=time.time()+minimum_seconds:
        raise ValueError('MLflow reference expired or too close to expiry')
    return document


def read_reference(wait=False):
    deadline=time.monotonic()+(600 if wait else 0)
    while True:
        try:
            fd=os.open(REFERENCE_PATH,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
            with os.fdopen(fd) as f:
                info=os.fstat(f.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size>4096:
                    raise ValueError('Invalid MLflow reference file')
                return valid(json.load(f))
        except (FileNotFoundError,ValueError):
            if time.monotonic()>=deadline:
                raise RuntimeError('Run the external renewal helper; no fresh MLflow reference is available') from None
            time.sleep(2)


def probe(reference):
    runtime=json.loads((SETUP/'runtime.json').read_text())
    if not runtime['MLFLOW_TRACKING_URI'].startswith('https://'):
        raise ValueError('MLflow must use verified HTTPS')
    for key in ('MLFLOW_TRACKING_AUTH','MLFLOW_TRACKING_USERNAME','MLFLOW_TRACKING_PASSWORD',
                'MLFLOW_TRACKING_INSECURE_TLS','MLFLOW_TOKEN_PATH'):
        os.environ.pop(key,None)
    os.environ['MLFLOW_TRACKING_TOKEN']=reference
    for key in ('MLFLOW_TRACKING_URI','MLFLOW_WORKSPACE','MLFLOW_EXPERIMENT_NAME'):
        os.environ[key]=str(runtime[key])
    # Keep OpenShell's injected TLS/proxy variables. Import after setting MLflow environment.
    os.environ['MLFLOW_HTTP_REQUEST_TIMEOUT']='20'
    os.environ['MLFLOW_HTTP_REQUEST_MAX_RETRIES']='0'
    import mlflow
    mlflow.set_tracking_uri(runtime['MLFLOW_TRACKING_URI'])
    mlflow.get_experiment_by_name(runtime['MLFLOW_EXPERIMENT_NAME'])


def publish_locked(expiry):
    os.umask(0o077)
    candidate=valid({'reference':os.environ.get('MLFLOW_TRACKING_TOKEN',''),'expires_at':expiry},300)
    try:
        previous=read_reference()['reference']
    except RuntimeError:
        previous=None
    if previous==candidate['reference']:
        raise ValueError('New exec did not receive a new provider reference; inspect readiness')
    probe(candidate['reference'])
    valid(candidate,300)
    SETUP.mkdir(parents=True,exist_ok=True)
    temp=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',dir=SETUP,delete=False) as f:
            temp=Path(f.name);os.fchmod(f.fileno(),0o600)
            json.dump(candidate,f);f.write('\n');f.flush();os.fsync(f.fileno())
        os.replace(temp,REFERENCE_PATH)
    finally:
        if temp is not None:temp.unlink(missing_ok=True)
    return {'published':True,'expires_at':expiry,'changed':True}


def publish(expiry):
    os.umask(0o077)
    SETUP.mkdir(parents=True,exist_ok=True)
    fd=os.open(SETUP/'mlflow-reference.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        return publish_locked(expiry)
    finally:os.close(fd)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expires-at',type=int,required=True)
    args=parser.parse_args()
    try:print(json.dumps(publish(args.expires_at)),flush=True)
    except Exception as exc:
        # A failed probe must not print HTTP bodies, auth headers or provider handles.
        print(json.dumps({'published':False,'error_type':type(exc).__name__}),flush=True)
        raise SystemExit(1)
