"""Central logging. Quiet by default (WARNING) so CLI output stays clean; set TRADEOS_LOG=INFO
(or DEBUG) to see ingestion/agent diagnostics. User-facing CLI output stays on print(); this is
for diagnostics, swallowed-error reporting, and observability."""

import logging
import os

_CONFIGURED = False


def get_logger(name: str = "tradeos") -> logging.Logger:
    global _CONFIGURED
    logger = logging.getLogger(name)
    if not _CONFIGURED:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s | %(message)s", "%H:%M:%S"))
        root = logging.getLogger("tradeos")
        root.addHandler(handler)
        root.setLevel(os.getenv("TRADEOS_LOG", "WARNING").upper())
        root.propagate = False
        _CONFIGURED = True
    return logger
