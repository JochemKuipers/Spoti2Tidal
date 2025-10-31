"""
Stress test script for TIDAL API rate limits.
Tests various configurations to find optimal requests/minute with minimal rate limit warnings.
"""

import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import NamedTuple

from logging_config import setup_logging
from services.tidal import _TIDAL_API_SEMAPHORE, _TIDAL_RATE_LIMITER, Tidal, TokenBucketRateLimiter


class TestConfig(NamedTuple):
    """Configuration for a single test run."""

    rate: float  # requests per second
    capacity: int  # burst capacity
    concurrency: int  # max concurrent requests
    min_delay: float
    max_delay: float


class TestResult(NamedTuple):
    """Results from a test run."""

    config: TestConfig
    total_requests: int
    successful_requests: int
    rate_limit_errors: int
    other_errors: int
    duration_seconds: float
    requests_per_minute: float
    avg_response_time: float
    rate_limit_warning_percent: float


# Test queries to use for API calls
TEST_QUERIES = [
    "beatles",
    "pink floyd",
    "led zeppelin",
    "queen",
    "radiohead",
    "nirvana",
    "the rolling stones",
    "david bowie",
    "fleetwood mac",
    "the beatles",
]


def make_test_request(tidal: Tidal, query: str) -> tuple[bool, bool, float]:
    """
    Make a test API request (search).
    Returns: (success, was_rate_limit, response_time)
    """
    start_time = time.time()
    try:
        tidal._search_tracks(query, limit=10)  # Just test if request succeeds
        response_time = time.time() - start_time
        return True, False, response_time
    except Exception as e:
        response_time = time.time() - start_time
        error_msg = str(e).lower()
        is_rate_limit = "429" in error_msg or "too many" in error_msg or "rate" in error_msg
        return False, is_rate_limit, response_time


def run_test_config(tidal: Tidal, config: TestConfig, duration_seconds: int = 60) -> TestResult:
    """
    Run a stress test with the given configuration.

    Args:
        tidal: Tidal service instance
        config: Test configuration
        duration_seconds: How long to run the test (default 60 seconds)

    Returns:
        TestResult with statistics
    """
    print(f"\n{'=' * 80}")
    print(
        f"Testing config: rate={config.rate} req/s, capacity={config.capacity}, "
        f"concurrency={config.concurrency}"
    )
    print(f"{'=' * 80}")

    # Temporarily replace the global rate limiter and semaphore
    original_limiter = _TIDAL_RATE_LIMITER
    original_semaphore = _TIDAL_API_SEMAPHORE

    # Create new rate limiter with test config
    test_limiter = TokenBucketRateLimiter(
        rate=config.rate,
        capacity=config.capacity,
        min_delay=config.min_delay,
        max_delay=config.max_delay,
    )

    from threading import Semaphore

    test_semaphore = Semaphore(config.concurrency)

    # Monkey-patch the global instances (for testing only!)
    import services.tidal as tidal_module

    tidal_module._TIDAL_RATE_LIMITER = test_limiter
    tidal_module._TIDAL_API_SEMAPHORE = test_semaphore

    try:
        stats = {
            "total": 0,
            "successful": 0,
            "rate_limit_errors": 0,
            "other_errors": 0,
            "response_times": [],
        }

        start_time = time.time()
        end_time = start_time + duration_seconds

        # Use thread pool to make concurrent requests
        query_index = 0
        with ThreadPoolExecutor(max_workers=config.concurrency) as executor:
            futures = set()  # Use set to avoid duplicates

            while time.time() < end_time:
                # Submit new requests up to concurrency limit
                while len(futures) < config.concurrency and time.time() < end_time:
                    query = TEST_QUERIES[query_index % len(TEST_QUERIES)]
                    query_index += 1
                    future = executor.submit(make_test_request, tidal, query)
                    futures.add(future)
                    stats["total"] += 1

                # Process any completed requests (non-blocking)
                # Collect completed futures without waiting
                completed_futures = [f for f in futures if f.done()]

                for future in completed_futures:
                    try:
                        success, was_rate_limit, response_time = future.result()
                        stats["response_times"].append(response_time)
                        if success:
                            stats["successful"] += 1
                        elif was_rate_limit:
                            stats["rate_limit_errors"] += 1
                        else:
                            stats["other_errors"] += 1
                    except Exception:
                        stats["other_errors"] += 1
                    finally:
                        futures.discard(future)

                # Small sleep to prevent busy waiting
                if not completed_futures:
                    time.sleep(0.05)

        # Wait for remaining futures (with timeout)
        if futures:
            remaining_futures = list(futures)
            try:
                for future in as_completed(remaining_futures, timeout=30.0):
                    try:
                        success, was_rate_limit, response_time = future.result()
                        stats["response_times"].append(response_time)
                        if success:
                            stats["successful"] += 1
                        elif was_rate_limit:
                            stats["rate_limit_errors"] += 1
                        else:
                            stats["other_errors"] += 1
                    except Exception:
                        stats["other_errors"] += 1
            except TimeoutError:
                # Some futures still not complete after 30s - count as errors
                for future in remaining_futures:
                    if not future.done():
                        stats["other_errors"] += 1

        actual_duration = time.time() - start_time
        avg_response_time = (
            sum(stats["response_times"]) / len(stats["response_times"])
            if stats["response_times"]
            else 0.0
        )
        requests_per_minute = (stats["total"] / actual_duration) * 60
        rate_limit_warning_percent = (
            (stats["rate_limit_errors"] / stats["total"]) * 100 if stats["total"] > 0 else 0.0
        )

        result = TestResult(
            config=config,
            total_requests=stats["total"],
            successful_requests=stats["successful"],
            rate_limit_errors=stats["rate_limit_errors"],
            other_errors=stats["other_errors"],
            duration_seconds=actual_duration,
            requests_per_minute=requests_per_minute,
            avg_response_time=avg_response_time,
            rate_limit_warning_percent=rate_limit_warning_percent,
        )

        return result

    finally:
        # Restore original instances
        tidal_module._TIDAL_RATE_LIMITER = original_limiter
        tidal_module._TIDAL_API_SEMAPHORE = original_semaphore


def print_result(result: TestResult):
    """Print formatted test result."""
    print("\nResults:")
    print(f"  Total requests: {result.total_requests}")
    success_pct = (
        result.successful_requests / result.total_requests * 100
        if result.total_requests > 0
        else 0.0
    )
    print(f"  Successful: {result.successful_requests} ({success_pct:.1f}%)")
    print(
        f"  Rate limit errors: {result.rate_limit_errors} "
        f"({result.rate_limit_warning_percent:.2f}%)"
    )
    print(f"  Other errors: {result.other_errors}")
    print(f"  Duration: {result.duration_seconds:.1f} seconds")
    print(f"  Requests/minute: {result.requests_per_minute:.1f}")
    print(f"  Avg response time: {result.avg_response_time * 1000:.1f}ms")


def generate_test_configs() -> list[TestConfig]:
    """Generate a series of test configurations to try."""
    configs = []

    # Start conservative and increase
    base_configs = [
        # (rate_per_sec, capacity, concurrency, min_delay, max_delay)
        (5.0, 3, 3, 0.05, 2.0),  # Very conservative
        (10.0, 5, 5, 0.05, 2.0),  # Current default
        (15.0, 8, 8, 0.05, 2.0),  # Moderate increase
        (20.0, 10, 10, 0.05, 2.0),  # Aggressive
        (30.0, 15, 15, 0.05, 2.0),  # Very aggressive
        (40.0, 20, 20, 0.05, 2.0),  # Extreme
    ]

    for rate, capacity, concurrency, min_delay, max_delay in base_configs:
        configs.append(
            TestConfig(
                rate=rate,
                capacity=capacity,
                concurrency=concurrency,
                min_delay=min_delay,
                max_delay=max_delay,
            )
        )

    return configs


def main():
    """Run stress tests and generate report."""
    setup_logging()
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.WARNING)  # Reduce noise during testing

    print("=" * 80)
    print("TIDAL API Rate Limit Stress Test")
    print("=" * 80)
    print("\nThis script will test various rate limit configurations to find")
    print("the optimal requests/minute with minimal rate limit warnings.")
    print("\nWARNING: This will make many API calls. Use responsibly!")
    print("\nMake sure you're logged into TIDAL before running this.")

    response = input("\nContinue? (yes/no): ")
    if response.lower() not in ("yes", "y"):
        print("Aborted.")
        sys.exit(0)

    # Initialize TIDAL
    tidal = Tidal()
    if not tidal.ensure_logged_in():
        print("\nERROR: Not logged into TIDAL. Please log in first.")
        sys.exit(1)

    print("\n✓ TIDAL session active")
    print("\nStarting stress tests (each test runs for 60 seconds)...\n")

    configs = generate_test_configs()
    results: list[TestResult] = []

    for i, config in enumerate(configs, 1):
        print(f"\n[{i}/{len(configs)}] ", end="")
        try:
            result = run_test_config(tidal, config, duration_seconds=60)
            results.append(result)
            print_result(result)

            # If we hit too many rate limits, the remaining configs will likely be worse
            if result.rate_limit_warning_percent > 50:
                print(
                    f"\n⚠ High rate limit error rate ({result.rate_limit_warning_percent:.1f}%). "
                    f"Stopping aggressive tests."
                )
                break

            # Cooldown between tests
            if i < len(configs):
                print("\n⏳ Cooldown: waiting 10 seconds before next test...")
                time.sleep(10)

        except KeyboardInterrupt:
            print("\n\nTest interrupted by user.")
            break
        except Exception as e:
            logger.exception(f"Error during test: {e}")
            print(f"\n⚠ Test failed with error: {e}")
            continue

    # Generate final report
    print("\n" + "=" * 80)
    print("FINAL REPORT")
    print("=" * 80)

    if not results:
        print("No successful test runs. Cannot generate report.")
        return

    # Find best config (highest RPM with < 5% rate limit errors)
    best_result = None
    best_rpm_with_low_errors = 0

    for result in results:
        if result.rate_limit_warning_percent < 5.0:
            if result.requests_per_minute > best_rpm_with_low_errors:
                best_rpm_with_low_errors = result.requests_per_minute
                best_result = result

    print("\n📊 All Test Results:")
    print("-" * 80)
    print(
        f"{'Rate':<8} {'Capacity':<10} {'Concurrency':<12} {'RPM':<8} "
        f"{'Rate Limit %':<15} {'Avg Time (ms)':<15}"
    )
    print("-" * 80)

    for result in results:
        print(
            f"{result.config.rate:<8.1f} {result.config.capacity:<10} "
            f"{result.config.concurrency:<12} {result.requests_per_minute:<8.1f} "
            f"{result.rate_limit_warning_percent:<15.2f} {result.avg_response_time * 1000:<15.1f}"
        )

    print("\n" + "=" * 80)
    print("RECOMMENDATIONS")
    print("=" * 80)

    if best_result:
        print("\n✅ Best configuration (with < 5% rate limit errors):")
        print(f"   Rate: {best_result.config.rate} req/s")
        print(f"   Capacity: {best_result.config.capacity}")
        print(f"   Concurrency: {best_result.config.concurrency}")
        print(f"   Achieved: {best_result.requests_per_minute:.1f} requests/minute")
        print(f"   Rate limit errors: {best_result.rate_limit_warning_percent:.2f}%")
    else:
        print("\n⚠ No configuration achieved < 5% rate limit errors.")
        print("   Here's the configuration with lowest error rate:")
        best_lowest_errors = min(results, key=lambda r: r.rate_limit_warning_percent)
        print(f"   Rate: {best_lowest_errors.config.rate} req/s")
        print(f"   Capacity: {best_lowest_errors.config.capacity}")
        print(f"   Concurrency: {best_lowest_errors.config.concurrency}")
        print(f"   Achieved: {best_lowest_errors.requests_per_minute:.1f} requests/minute")
        print(f"   Rate limit errors: {best_lowest_errors.rate_limit_warning_percent:.2f}%")

    # Summary statistics
    print("\n📈 Summary:")
    print(f"   Total tests run: {len(results)}")
    print(f"   Highest RPM: {max(r.requests_per_minute for r in results):.1f}")
    print(f"   Lowest error rate: {min(r.rate_limit_warning_percent for r in results):.2f}%")

    print("\n" + "=" * 80)


if __name__ == "__main__":
    main()
