try:
    from tools.core.registry import register_tools
except ModuleNotFoundError as exc:
    _registry_import_error = exc

    def register_tools(*args, **kwargs):
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
    "fallback_search",
    "register_tools",
    "tavily_search",
]
