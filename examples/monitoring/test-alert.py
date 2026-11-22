#!/usr/bin/env python3
"""Explicit test of the Alertmanager notification path, including resolution."""
import argparse
import json
import urllib.request
from datetime import datetime, timedelta, timezone


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--resolve', action='store_true')
    parser.add_argument('--url', default='http://127.0.0.1:9093')
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    alert = {'labels': {'alertname': 'MonitoringTest', 'host': 'controller', 'severity': 'info'},
             'annotations': {'summary': 'Проверка уведомлений MongoDB DR',
                             'description': 'Тест канала Prometheus Alertmanager → Telegram; аварии нет.'},
             'startsAt': (now - timedelta(seconds=30)).isoformat(),
             'endsAt': (now if args.resolve else now + timedelta(minutes=5)).isoformat()}
    request = urllib.request.Request(args.url.rstrip('/') + '/api/v2/alerts',
                                     data=json.dumps([alert]).encode(),
                                     headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(request, timeout=10) as response:
        print('Alertmanager accepted', 'resolution' if args.resolve else 'test alert', response.status)


if __name__ == '__main__':
    main()
