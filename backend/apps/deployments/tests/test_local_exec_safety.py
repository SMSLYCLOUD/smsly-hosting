# _LocalSSHClient was removed in the SSH->REST transfer refactor.
# All transfer operations now use the target node's REST API with
# HMAC V2 authentication. Local transfers use _node_api_request()
# with target_server_ip pointing to localhost.
# This file is intentionally empty — the safety guarantees previously
# tested here (shell=False, shlex.split) are enforced by the Docker
# SDK's exec_run with list-form commands in views_transfer.py.
