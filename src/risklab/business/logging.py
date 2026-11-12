"""Enable only the application's JSON stream; do not change ML/root logger levels."""

import logging


def configure_logging() -> None:
    logger = logging.getLogger("risklab.business")
    logger.setLevel(logging.INFO)
    if not any(handler.name == "risklab-json" for handler in logger.handlers):
        handler = logging.StreamHandler()
        handler.set_name("risklab-json")
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
