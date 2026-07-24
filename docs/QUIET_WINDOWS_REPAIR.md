# Echoes Cinema quiet-window repair

This branch permanently hides every long-lived child process used by the local control center, provider bootstrap bridge, model bootstrap, and real provider.

The repair preserves D-drive storage, logs, model caches, resumable P0 work, and the existing truthful status contract.

A Windows CI contract parses both PowerShell launchers and fails if a visible bootstrap invocation or a missing `-WindowStyle Hidden` launch returns.
