#!/usr/bin/env python3

import argparse
import os
import signal
import sys
import time
from threading import Thread

import prometheus_client
import requests
import yaml


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
my_parser = argparse.ArgumentParser(
    description='Sends GET requests to a webserver at a particular frequency and tracks latency for all requests')
my_parser.add_argument('--config-file', action='store', help='path to a configuration file',
                       default='./config.yaml', type=str)
arguments = my_parser.parse_args()

# Declare config object
config = dict()

# Prometheus metric objects
APP_METRIC_PREFIX = 'http_probe'
http_requests_completed = prometheus_client.Counter(
    name=f'{APP_METRIC_PREFIX}_http_requests_completed',
    documentation='number of HTTP requests sent with server response',
    labelnames=('method', 'target', 'code'))
http_requests_errors = prometheus_client.Counter(
    name=f'{APP_METRIC_PREFIX}_http_requests_errors',
    documentation='number of HTTP requests sent without server response',
    labelnames=('method', 'target', 'type'))
latency_histogram = prometheus_client.Histogram(
    name=f'{APP_METRIC_PREFIX}_latency_seconds',
    documentation='bucketed groups of round-trip latencies',
    labelnames=('method', 'target'))


def load_configuration(pathname: str) -> bool:
    global config

    try:
        with open(pathname, 'r') as config_file:
            configuration = yaml.load(config_file, Loader=yaml.FullLoader)
            if verify_config(configuration):
                config = configuration
                from requests.packages.urllib3.exceptions import InsecureRequestWarning
                if not configuration['target']['verify_ssl']:
                    requests.packages.urllib3.disable_warnings(InsecureRequestWarning)
                return True
            else:
                return False
    except IOError:
        return False


def reload_configuration(signal_number, frame):
    print('Reloading config...')
    if load_configuration(arguments.config_file):
        print(config)
    else:
        print('Reload failed.')


def verify_config(configuration: dict) -> bool:
    if not isinstance(configuration, dict):
        return False

    # Server port may be specified, but is not required - default to port 8000
    if 'server' in configuration and 'port' in configuration['server']:
        configuration['server']['port'] = check_port(configuration['server']['port'])
    else:
        configuration['server'] = {'port': 8000}

    # Target config block must exist
    if 'target' not in configuration:
        return False
    else:
        target = configuration['target']
        # An address must be defined
        if 'address' not in target:
            return False
        # Other parameters are optional
        target['port'] = 80 if 'port' not in target else check_port(target['port'])
        target['pathname'] = '/' if 'pathname' not in target else target['pathname']
        target['protocol'] = 'http' if 'protocol' not in target else target['protocol']
        target['frequency'] = 1 if 'frequency' not in target else check_frequency(target['frequency'])
        target['timeout'] = 1 if 'timeout' not in target else check_timeout(target['timeout'])
        target['verify_ssl'] = True if 'verify_ssl' not in target else target['verify_ssl']
        return True


def http_request(endpoint: str, timeout: float, verify: bool):
    now = time.time()
    try:
        # Send request to endpoint
        response = requests.get(endpoint, timeout=timeout, verify=verify)
        # Assuming no errors in the request itself, count the type of result
        http_requests_completed.labels(method='GET', target=endpoint, code=response.status_code).inc()
        # Track latency only for completed requests
        latency = time.time() - now
        latency_histogram.labels(method='GET', target=endpoint).observe(latency)
    except requests.exceptions.HTTPError:
        http_requests_errors.labels(method='GET', target=endpoint, type='http').inc()
    except requests.exceptions.ConnectionError:
        http_requests_errors.labels(method='GET', target=endpoint, type='connection').inc()
    except requests.exceptions.TooManyRedirects:
        http_requests_errors.labels(method='GET', target=endpoint, type='redirects').inc()
    except requests.exceptions.Timeout:
        http_requests_errors.labels(method='GET', target=endpoint, type='timeout').inc()
    except requests.exceptions.RequestException:
        http_requests_errors.labels(method='GET', target=endpoint, type='request').inc()
    except Exception:
        http_requests_errors.labels(method='GET', target=endpoint, type='unknown').inc()


def main():
    while True:
        # Get necessary fields / variables from configuration
        protocol, address = config['target']['protocol'], config['target']['address']
        port, pathname = config['target']['port'], config['target']['pathname'],
        target_endpoint = f'{protocol}://{address}:{port}{pathname}'

        # Create thread to execute and maintain the HTTP request
        thread = Thread(target=http_request,
                        args=(target_endpoint, config['target']['timeout'], config['target']['verify_ssl']))
        thread.start()

        # Wait for a predetermined amount before next HTTP request
        time.sleep(1 / config['target']['frequency'])


if __name__ == '__main__':
    # Load configuration
    if not load_configuration(arguments.config_file):
        print(f'Problem occurred when loading f{arguments.config_file}.')
        sys.exit(-1)

    # Output PID
    print(f'PID: {os.getpid()}')

    # Register HUP signal for hot config reload
    signal.signal(signal.SIGHUP, reload_configuration)
    # Expose metrics
    prometheus_client.start_http_server(config['server']['port'])
    # Start main program
    main()
