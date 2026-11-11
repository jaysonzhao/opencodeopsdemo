import argparse, base64, fcntl, hashlib, json, os, re, socket, ssl, subprocess, sys, time
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler, HTTPSHandler

KEY = 'MLFLOW_TRACKING_TOKEN'
STAGE = 'startup'
def require(ok, message):
    if not ok: raise RuntimeError(message)
def run(args, extra=None, data=None):
    result = subprocess.run(args, input=data, text=True, capture_output=True,
        timeout=120, env={**os.environ, **(extra or {})})
    require(result.returncode == 0, 'command failed')
    return result.stdout
def oc(*args, **kw): return run(['oc', '-n', E['NS'], *args], **kw)
def shell(*args, **kw):
    extra = {'XDG_CONFIG_HOME': E['OPENSHELL_CONFIG_HOME'], **kw.pop('extra', {})}
    return run([E['OPENSHELL_BIN'], '--gateway', E['GATEWAY'],
        '--workspace', E['WORKSPACE'], *args], extra=extra, **kw)
def digest(x): return hashlib.sha256(json.dumps(x, sort_keys=True).encode()).hexdigest()
def save(state):
    tmp = ROOT/'state.tmp'; tmp.write_text(json.dumps(state, indent=2)+'\n')
    os.replace(tmp, ROOT/'state.json')
def provider():
    doc = json.loads(shell('provider', 'list', '-o', 'json'))
    matches = [x for x in doc.get('providers', []) if x.get('name') == E['TRACE_PROVIDER']]
    require(len(matches) == 1, 'expected exactly one MLflow provider')
    return matches[0]
def binding(p): return {k: p.get(k) for k in ('id', 'name', 'workspace', 'type')}
def status(wait=False):
    args = ['sandbox', 'provider', 'status', E['SANDBOX'], E['TRACE_PROVIDER'], '-o', 'json']
    if wait: args += ['--wait', '--timeout', '60']
    return json.loads(shell(*args))
def instance(s):
    targets = s.get('targets', []); require(len(targets) == 1, 'expected one sandbox target')
    t = targets[0]; desired = t.get('receipt', {}).get('desired', {})
    observed = t.get('observed', {})
    result = {'sandbox_id': desired.get('sandbox_id'),
              'session_id': observed.get('session_id'),
              'process_instance_id': observed.get('process_instance_id')}
    require(all(result.values()), 'cannot identify existing sandbox instance')
    return result
def profile(p):
    doc = json.loads(shell('profile', 'export', p['type'], '-o', 'json'))
    creds = [c for c in doc.get('credentials', []) if KEY in c.get('env_vars', [])]
    require(len(creds) == 1 and creds[0].get('stable_placeholder') is True,
            'native stable placeholder required')
    require(not creds[0].get('refresh') and not creds[0].get('token_grant'),
            'conflicting refresh configuration')
    u = urlsplit(C['mlflow_uri'])
    require(u.scheme == 'https' and u.hostname and not u.username and not u.query
            and not u.fragment, 'existing verified HTTPS tracking URI required')
    expected = (u.hostname, u.port or 443, u.path.rstrip('/')+'/api/**')
    endpoints = doc.get('endpoints', [])
    require(any((x.get('host'), x.get('port'), x.get('path')) == expected
                for x in endpoints), 'runtime/profile endpoint mismatch')
    require(KEY in p.get('credential_keys', []), 'credential key missing')
    return digest(doc)
def claims(token, uid, static):
    parts = token.split('.'); require(len(parts) == 3, 'expected JWT')
    c = json.loads(base64.urlsafe_b64decode(parts[1]+'='*(-len(parts[1])%4)))
    require(c.get('sub') == 'system:serviceaccount:'+E['NS']+':'+E['TRACER'], 'wrong subject')
    if static:
        require('exp' not in c, 'candidate still expires')
        require(c.get('kubernetes.io/serviceaccount/service-account.uid') == uid,
                'wrong service account UID')
        require(c.get('kubernetes.io/serviceaccount/secret.name') == E['STATIC_SECRET'],
                'wrong token Secret')
        return 0
    exp = c.get('exp')
    require(type(exp) is int and exp > time.time()+600, 'insufficient rollback lifetime')
    return exp*1000
class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl): return None
def probe(token):
    ctx = ssl.create_default_context(cafile=os.environ.get('MLFLOW_CA_BUNDLE') or None)
    opener = build_opener(NoRedirect(), HTTPSHandler(context=ctx))
    tracking_host = urlsplit(C['mlflow_uri']).hostname
    probe_host = os.environ.get('MLFLOW_PROBE_HOST')
    original_getaddrinfo = socket.getaddrinfo
    if probe_host:
        def resolve_probe_host(host, port, *args, **kwargs):
            if host == tracking_host:
                host = probe_host
            return original_getaddrinfo(host, port, *args, **kwargs)
        socket.getaddrinfo = resolve_probe_host
    url = C['mlflow_uri'].rstrip('/')+'/api/2.0/mlflow/experiments/get-by-name?'+urlencode(
        {'experiment_name': C['mlflow_experiment']})
    req = Request(url, headers={'Authorization': 'Bearer '+token,
        'X-MLFLOW-WORKSPACE': C['mlflow_workspace'], 'Accept': 'application/json'})
    try:
        with opener.open(req, timeout=20) as response:
            require(response.status == 200, 'probe failed')
            doc = json.load(response)
    finally:
        if probe_host:
            socket.getaddrinfo = original_getaddrinfo
    x = doc.get('experiment', {})
    require(x.get('name') == C['mlflow_experiment'] and x.get('experiment_id'),
            'expected experiment not returned')
    return str(x['experiment_id'])
def candidate(create=False):
    sa = json.loads(oc('get', 'sa', E['TRACER'], '-o', 'json'))
    require(E['STATIC_SECRET'] not in [x.get('name') for x in sa.get('secrets', [])],
            'Secret is referenced by ServiceAccount.secrets; use a new manual Secret')
    text = oc('get', 'secret', E['STATIC_SECRET'], '--ignore-not-found', '-o', 'json')
    created = False
    if not text:
        require(create, 'candidate Secret missing')
        doc = {'apiVersion': 'v1', 'kind': 'Secret', 'type': 'kubernetes.io/service-account-token',
            'metadata': {'namespace': E['NS'], 'name': E['STATIC_SECRET'],
              'annotations': {'kubernetes.io/service-account.name': E['TRACER']},
              'labels': {'app.kubernetes.io/managed-by': 'mlflow-static-token-skill'}}}
        oc('create', '-f', '-', data=json.dumps(doc)); created = True
    for attempt in range(30):
        doc = json.loads(oc('get', 'secret', E['STATIC_SECRET'], '-o', 'json'))
        require(doc.get('type') == 'kubernetes.io/service-account-token', 'wrong Secret type')
        a = doc['metadata'].get('annotations', {})
        require(a.get('kubernetes.io/service-account.name') == E['TRACER'], 'wrong Secret owner')
        encoded = doc.get('data', {}).get('token')
        if encoded:
            require(a.get('kubernetes.io/service-account.uid') == sa['metadata']['uid'],
                    'Secret association UID mismatch')
            token = base64.b64decode(encoded).decode().strip()
            claims(token, sa['metadata']['uid'], True)
            return token, doc['metadata']['uid'], created
        time.sleep(2)
    raise RuntimeError('controller did not populate token within one minute')
def update(token, expiry):
    shell('provider', 'update', E['TRACE_PROVIDER'], '--credential', KEY,
        '--credential-expires-at', KEY+'='+str(expiry), '--wait', '--timeout', '60',
        '-o', 'json', extra={KEY: token})
def verify(state, expiry):
    p = provider(); require(binding(p) == state['provider'], 'provider identity changed')
    require(profile(p) == state['profile_sha256'], 'profile changed')
    observed_expiry = p.get('credential_expires_at_ms', {}).get(KEY)
    require(observed_expiry in (None, 0) if expiry == 0 else observed_expiry == expiry,
            'provider expiry not as expected')
    s = status(True); require(instance(s) == state['instance'], 'sandbox instance changed')
    observed = s['targets'][0].get('observed', {})
    require(observed.get('credentials_installed') is True
            and observed.get('reason') != 'credential_expired', 'credential not active')
def main():
    global E, C, ROOT, STAGE
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['prepare', 'activate', 'verify', 'rollback'])
    parser.add_argument('--renewers-stopped', action='store_true')
    args = parser.parse_args(); os.umask(0o077)
    keys = ['NS', 'GATEWAY', 'WORKSPACE', 'SANDBOX', 'TRACE_PROVIDER', 'TRACER',
            'STATIC_SECRET', 'OPENSHELL_BIN', 'OPENSHELL_CONFIG_HOME', 'RUNTIME', 'MIGRATION_DIR']
    E = {k: os.environ[k] for k in keys}; ROOT = Path(E['MIGRATION_DIR'])
    C = json.loads(Path(E['RUNTIME']).read_text())
    require(C['namespace'] == E['NS'], 'runtime namespace mismatch')
    require(re.search(r'\b0\.1\.7\b', run([E['OPENSHELL_BIN'], '--version'])), 'CLI version mismatch')
    context = {**E, 'cluster': run(['oc', 'whoami', '--show-server']).strip(), 'runtime_sha256': digest(C)}
    STAGE = 'preflight'; p = provider(); ph = profile(p)
    require(p.get('workspace') == E['WORKSPACE'] and p.get('id'), 'wrong provider workspace')
    if args.action == 'prepare':
        require(not (ROOT/'state.json').exists(), 'use a new migration directory')
        before = instance(status()); STAGE = 'candidate_secret'
        token, uid, created = candidate(True); STAGE = 'candidate_probe'
        exp_id = probe(token)
        state = {'context': context, 'provider': binding(p), 'profile_sha256': ph,
            'instance': before, 'old_expiry_ms': p.get('credential_expires_at_ms', {}).get(KEY),
            'secret_uid': uid, 'secret_created': created, 'experiment_id': exp_id, 'phase': 'prepared'}
        save(state); print(json.dumps({'event': 'candidate_verified', 'secret_created': created,
            'experiment_id': exp_id, 'token_has_exp': False})); return
    state = json.loads((ROOT/'state.json').read_text())
    require(state['context'] == context and state['provider'] == binding(p)
            and state['profile_sha256'] == ph, 'deployment context changed')
    if args.action == 'verify':
        STAGE = 'provider_verification'; verify(state, state['expected_expiry_ms'])
        print(json.dumps({'event': 'provider_verified', 'trace_delivery_verified': False})); return
    require(args.renewers_stopped, 'stop every renewal writer first')
    ident = '|'.join(E[k] for k in ('GATEWAY', 'WORKSPACE', 'SANDBOX', 'TRACE_PROVIDER'))
    lock = Path(E['OPENSHELL_CONFIG_HOME'])/('trace-renew-'+hashlib.sha256(ident.encode()).hexdigest()[:16]+'.lock')
    fd = os.open(lock, os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW, 0o600)
    try:
        STAGE = 'writer_lock'; fcntl.flock(fd, fcntl.LOCK_EX|fcntl.LOCK_NB)
        require(instance(status()) == state['instance'], 'sandbox instance changed')
        if args.action == 'activate':
            STAGE = 'candidate_recheck'; token, uid, _ = candidate()
            require(uid == state['secret_uid'], 'candidate Secret was replaced')
            probe(token); expiry = 0
            state['old_expiry_ms'] = provider().get('credential_expires_at_ms', {}).get(KEY)
        else:
            STAGE = 'rollback_token'; token = oc('create', 'token', E['TRACER'], '--duration=2h').strip()
            expiry = claims(token, None, False); probe(token)
        state.update(phase=args.action+'_pending', expected_expiry_ms=expiry); save(state)
        STAGE = 'provider_update'; update(token, expiry)
        STAGE = 'provider_verification'; verify(state, expiry)
        state['phase'] = 'static_active' if expiry == 0 else 'short_lived_active'; save(state)
        print(json.dumps({'event': state['phase'], 'sandbox_instance_unchanged': True,
            'trace_delivery_verified': False, 'credential_expiry_ms': expiry or None}))
    finally: os.close(fd)
if __name__ == '__main__':
    try: main()
    except Exception as exc:
        print(json.dumps({'event': 'migration_failed', 'stage': STAGE,
            'error_type': type(exc).__name__, 'http_status': getattr(exc, 'code', None)}))
        sys.exit(1)
