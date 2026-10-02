# -*- coding: utf-8 -*-
"""Small no-key web search helper for short in-game answers.
Ported from sky-companion project."""
import html
import re
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urlparse

import requests


SEARCH_URL = "https://html.duckduckgo.com/html/"
BING_URL = "https://www.bing.com/search"
CN_BING_URL = "https://cn.bing.com/search"
SOGOU_URL = "https://www.sogou.com/web"
SO_URL = "https://www.so.com/s"
BAIDU_URL = "http://www.baidu.com/s"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def _clean_text(text):
    return re.sub(r"\s+", " ", html.unescape(str(text or ""))).strip()


def _strip_tags(fragment):
    fragment = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", str(fragment or ""))
    fragment = re.sub(r"(?is)<[^>]+>", " ", fragment)
    return _clean_text(fragment)


def _query_terms(query):
    query = _clean_text(query)
    cjk_terms = [t for t in re.findall(r"[\u4e00-\u9fff]{2,}", query) if t not in ("今天", "今日", "最新", "现在", "一下", "什么", "怎么")]
    latin_terms = [t.lower() for t in re.findall(r"[A-Za-z0-9]{3,}", query)]
    terms = cjk_terms + latin_terms
    if not terms and re.search(r"[\u4e00-\u9fff]", query):
        terms = list(re.findall(r"[\u4e00-\u9fff]", query))
    return terms[:8]


def _relevant_to_query(query, item):
    terms = _query_terms(query)
    if not terms:
        return True
    hay = _clean_text((item.get("title", "") or "") + " " + (item.get("snippet", "") or "")).lower()
    if any(term.lower() in hay for term in terms):
        return True
    query_chars = set(re.findall(r"[\u4e00-\u9fff]", query or ""))
    hay_chars = set(re.findall(r"[\u4e00-\u9fff]", hay or ""))
    if query_chars and len(query_chars & hay_chars) >= max(2, min(4, len(query_chars))):
        return True
    return False


def _clean_duck_url(href):
    href = html.unescape(str(href or "")).strip()
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    qs = parse_qs(parsed.query)
    if "uddg" in qs and qs["uddg"]:
        return unquote(qs["uddg"][0])
    return href


class _DuckParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.results = []
        self.current = None
        self.in_title = False
        self.in_snippet = False
        self.snippet_depth = 0

    def _finish_current(self):
        if not self.current:
            return
        title = _clean_text(self.current.get("title"))
        snippet = _clean_text(self.current.get("snippet"))
        url = self.current.get("url", "")
        if title and (snippet or url):
            self.results.append({"title": title, "snippet": snippet, "url": url})
        self.current = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        cls = attrs.get("class", "")
        if tag == "a" and "result__a" in cls:
            self._finish_current()
            self.current = {"title": "", "snippet": "", "url": _clean_duck_url(attrs.get("href", ""))}
            self.in_title = True
            return
        if self.current and "result__snippet" in cls:
            self.in_snippet = True
            self.snippet_depth = 1
            return
        if self.in_snippet:
            self.snippet_depth += 1

    def handle_data(self, data):
        if not self.current:
            return
        if self.in_title:
            self.current["title"] += data
        elif self.in_snippet:
            self.current["snippet"] += data

    def handle_endtag(self, tag):
        if self.in_title and tag == "a":
            self.in_title = False
        if self.in_snippet:
            self.snippet_depth -= 1
            if self.snippet_depth <= 0:
                self.in_snippet = False
                self.snippet_depth = 0

    def close(self):
        super().close()
        self._finish_current()


class _BingParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.results = []
        self.current = None
        self.result_depth = 0
        self.in_h2 = False
        self.in_title = False
        self.in_snippet = False
        self.snippet_depth = 0

    def _finish_current(self):
        if not self.current:
            return
        title = _clean_text(self.current.get("title"))
        snippet = _clean_text(self.current.get("snippet"))
        url = self.current.get("url", "")
        if title and (snippet or url):
            self.results.append({"title": title, "snippet": snippet, "url": url})
        self.current = None
        self.result_depth = 0
        self.in_h2 = False
        self.in_title = False
        self.in_snippet = False
        self.snippet_depth = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        cls = attrs.get("class", "")
        if tag == "li" and "b_algo" in cls:
            self._finish_current()
            self.current = {"title": "", "snippet": "", "url": ""}
            self.result_depth = 1
            return
        if not self.current:
            return
        self.result_depth += 1
        if tag == "h2":
            self.in_h2 = True
        elif self.in_h2 and tag == "a" and not self.current.get("url"):
            self.current["url"] = attrs.get("href", "")
            self.in_title = True
        elif tag == "p" and not self.current.get("snippet"):
            self.in_snippet = True
            self.snippet_depth = 1
            return
        if self.in_snippet:
            self.snippet_depth += 1

    def handle_data(self, data):
        if not self.current:
            return
        if self.in_title:
            self.current["title"] += data
        elif self.in_snippet:
            self.current["snippet"] += data

    def handle_endtag(self, tag):
        if not self.current:
            return
        if self.in_title and tag == "a":
            self.in_title = False
        if self.in_h2 and tag == "h2":
            self.in_h2 = False
        if self.in_snippet:
            self.snippet_depth -= 1
            if self.snippet_depth <= 0:
                self.in_snippet = False
                self.snippet_depth = 0
        self.result_depth -= 1
        if self.result_depth <= 0:
            self._finish_current()

    def close(self):
        super().close()
        self._finish_current()


def _dedupe_results(items, max_results, query=""):
    seen = set()
    results = []
    for item in items:
        title = _clean_text(item.get("title"))
        snippet = _clean_text(item.get("snippet"))
        url = item.get("url", "")
        key = (title.lower(), url)
        if not title or key in seen:
            continue
        candidate = {"title": title[:100], "snippet": snippet[:220], "url": url[:220]}
        if query and not _relevant_to_query(query, candidate):
            continue
        seen.add(key)
        results.append(candidate)
        if len(results) >= max_results:
            break
    return results


def _search_duckduckgo(query, max_results, timeout):
    response = requests.get(
        SEARCH_URL, params={"q": query},
        headers={"User-Agent": USER_AGENT}, timeout=timeout,
    )
    if response.status_code != 200:
        return []
    parser = _DuckParser()
    parser.feed(response.text)
    parser.close()
    return _dedupe_results(parser.results, max_results, query)


def _search_bing(query, max_results, timeout):
    response = requests.get(
        BING_URL, params={"q": query},
        headers={"User-Agent": USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9"},
        timeout=timeout,
    )
    response.raise_for_status()
    parser = _BingParser()
    parser.feed(response.text)
    parser.close()
    return _dedupe_results(parser.results, max_results, query)


def _search_cn_bing(query, max_results, timeout):
    response = requests.get(
        CN_BING_URL, params={"q": query},
        headers={"User-Agent": USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9"},
        timeout=timeout,
    )
    response.raise_for_status()
    parser = _BingParser()
    parser.feed(response.text)
    parser.close()
    return _dedupe_results(parser.results, max_results, query)


def _search_baidu(query, max_results, timeout):
    response = requests.get(
        BAIDU_URL, params={"wd": query},
        headers={"User-Agent": USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9"},
        timeout=timeout,
    )
    response.raise_for_status()
    chunks = re.findall(r'(?is)<h3[^>]*>.*?</h3>.{0,1200}', response.text)
    items = []
    for chunk in chunks:
        title_m = re.search(r'(?is)<h3[^>]*>(.*?)</h3>', chunk)
        if not title_m:
            continue
        title = _strip_tags(title_m.group(1))
        snippet = _strip_tags(chunk).replace(title, "", 1)
        items.append({"title": title, "snippet": snippet, "url": ""})
    return _dedupe_results(items, max_results, query)


def _search_so(query, max_results, timeout):
    response = requests.get(
        SO_URL, params={"q": query},
        headers={"User-Agent": USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9"},
        timeout=timeout,
    )
    response.raise_for_status()
    chunks = re.findall(r'(?is)<h3[^>]*>.*?</h3>.{0,1200}', response.text)
    items = []
    for chunk in chunks:
        title_m = re.search(r'(?is)<h3[^>]*>(.*?)</h3>', chunk)
        if not title_m:
            continue
        title = _strip_tags(title_m.group(1))
        snippet = _strip_tags(chunk).replace(title, "", 1)
        items.append({"title": title, "snippet": snippet, "url": ""})
    return _dedupe_results(items, max_results, query)


def _has_cjk(text):
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


def search_web(query, max_results=3, timeout=5):
    """Return compact web results using no-key search pages. Never raises."""
    query = _clean_text(query)
    if not query:
        return []
    if _has_cjk(query):
        searchers = (_search_so, _search_baidu, _search_cn_bing, _search_duckduckgo, _search_bing)
    else:
        searchers = (_search_duckduckgo, _search_bing)
    for searcher in searchers:
        try:
            results = searcher(query, max_results, timeout)
            if results:
                return results
        except Exception:
            continue
    return []


def format_results(results):
    lines = []
    for i, item in enumerate(results or [], 1):
        title = item.get("title", "")
        snippet = item.get("snippet", "")
        if snippet:
            lines.append(f"{i}. {title}：{snippet}")
        else:
            lines.append(f"{i}. {title}")
    return "\n".join(lines)
