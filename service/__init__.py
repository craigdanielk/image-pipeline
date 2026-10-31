"""Creative Studio hosted service (CS-B1 platform foundation).

Hosted entrypoints (api/index.py, api/worker.py) import only this package.
Nothing here reads a local jobs_root, tenant registry or filesystem selector;
the local operator server in ui/ remains a separate compatibility entrypoint.
"""
