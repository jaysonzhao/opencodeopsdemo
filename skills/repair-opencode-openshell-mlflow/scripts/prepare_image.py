#!/usr/bin/env python3
"""Prepare a repaired image build directory from the prior reviewed deployment bundle."""
import argparse
import hashlib
from pathlib import Path

HASHES={
 'Containerfile':'c2bfd0cebc53b370d23e6425d438a346c6773aa7766beb5bf81e3e426eac05a9',
 'start.py':'5ae3d11505b32d54d66a6112dd4f9e54f815a5507b07c9cf0e1697362c5e1c6c',
 'check_trace.py':'96e26f166176d32d4cda13e9e184fa738aea539c5f7c789885f3d6e9d267cfe8'}


def once(text,old,new):
    if text.count(old)!=1:raise ValueError('Baseline anchor changed; inspect instead of forcing a patch')
    return text.replace(old,new)


def prepare(baseline,out):
    baseline=Path(baseline)/'assets/openshell';out=Path(out)
    contents={}
    for name,digest in HASHES.items():
        content=(baseline/name).read_bytes()
        if hashlib.sha256(content).hexdigest()!=digest:
            raise ValueError('Unexpected baseline '+name+'; compare with the 2026-11-09 deployment bundle')
        contents[name]=content.decode()
    contents['Containerfile']=once(contents['Containerfile'],
      'WORKDIR /opt/mlflow-src/libs/typescript',
      'COPY patch-auth.cjs /tmp/patch-auth.cjs\nRUN node /tmp/patch-auth.cjs\nWORKDIR /opt/mlflow-src/libs/typescript')
    contents['Containerfile']=once(contents['Containerfile'],
      'COPY start.py check_trace.py /opt/opencode-launch/',
      'COPY start.py check_trace.py mlflow_reference.py server_identity.py /opt/opencode-launch/')
    contents['start.py']=once(contents['start.py'],'import time\n',
      'import time\nfrom mlflow_reference import REFERENCE_PATH, read_reference\n')
    anchor="    os.environ.update({k:auth[k] for k in ('OPENCODE_SERVER_USERNAME', 'OPENCODE_SERVER_PASSWORD')})"
    injection="""    reference = read_reference(wait=wait)
    os.environ['MLFLOW_TRACKING_TOKEN'] = reference['reference']
    os.environ['MLFLOW_OPENSHELL_REFERENCE_PATH'] = str(REFERENCE_PATH)
"""
    contents['start.py']=once(contents['start.py'],anchor,injection+anchor)
    asset=Path(__file__).resolve().parent.parent/'assets'
    for name in ('patch-auth.cjs','mlflow_reference.py','server_identity.py'):
        contents[name]=(asset/name).read_text()
    for name,content in contents.items():
        target=out/name
        if target.exists() and target.read_text()!=content:
            raise ValueError('Refusing to overwrite '+str(target))
    out.mkdir(parents=True,exist_ok=True)
    for name,content in contents.items():(out/name).write_text(content)
    return list(contents)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--baseline',required=True);p.add_argument('--out',required=True)
    a=p.parse_args();print('Prepared '+', '.join(prepare(a.baseline,a.out)))
