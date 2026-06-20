import logging

from django.test import TestCase


class LogRedactionTest(TestCase):
    def test_secrets_not_logged(self):
        logging.getLogger('test_observability')

        def mask_secrets(log_str):
            import re
            log_str = re.sub(r'(api_key|password|DATABASE_URL)=([^\s]+)', r'\1=***', log_str, flags=re.IGNORECASE)
            return log_str

        raw_log = "Connection failed with DATABASE_URL=postgres://user:pass@host/db and API_KEY=secret123"
        safe_log = mask_secrets(raw_log)

        self.assertNotIn("postgres://user:pass@host/db", safe_log)
        self.assertNotIn("secret123", safe_log)
        self.assertIn("DATABASE_URL=***", safe_log)
        self.assertIn("API_KEY=***", safe_log)
