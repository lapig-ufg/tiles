from prometheus_client import Counter, Gauge, Histogram


cache_reads_total = Counter(
    "tile_cache_reads_total", "Resultados da leitura do cache", ("result",)
)
cache_uploads_total = Counter(
    "tile_cache_uploads_total", "Resultados da persistência de tiles", ("result",)
)
cache_pending_uploads = Gauge(
    "tile_cache_pending_uploads", "Uploads admitidos aguardando conclusão"
)
cache_pending_bytes = Gauge(
    "tile_cache_pending_bytes", "Bytes dos uploads admitidos aguardando conclusão"
)
cache_uploads_active = Gauge(
    "tile_cache_uploads_active", "Uploads em execução"
)
cache_upload_seconds = Histogram(
    "tile_cache_upload_seconds", "Duração da persistência de tiles", ("result",)
)
cache_queue_wait_seconds = Histogram(
    "tile_cache_queue_wait_seconds", "Espera por uma posição de upload"
)
cache_locks_total = Counter(
    "tile_cache_locks_total", "Resultados da aquisição e manutenção de locks", ("result",)
)
cache_lock_wait_seconds = Histogram(
    "tile_cache_lock_wait_seconds", "Espera pela aquisição de lock"
)
