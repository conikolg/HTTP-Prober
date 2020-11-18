#!/usr/bin/env python3

import argparse
import time

import prometheus_client
import requests


def check_port(port) -> int:
    """
    Determines if the given port is a valid (a positive integer
    between 1 and 65535, inclusive) port number.
    :param port: the given port number
    :return: the validated port number
    :raise argparse.ArgumentTypeError: when the given port number is not
    an integer value or falls outside the allowed range of port numbers.
    """

    try:
        port = int(port)
        if port < 1:
            raise argparse.ArgumentTypeError(f'{port} must be at least 1')
        if port > 65535:
            raise argparse.ArgumentTypeError(f'{port} cannot exceed 65535')
    except ValueError:
        raise argparse.ArgumentTypeError(f'{port} must be a positive integer value')

    return port


def check_frequency(frequency) -> float:
    """
    Determines if the given frequency is positive number.
    :param frequency: the given frequency
    :return: the validated frequency
    :raise argparse.ArgumentTypeError: when the given frequency is not
    a positive floating point value.
    """

    try:
        frequency = float(frequency)
        if frequency <= 0:
            raise argparse.ArgumentTypeError(f'{frequency} must be a positive value')
    except ValueError:
        raise argparse.ArgumentTypeError(f'{frequency} must be a positive value')

    return frequency


def check_timeout(timeout) -> float:
    """
    Determines if the given timeout is positive number.
    :param timeout: the given timeout
    :return: the validated timeout
    :raise argparse.ArgumentTypeError: when the given timeout is not
    a positive floating point value.
    """

    try:
        timeout = float(timeout)
        if timeout <= 0:
            raise argparse.ArgumentTypeError(f'{timeout} must be a positive value')
    except ValueError:
        raise argparse.ArgumentTypeError(f'{timeout} must be a positive value')

    return timeout


# Handling command line arguments
my_parser = argparse.ArgumentParser(description='Sends a GET request to a webserver and tracks latency')
my_parser.add_argument('-a', '--address', action='store', help='IP address or URL of the target webserver',
                       default='127.0.0.1', type=str)
my_parser.add_argument('-p', '--port', action='store', help='port number of the target webserver',
                       default=80, type=check_port)
my_parser.add_argument('-f', '--frequency', action='store',
                       help='number of times per second a GET request will be sent',
                       default=1, type=check_frequency)
my_parser.add_argument('-t', '--timeout', action='store', help='maximum time in seconds to wait for a server response',
                       default=1, type=check_timeout)
my_parser.add_argument('--server-port', action='store', help='port number on which metrics will be exposed',
                       default=8000, type=check_port)
arguments = my_parser.parse_args()

# Prometheus metric objects
APP_METRIC_PREFIX = 'python_probe'
http_requests_completed = prometheus_client.Counter(
    name=f'{APP_METRIC_PREFIX}_http_requests_completed',
    documentation='number of HTTP requests sent with server response',
    labelnames=('method', 'target', 'code'))
http_requests_errors = prometheus_client.Counter(
    name=f'{APP_METRIC_PREFIX}_http_requests_errors',
    documentation='number of HTTP requests sent without server response',
    labelnames=('method', 'target', 'type'))
latency_gauge = prometheus_client.Gauge(
    name=f'{APP_METRIC_PREFIX}_latest_latency_seconds',
    documentation='round-trip latency of most recent scrape',
    labelnames=('method', 'target'))
latency_histogram = prometheus_client.Histogram(
    name=f'{APP_METRIC_PREFIX}_latency_seconds',
    documentation='bucketed groups of round-trip latencies',
    labelnames=('method', 'target'))


def main():
    # Initialize some metrics
    target_endpoint = f'http://{arguments.address}:{arguments.port}'
    http_requests_completed.labels(method='GET', target=target_endpoint, code=200).inc(0)
    for err in ['http', 'connection', 'redirects', 'timeout', 'request', 'unknown']:
        http_requests_errors.labels(method='GET', target=target_endpoint, type=err).inc(0)

    while True:
        now = time.time()
        try:
            # Send request to endpoint
            response = requests.get(target_endpoint, timeout=arguments.timeout)
            # Assuming no errors in the request itself, count the type of result
            http_requests_completed.labels(method='GET', target=target_endpoint, code=response.status_code).inc()
            # Track latency only for completed requests
            latency = time.time() - now
            latency_gauge.labels(method='GET', target=target_endpoint).set(latency)
            latency_histogram.labels(method='GET', target=target_endpoint).observe(latency)
        except requests.exceptions.HTTPError:
            http_requests_errors.labels(method='GET', target=target_endpoint, type='http').inc()
        except requests.exceptions.ConnectionError:
            http_requests_errors.labels(method='GET', target=target_endpoint, type='connection').inc()
        except requests.exceptions.TooManyRedirects:
            http_requests_errors.labels(method='GET', target=target_endpoint, type='redirects').inc()
        except requests.exceptions.Timeout:
            http_requests_errors.labels(method='GET', target=target_endpoint, type='timeout').inc()
        except requests.exceptions.RequestException:
            http_requests_errors.labels(method='GET', target=target_endpoint, type='request').inc()
        except Exception:
            http_requests_errors.labels(method='GET', target=target_endpoint, type='unknown').inc()
        finally:
            # To keep timing right for when errors occur
            latency = time.time() - now

        # Wait long enough to keep pace with indicated frequency of requests per second
        # Factor in the time this past request took
        # Ensure we cannot wait a negative amount of times
        print('Waiting', max(0, 1 / arguments.frequency - latency))
        time.sleep(max(0, 1 / arguments.frequency - latency))


if __name__ == '__main__':
    prometheus_client.start_http_server(arguments.server_port)
    main()
