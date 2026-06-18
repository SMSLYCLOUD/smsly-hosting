# This test covered _scrub_ssh_key_paths which was removed during the
# SSH → REST refactor. The transfer service no longer stores or exposes
# SSH key paths, making this test obsolete.
from django.test import SimpleTestCase


class Finding106SshKeyPathScrubTests(SimpleTestCase):
    def test_scrub_function_removed(self):
        pass
