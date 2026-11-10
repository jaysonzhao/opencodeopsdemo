"""Canonical foreground OpenCode server; provider values remain outside the sandbox."""
import json
import os
from pathlib import Path
import time

ROOT = Path('/sandbox')
CONFIG = ROOT/'.opencode-server'


def configure(wait=False):
    os.umask(0o077)
    deadline = time.monotonic()+(600 if wait else 0)
    while not all((CONFIG/name).is_file() for name in ('runtime.json','server-auth.json')):
        if time.monotonic() >= deadline:
            raise RuntimeError('Stage runtime and server login through server_credentials.py stage')
        time.sleep(2)
    c = json.loads((CONFIG/'runtime.json').read_text())
    auth = json.loads((CONFIG/'server-auth.json').read_text())
    for key in ('MODEL_API_KEY','MLFLOW_TRACKING_TOKEN'):
        if not os.environ.get(key, '').startswith('openshell:'):
            raise RuntimeError('Expected supervisor-issued opaque credential for '+key)
    if not auth.get('OPENCODE_SERVER_PASSWORD') or ':' in auth.get('OPENCODE_SERVER_USERNAME',''):
        raise RuntimeError('Invalid server login')
    for key in ('MLFLOW_TRACKING_AUTH','MLFLOW_TOKEN_PATH','MLFLOW_TRACKING_USERNAME',
                'MLFLOW_TRACKING_PASSWORD','MLFLOW_TRACKING_INSECURE_TLS'):
        os.environ.pop(key, None)
    # Keep OpenShell's injected TLS proxy trust. The supervisor trusts the destination CA.
    os.environ.update(MLFLOW_TRACKING_URI=c['mlflow_uri'].rstrip('/'),
        MLFLOW_WORKSPACE=c['mlflow_workspace'], MLFLOW_EXPERIMENT_NAME=c['mlflow_experiment'],
        MLFLOW_ENABLE_PROXY_MULTIPART_UPLOAD='false',
        HOME=str(ROOT), XDG_CONFIG_HOME=str(ROOT/'.config'), XDG_DATA_HOME=str(ROOT/'.local/share'),
        XDG_STATE_HOME=str(ROOT/'.local/state'), XDG_CACHE_HOME=str(ROOT/'.cache'),
        TMPDIR='/sandbox/.tmp', SQLITE_TMPDIR='/sandbox/.tmp', OPENCODE_DISABLE_MODELS_FETCH='true')
    os.environ.update({k:auth[k] for k in ('OPENCODE_SERVER_USERNAME','OPENCODE_SERVER_PASSWORD')})
    for key in ('XDG_CONFIG_HOME','XDG_DATA_HOME','XDG_STATE_HOME','XDG_CACHE_HOME','TMPDIR'):
        Path(os.environ[key]).mkdir(parents=True, exist_ok=True)
    model = c['model_id']
    settings = {'$schema':'https://opencode.ai/config.json', 'autoupdate':False,
        'enabled_providers':['configured'], 'model':'configured/'+model, 'small_model':'configured/'+model,
        'provider':{'configured':{'npm':'@ai-sdk/openai-compatible', 'name':'Configured API',
            'options':{'baseURL':c['model_base_url'].rstrip('/'), 'apiKey':os.environ['MODEL_API_KEY']},
            'models':{model:{'name':model,'limit':{'context':c['model_context'],'output':c['model_output']}}}}},
        'plugin':['file:///opt/mlflow-ts/integrations/opencode']}
    os.environ['OPENCODE_CONFIG_CONTENT'] = json.dumps(settings)
    return c


def main():
    configure(wait=True)
    workspace = ROOT/'workspace'
    workspace.mkdir(parents=True, exist_ok=True)
    os.chdir(workspace)
    import mlflow
    experiment = mlflow.set_experiment(os.environ['MLFLOW_EXPERIMENT_NAME'])
    os.environ['MLFLOW_EXPERIMENT_ID'] = str(experiment.experiment_id)
    (CONFIG/'experiment-id').write_text(str(experiment.experiment_id))
    print('MLflow experiment ready; starting authenticated OpenCode on loopback:4096', flush=True)
    os.execv('/opt/opencode/bin/opencode', ['opencode','serve','--hostname','127.0.0.1','--port','4096'])


if __name__ == '__main__':
    main()
