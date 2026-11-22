#!/usr/bin/env python3
"""Plot measured scaling results; never fabricate missing measurements."""
import argparse
import json
import os
from pathlib import Path

cache = Path(__file__).resolve().parent.parent / 'artifacts/matplotlib'
cache.mkdir(parents=True, exist_ok=True)
os.environ.setdefault('MPLCONFIGDIR', str(cache))
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('summary', type=Path)
    args = parser.parse_args()
    rows = sorted(json.loads(args.summary.read_text())['measurements'], key=lambda r: r['database_gib'])
    if not rows or not all(r['verified'] for r in rows):
        parser.error('Only verified real measurements can be plotted')
    x = [r['database_gib'] for r in rows]
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 11})
    fig, axis = plt.subplots(figsize=(10, 5.6), layout='constrained')
    for name, label, color in (
        ('snapshot_seconds', 'Полная логическая копия', '#2563eb'),
        ('restore_seconds', 'Восстановление PBM до точки PITR', '#d97706'),
        ('verification_seconds', 'Проверка данных', '#059669'),
    ):
        axis.plot(x, [r[name] for r in rows], 'o-', label=label, color=color, linewidth=2)
    axis.set(title='Время операций при росте базы', xlabel='Начальный объём документов BSON, ГиБ',
             ylabel='Время, с', xticks=range(1, 6),
             ylim=(0, max(r[key] for r in rows for key in
                          ('snapshot_seconds', 'restore_seconds', 'verification_seconds')) * 1.15))
    axis.grid(alpha=0.25)
    axis.legend()
    fig.suptitle('Один прогон на размер; нагрузка — 3 × 300 операций', fontsize=11)
    for extension in ('png', 'svg'):
        fig.savefig(args.summary.parent / f'timings.{extension}', dpi=180)
    plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(12, 5), layout='constrained')
    for axis, name, unit, factor, title, color in (
        (axes[0], 'snapshot_bytes', 'ГиБ', 1024 ** 3, 'Полная копия в S3', '#2563eb'),
        (axes[1], 'changes_bytes', 'МиБ', 1024 ** 2, 'Новые сегменты oplog за 900 операций', '#059669'),
    ):
        axis.plot(x, [r[name] / factor for r in rows], 'o-', color=color, linewidth=2)
        axis.set(title=title, xlabel='Начальный объём BSON, ГиБ', ylabel=unit,
                 xticks=range(1, 6), ylim=(0, max(r[name] / factor for r in rows) * 1.15))
        axis.grid(alpha=0.25)
    fig.suptitle('Сжатие s2; одинаковые настройки нагрузки на всех размерах', fontsize=11)
    for extension in ('png', 'svg'):
        fig.savefig(args.summary.parent / f'archive-size.{extension}', dpi=180)
    plt.close(fig)


if __name__ == '__main__':
    main()
