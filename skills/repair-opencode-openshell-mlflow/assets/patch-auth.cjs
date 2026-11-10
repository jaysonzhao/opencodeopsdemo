// Apply only to the audited MLflow v3.14.0 TypeScript source, before npm build.
const fs = require('node:fs');
const crypto = require('node:crypto');
const file = process.argv[2] || '/opt/mlflow-src/libs/typescript/core/src/auth/index.ts';
const source = fs.readFileSync(file, 'utf8');
const expected = '2bafc53962af0e0e4f574fdb782efad8f41550f85982399830084b76621d65ed';
if (crypto.createHash('sha256').update(source).digest('hex') !== expected) {
  throw new Error('Unexpected MLflow auth source; inspect version and existing patches');
}
const anchor = "    if (workspace) {\n      headers['X-MLFLOW-WORKSPACE'] = workspace;\n    }";
if (source.split(anchor).length !== 2) throw new Error('MLflow auth patch anchor changed');
const patch = `    // OPENSHELL_REFERENCE_RELOAD_V1: reread on every HTTP request; never cache a handle.
    const referencePath = process.env.MLFLOW_OPENSHELL_REFERENCE_PATH;
    if (referencePath !== '/sandbox/.opencode-setup/mlflow-reference.json') {
      throw new Error('Missing or unexpected OpenShell MLflow reference path');
    }
    const file = await fs.promises.open(referencePath, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW | fs.constants.O_NONBLOCK);
    let reference: { reference: string; expires_at: number };
    try {
      const info = await file.stat();
      if (!info.isFile() || info.size > 4096) throw new Error('Invalid MLflow reference file');
      reference = JSON.parse(await file.readFile('utf8'));
    } finally {
      await file.close();
    }
    if (!/^openshell:resolve:env:(?:v[0-9]+_)?MLFLOW_TRACKING_TOKEN$/.test(reference.reference)
        || !Number.isSafeInteger(reference.expires_at)
        || reference.expires_at * 1000 <= Date.now() + 10000) {
      throw new Error('Invalid or expired OpenShell MLflow reference; renew externally');
    }
    headers['Authorization'] = 'Bearer ' + reference.reference;
`;
fs.writeFileSync(file, source.replace(anchor, patch + anchor));
console.log('Applied per-request OpenShell reference reload to MLflow auth');
