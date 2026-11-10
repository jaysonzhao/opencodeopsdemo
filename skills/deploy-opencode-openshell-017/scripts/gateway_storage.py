#!/usr/bin/env python3
"""Helm post-renderer: set block storage only on the fresh owned gateway claim."""
import os
import sys
import yaml

documents=list(yaml.safe_load_all(sys.stdin))
matches=0
for document in documents:
    if not document:continue
    if document.get('kind')=='StatefulSet' and document.get('metadata',{}).get('name')==os.environ['RELEASE']:
        if document['metadata'].get('namespace',os.environ['NS'])!=os.environ['NS']:
            raise SystemExit('Gateway namespace mismatch')
        claims=document['spec']['volumeClaimTemplates']
        if len(claims)!=1 or claims[0]['metadata']['name']!='openshell-data':
            raise SystemExit('Unexpected gateway chart claim layout')
        claims[0]['spec']['storageClassName']=os.environ['STORAGE_CLASS']
        matches+=1
if matches!=1:raise SystemExit('Expected exactly one fresh gateway StatefulSet')
yaml.safe_dump_all(documents,sys.stdout,sort_keys=False)
