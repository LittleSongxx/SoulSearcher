try:  # Optional langchain extras may be absent in unit-test envs.
    from .search import *  # noqa: F401,F403
except Exception:
    pass

__all__ = []
