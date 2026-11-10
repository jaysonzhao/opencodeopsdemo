"""Run through OpenShell exec after the local client has received a marked reply."""
import sys
import time
from start import configure, CONFIG

configure()
import mlflow
experiment_id = (CONFIG/'experiment-id').read_text().strip()
marker = sys.argv[1]
for attempt in range(12):
    traces = mlflow.search_traces(experiment_ids=[experiment_id], max_results=50, return_type='list')
    found = [t for t in traces if marker in str(t.to_dict())]
    if found:
        print('Matching MLflow trace:', found[0].info.trace_id)
        break
    time.sleep(5)
else:
    raise SystemExit('No matching trace. Inspect exporter, policy, TLS, token expiry and MLflow compatibility.')
