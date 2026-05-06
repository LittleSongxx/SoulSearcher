try:
    from tools.code.code_executor import execute_python_code
except ModuleNotFoundError as exc:
    _code_executor_import_error = exc

    def execute_python_code(*args, **kwargs):
        raise _code_executor_import_error


try:
    from tools.core.registry import get_registered_tools, set_registered_tools
except ModuleNotFoundError as exc:
    _registry_import_error = exc

    def get_registered_tools(*args, **kwargs):
        raise _registry_import_error

    def set_registered_tools(*args, **kwargs):
        raise _registry_import_error


try:
    from tools.crawl.crawler import crawl_url, crawl_urls
except ModuleNotFoundError as exc:
    _crawler_import_error = exc

    def crawl_url(*args, **kwargs):
        raise _crawler_import_error

    def crawl_urls(*args, **kwargs):
        raise _crawler_import_error


try:
    from tools.search.fallback_search import fallback_search
    from tools.search.search import tavily_search
except ModuleNotFoundError as exc:
    _search_import_error = exc

    def fallback_search(*args, **kwargs):
        raise _search_import_error

    def tavily_search(*args, **kwargs):
        raise _search_import_error


__all__ = [
    "crawl_url",
    "crawl_urls",
    "execute_python_code",
    "fallback_search",
    "get_registered_tools",
    "set_registered_tools",
    "tavily_search",
]
