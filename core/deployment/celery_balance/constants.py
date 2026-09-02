"""Константы балансировщика Celery (без привязки к железу хоста)."""

from __future__ import annotations

FOOTPRINT_FILENAME = 'task_footprint.yaml'
DECISION_FILENAME = 'decision.json'
HISTORY_FILENAME = 'history.jsonl'
QUEUES_CACHE_NAME = 'celery_queues.bin'
BEAT_SCHEDULE_CACHE_NAME = 'celery_beat_schedule.bin'
SIGNATURE_SEPARATOR = b'\n---SIGNATURE---\n'

MODE_OFF = 'off'
MODE_RECOMMEND = 'recommend'
MODE_AUTO = 'auto'
MODES = frozenset({MODE_OFF, MODE_RECOMMEND, MODE_AUTO})
DEFAULT_MODE = MODE_AUTO

CLASS_LIGHT = 'light'
CLASS_MEDIUM = 'medium'
CLASS_HEAVY = 'heavy'
TASK_CLASSES = frozenset({CLASS_LIGHT, CLASS_MEDIUM, CLASS_HEAVY})

# Generic defaults — не инвентарь конкретной машины.
CLASS_DEFAULTS: dict[str, dict[str, float | int]] = {
    CLASS_LIGHT: {
        'ram_mb': 128,
        'cpu_units': 0.25,
        'vram_mb': 0,
        'max_parallel': 8,
    },
    CLASS_MEDIUM: {
        'ram_mb': 256,
        'cpu_units': 1.0,
        'vram_mb': 0,
        'max_parallel': 4,
    },
    CLASS_HEAVY: {
        'ram_mb': 1024,
        'cpu_units': 2.0,
        'vram_mb': 0,
        'max_parallel': 1,
    },
}

DEFAULT_OS_RESERVE_RAM_MB = 1024
DEFAULT_RESERVE_CPU = 1.0
DEFAULT_MIN_CONCURRENCY = 1
DEFAULT_WATCH_INTERVAL_SEC = 30.0
DEFAULT_HYSTERESIS_RATIO = 0.2
DEFAULT_HISTORY_MIN_SAMPLES = 5
DEFAULT_PREFETCH = 4
HEAVY_PREFETCH = 1
DEFAULT_RESERVE_LIGHT = 2
DEFAULT_QUEUE_NAME = 'default'
DEFAULT_GPU_UTIL_CAP = 80.0

# Роли, которые нельзя вытеснять бюджетом Celery.
RESERVE_ROLES = frozenset({
    'api',
    'media-api',
    'client',
    'redis',
    'nginx',
    'postgres',
    'meilisearch',
    'jupyter',
    'celery-beat',
})
CELERY_WORKER_ROLE = 'celery-worker'

REDIS_CELERY_BROKER_DB_DEFAULT = 2
