"""
Shared fixtures for mocking Selenium interactions so scraper tests never touch a real
browser or network. FakeDriver/FakeElement provide just enough of the Selenium WebDriver
surface (find_element/find_elements/get/quit) that Fw3Scraper's extraction methods work
unmodified against them.
"""

import pytest
from selenium.common.exceptions import NoSuchElementException, TimeoutException


class FakeElement:
    def __init__(self, text="", attrs=None):
        self.text = text
        self._attrs = attrs or {}

    def get_attribute(self, name):
        return self._attrs.get(name)


class FakeDriver:
    """Minimal stand-in for webdriver.Chrome, keyed by (By, selector) like the real find_*."""

    def __init__(self):
        self.find_element_map: dict = {}
        self.find_elements_map: dict = {}
        self.visited_urls: list = []
        self.quit_called = False

    def get(self, url):
        self.visited_urls.append(url)

    def find_element(self, by, value):
        key = (by, value)
        if key not in self.find_element_map:
            raise NoSuchElementException(f"no element configured for {key}")
        result = self.find_element_map[key]
        if isinstance(result, Exception):
            raise result
        return result

    def find_elements(self, by, value):
        return self.find_elements_map.get((by, value), [])

    def quit(self):
        self.quit_called = True


class DummyWebDriverWait:
    """
    Replaces selenium's WebDriverWait so tests don't pay real polling delays. Evaluates
    the condition once against the fake driver: NoSuchElementException (the condition's
    own miss signal) is translated into TimeoutException, matching the real contract
    closely enough for code that only distinguishes "found" from "timed out".
    """

    def __init__(self, driver, timeout):
        self.driver = driver

    def until(self, method):
        try:
            result = method(self.driver)
        except NoSuchElementException as exc:
            raise TimeoutException(str(exc)) from exc
        if not result:
            raise TimeoutException("condition not met")
        return result


@pytest.fixture(autouse=True)
def patch_selenium_waits(monkeypatch):
    """
    Applied to every test in this package: swaps in DummyWebDriverWait and no-ops
    time.sleep inside fw3_jobscraper, so scraper methods run instantly and
    deterministically against FakeDriver instead of a real browser/clock.
    """
    monkeypatch.setattr("scrapers.fw3_jobscraper.WebDriverWait", DummyWebDriverWait)
    monkeypatch.setattr("scrapers.fw3_jobscraper.time.sleep", lambda seconds: None)
