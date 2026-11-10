"""OpenCode canonical process inside OpenShell; secrets arrive as provider handles."""
import json
import os
from pathlib import Path
import subprocess
import time

ROOT = Path('/sandbox')
CONFIG = ROOT / '.opencode-setup'


def configure(wait=False):
    os.umask(0o077)
    deadline = time.monotonic() + (600 if wait else 0)
    while not all((CONFIG / name).is_file() for name in ('runtime.json', 'server-auth.json')):
        if time.monotonic() >= deadline:
            raise RuntimeError('Start the retained sandbox and immediately stage runtime.json and server-auth.json with credentials.py stage')
        time.sleep(2)
    config = json.loads((CONFIG / 'runtime.json').read_text())
    auth = json.loads((CONFIG / 'server-auth.json').read_text())
    required = ['MINIMAX_API_KEY', 'MLFLOW_TRACKING_TOKEN']
    if not all(os.environ.get(k) for k in required):
        raise RuntimeError('Attach both OpenShell providers before starting the server')
    for key in required:
        if not os.environ[key].startswith('openshell:'):
            raise RuntimeError('Expected OpenShell credential placeholder for '+key)
    if not auth.get('OPENCODE_SERVER_PASSWORD') or ':' in auth.get('OPENCODE_SERVER_USERNAME', ''):
        raise RuntimeError('Invalid server login settings')
    if config['BASE_URL'] != 'https://api.minimax.io/anthropic/v1':
        raise RuntimeError('This runtime is pinned to MiniMax Global Anthropic-compatible API')
    context, output = int(config['MODEL_CONTEXT']), int(config['MODEL_OUTPUT'])
    if not 0 < output < context:
        raise RuntimeError('Require 0 < MODEL_OUTPUT < MODEL_CONTEXT')
    for key in ('MLFLOW_TRACKING_AUTH', 'MLFLOW_TOKEN_PATH', 'MLFLOW_TRACKING_USERNAME',
                'MLFLOW_TRACKING_PASSWORD', 'MLFLOW_TRACKING_INSECURE_TLS'):
        os.environ.pop(key, None)
    # Preserve OpenShell's injected CA trust variables. They authenticate its TLS proxy.
    for key in ('MLFLOW_TRACKING_URI', 'MLFLOW_WORKSPACE', 'MLFLOW_EXPERIMENT_NAME'):
        os.environ[key] = str(config[key])
    os.environ.update({k:auth[k] for k in ('OPENCODE_SERVER_USERNAME', 'OPENCODE_SERVER_PASSWORD')})
    os.environ.update(HOME=str(ROOT), XDG_CONFIG_HOME=str(ROOT/'.config'),
                      XDG_DATA_HOME=str(ROOT/'.local/share'), XDG_STATE_HOME=str(ROOT/'.local/state'),
                      XDG_CACHE_HOME=str(ROOT/'.cache'), OPENCODE_DISABLE_MODELS_FETCH='true')
    for name in ('XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_STATE_HOME', 'XDG_CACHE_HOME'):
        Path(os.environ[name]).mkdir(parents=True, exist_ok=True)
    model = config['MODEL_NAME']
    settings = {'$schema':'https://opencode.ai/config.json', 'autoupdate':False,
        'enabled_providers':['minimax-global'], 'model':'minimax-global/'+model,
        'small_model':'minimax-global/'+model,
        'provider':{'minimax-global':{'npm':'@ai-sdk/anthropic','name':'MiniMax Global',
            'options':{'baseURL':config['BASE_URL'], 'apiKey':os.environ['MINIMAX_API_KEY']},
            'models':{model:{'name':model,'limit':{'context':context,'output':output}}}}},
        'plugin':['file:///opt/mlflow-ts/integrations/opencode']}
    os.environ['OPENCODE_CONFIG_CONTENT'] = json.dumps(settings)
    return config


def main():
    configure(wait=True)
    workspace = ROOT/'workspace'
    workspace.mkdir(parents=True, exist_ok=True)
    os.chdir(workspace)
    import mlflow
    experiment = mlflow.set_experiment(os.environ['MLFLOW_EXPERIMENT_NAME'])
    os.environ['MLFLOW_EXPERIMENT_ID'] = str(experiment.experiment_id)
    (CONFIG/'experiment-id').write_text(str(experiment.experiment_id))
    print('MLflow experiment ready; starting OpenCode on sandbox loopback:4096', flush=True)
    os.execv('/opt/opencode/bin/opencode', ['opencode','serve','--hostname','127.0.0.1','--port','4096'])


if __name__ == '__main__':
    main()
