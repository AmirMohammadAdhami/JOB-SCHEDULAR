"""
Handler registry — maps handler names to Python callables.

The handler field on a Job is never executed directly from user input.
It maps to a pre-registered Python function. Only developers can add
new handlers by updating this registry in code.
"""
import logging
from typing import Any, Callable

logger = logging.getLogger("apps.jobs.handlers")

# The registry: handler_name -> callable
HANDLER_REGISTRY: dict[str, Callable[..., Any]] = {}


def register_handler(name: str):
    """Decorator to register a handler function."""

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        HANDLER_REGISTRY[name] = func
        logger.debug(f"Registered handler: {name}")
        return func

    return decorator


def get_handler(name: str) -> Callable[..., Any]:
    """Look up a handler by name. Raises KeyError if not found."""
    if name not in HANDLER_REGISTRY:
        raise KeyError(
            f"Handler '{name}' is not registered. "
            f"Available handlers: {list(HANDLER_REGISTRY.keys())}"
        )
    return HANDLER_REGISTRY[name]


def get_handler_names() -> list[str]:
    """Return all registered handler names."""
    return list(HANDLER_REGISTRY.keys())


# ---------------------------------------------------------------------------
# Example handlers (replace with real job implementations)
# ---------------------------------------------------------------------------


@register_handler("billing_reconciliation")
def billing_reconciliation(**kwargs):
    logger.info("Running billing_reconciliation handler")
    return {"status": "success"}


@register_handler("generate_daily_report")
def generate_daily_report(**kwargs):
    logger.info("Running generate_daily_report handler")
    return {"status": "success"}


@register_handler("sync_salesforce_data")
def sync_salesforce_data(**kwargs):
    logger.info("Running sync_salesforce_data handler")
    return {"status": "success"}


@register_handler("cleanup_temp_files")
def cleanup_temp_files(**kwargs):
    logger.info("Running cleanup_temp_files handler")
    return {"status": "success"}
