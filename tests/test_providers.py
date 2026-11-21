from __future__ import annotations

import unittest

from ela.providers import PROVIDERS


class ProviderTests(unittest.TestCase):
    def test_ids_are_unique(self):
        ids = [provider.id for provider in PROVIDERS]
        self.assertEqual(len(ids), len(set(ids)))

    def test_local_providers_need_no_key(self):
        by_id = {provider.id: provider for provider in PROVIDERS}
        for name in ("ollama", "lmstudio", "llamacpp"):
            self.assertIn(name, by_id)
            self.assertFalse(by_id[name].needs_key)
            self.assertTrue(
                by_id[name].base_url.startswith("http://127.0.0.1")
            )

    def test_cloud_providers_are_https_and_need_keys(self):
        for provider in PROVIDERS:
            if provider.id in ("custom", "ollama", "lmstudio", "llamacpp"):
                continue
            self.assertTrue(provider.needs_key, provider.id)
            self.assertTrue(
                provider.base_url.startswith("https://"), provider.id
            )

    def test_custom_endpoint_is_last_and_blank(self):
        self.assertEqual(PROVIDERS[-1].id, "custom")
        self.assertEqual(PROVIDERS[-1].base_url, "")
        self.assertFalse(PROVIDERS[-1].needs_key)


if __name__ == "__main__":
    unittest.main()
