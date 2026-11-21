from __future__ import annotations

import os
import random
import unittest
from unittest import mock

from ela.config import Config, apply_environment
from ela.retry import RetryPolicy, is_retryable, retry_call


class FakeAPIError(Exception):
    def __init__(self, message: str = "boom", status_code=None):
        super().__init__(message)
        self.status_code = status_code


class APIConnectionError(Exception):
    pass


class IsRetryableTests(unittest.TestCase):
    def test_rate_limit_status_is_retryable(self):
        self.assertTrue(is_retryable(FakeAPIError("slow down", 429)))

    def test_server_status_is_retryable(self):
        self.assertTrue(is_retryable(FakeAPIError("unavailable", 503)))
        self.assertTrue(is_retryable(FakeAPIError("gateway", 502)))
        self.assertTrue(is_retryable(FakeAPIError("timeout", 408)))

    def test_connection_error_class_name_is_retryable(self):
        self.assertTrue(is_retryable(APIConnectionError("socket closed")))

    def test_message_hints_are_retryable(self):
        self.assertTrue(is_retryable(RuntimeError("Rate Limit exceeded")))
        self.assertTrue(is_retryable(RuntimeError("server Overloaded, try later")))
        self.assertTrue(is_retryable(RuntimeError("Temporarily Unavailable")))
        self.assertTrue(is_retryable(RuntimeError("Connection reset by peer")))
        self.assertTrue(is_retryable(RuntimeError("Request timed out")))

    def test_plain_errors_are_not_retryable(self):
        self.assertFalse(is_retryable(ValueError("bad argument")))
        self.assertFalse(is_retryable(FakeAPIError("no key", 401)))
        self.assertFalse(is_retryable(FakeAPIError("not found", 404)))

    def test_exception_without_status_code_is_safe(self):
        self.assertFalse(is_retryable(KeyError("missing")))

    def test_string_status_code_is_understood(self):
        self.assertTrue(is_retryable(FakeAPIError("limited", "429")))


class DelayForTests(unittest.TestCase):
    def test_exponential_growth(self):
        policy = RetryPolicy(base_delay=1.0, max_delay=100.0, jitter=0.0)
        self.assertEqual(
            [policy.delay_for(attempt) for attempt in (1, 2, 3, 4)],
            [1.0, 2.0, 4.0, 8.0],
        )

    def test_max_delay_caps_growth(self):
        policy = RetryPolicy(base_delay=1.0, max_delay=5.0, jitter=0.0)
        self.assertEqual(
            [policy.delay_for(attempt) for attempt in (1, 2, 3, 4, 10)],
            [1.0, 2.0, 4.0, 5.0, 5.0],
        )

    def test_jitter_stays_within_band(self):
        policy = RetryPolicy(base_delay=2.0, max_delay=100.0, jitter=0.25)
        for seed in range(20):
            random.seed(seed)
            delay = policy.delay_for(3)  # 8.0 before jitter
            self.assertGreaterEqual(delay, 6.0)
            self.assertLessEqual(delay, 10.0)

    def test_delay_is_never_negative(self):
        policy = RetryPolicy(base_delay=0.0, max_delay=0.0, jitter=0.5)
        for seed in range(10):
            random.seed(seed)
            self.assertGreaterEqual(policy.delay_for(1), 0.0)


class RetryCallTests(unittest.TestCase):
    def setUp(self):
        self.sleeps: list[float] = []
        self.policy = RetryPolicy(
            max_attempts=3, base_delay=1.0, max_delay=4.0, jitter=0.0
        )

    def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)

    def test_second_attempt_succeeds(self):
        calls = []

        def fn():
            calls.append(1)
            if len(calls) == 1:
                raise FakeAPIError("rate limited", 429)
            return "ok"

        result = retry_call(fn, policy=self.policy, sleep=self.sleep)
        self.assertEqual(result, "ok")
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.sleeps, [1.0])

    def test_non_retryable_error_raises_immediately(self):
        calls = []

        def fn():
            calls.append(1)
            raise ValueError("nope")

        with self.assertRaises(ValueError):
            retry_call(fn, policy=self.policy, sleep=self.sleep)
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.sleeps, [])

    def test_exhausted_attempts_raise_last_error(self):
        calls = []

        def fn():
            calls.append(1)
            raise FakeAPIError("down %d" % len(calls), 503)

        with self.assertRaises(FakeAPIError) as caught:
            retry_call(fn, policy=self.policy, sleep=self.sleep)
        self.assertEqual(len(calls), 3)
        self.assertEqual(str(caught.exception), "down 3")
        self.assertEqual(self.sleeps, [1.0, 2.0])

    def test_should_stop_prevents_retry(self):
        calls = []

        def fn():
            calls.append(1)
            raise FakeAPIError("rate limited", 429)

        with self.assertRaises(FakeAPIError):
            retry_call(
                fn,
                policy=self.policy,
                should_stop=lambda: True,
                sleep=self.sleep,
            )
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.sleeps, [])

    def test_should_stop_checked_after_failure_only(self):
        stopped = []

        def should_stop():
            stopped.append(1)
            return False

        result = retry_call(
            lambda: "fine",
            policy=self.policy,
            should_stop=should_stop,
            sleep=self.sleep,
        )
        self.assertEqual(result, "fine")
        self.assertEqual(stopped, [])

    def test_on_retry_failure_does_not_block_retry(self):
        calls = []
        retries = []

        def fn():
            calls.append(1)
            if len(calls) == 1:
                raise FakeAPIError("overloaded", 503)
            return "ok"

        def on_retry(exc, attempt, delay):
            retries.append((attempt, delay))
            raise RuntimeError("ui exploded")

        result = retry_call(fn, policy=self.policy, on_retry=on_retry, sleep=self.sleep)
        self.assertEqual(result, "ok")
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(retries), 1)
        self.assertEqual(retries[0][0], 1)

    def test_on_retry_receives_exception_attempt_and_delay(self):
        seen = []
        calls = []

        def fn():
            calls.append(1)
            if len(calls) < 3:
                raise FakeAPIError("timed out", 408)
            return "done"

        retry_call(
            fn,
            policy=self.policy,
            on_retry=lambda exc, attempt, delay: seen.append((attempt, delay)),
            sleep=self.sleep,
        )
        self.assertEqual([item[0] for item in seen], [1, 2])
        self.assertEqual(self.sleeps, [1.0, 2.0])

    def test_on_attempt_runs_before_every_attempt(self):
        attempts = []
        calls = []

        def fn():
            calls.append(1)
            if len(calls) == 1:
                raise FakeAPIError("rate limited", 429)
            return "ok"

        retry_call(
            fn,
            policy=self.policy,
            on_attempt=attempts.append,
            sleep=self.sleep,
        )
        self.assertEqual(attempts, [1, 2])

    def test_on_attempt_failure_is_ignored(self):
        def on_attempt(attempt):
            raise RuntimeError("noisy")

        self.assertEqual(
            retry_call(lambda: 7, policy=self.policy, on_attempt=on_attempt), 7
        )


class ConfigRetryPolicyTests(unittest.TestCase):
    def test_defaults_map_to_policy(self):
        policy = Config().retry_policy()
        self.assertIsInstance(policy, RetryPolicy)
        self.assertEqual(policy.max_attempts, 3)
        self.assertEqual(policy.base_delay, 1.0)
        self.assertEqual(policy.max_delay, 20.0)

    def test_custom_fields_map_to_policy(self):
        config = Config(max_retries=5, retry_base_delay=0.25, retry_max_delay=8.0)
        policy = config.retry_policy()
        self.assertEqual(policy.max_attempts, 5)
        self.assertEqual(policy.base_delay, 0.25)
        self.assertEqual(policy.max_delay, 8.0)

    def test_environment_overrides_retry_settings(self):
        with mock.patch.dict(
            os.environ,
            {
                "ELA_MAX_RETRIES": "4",
                "ELA_RETRY_BASE_DELAY": "0.5",
                "ELA_RETRY_MAX_DELAY": "6.5",
            },
            clear=False,
        ):
            config = apply_environment(Config())
        self.assertEqual(config.max_retries, 4)
        self.assertEqual(config.retry_base_delay, 0.5)
        self.assertEqual(config.retry_max_delay, 6.5)

    def test_invalid_environment_values_are_ignored(self):
        with mock.patch.dict(
            os.environ,
            {
                "ELA_MAX_RETRIES": "many",
                "ELA_RETRY_BASE_DELAY": "soon",
                "ELA_RETRY_MAX_DELAY": "",
            },
            clear=False,
        ):
            config = apply_environment(Config())
        self.assertEqual(config.max_retries, 3)
        self.assertEqual(config.retry_base_delay, 1.0)
        self.assertEqual(config.retry_max_delay, 20.0)


if __name__ == "__main__":
    unittest.main()
