"""End-to-end L1-L7 & R4 security verification suite (executed via pytest)."""


def load_tests(loader, tests, pattern):
    """Prevent stdlib unittest discovery from loading pytest-fixture modules without pytest."""
    return loader.suiteClass()
