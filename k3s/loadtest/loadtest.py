#!/usr/bin/env python3
"""
Load test para validação do pool de Service Accounts GEE.

Gera requisições concorrentes para tiles, timeseries e catálogo,
monitorando distribuição de SAs, erros 429 e latência por endpoint.

Uso:
    python3 loadtest.py --base-url http://tiles-loadtest.local \
                        --concurrency 50 \
                        --duration 300 \
                        --log-file /tmp/tiles-loadtest.log
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import random
import signal
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime

import aiohttp

# ---------------------------------------------------------------------------
# Configuração de log
# ---------------------------------------------------------------------------

def setup_logging(log_file: str) -> logging.Logger:
    log = logging.getLogger("loadtest")
    log.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # Arquivo
    fh = logging.FileHandler(log_file, mode="a")
    fh.setFormatter(fmt)
    log.addHandler(fh)
    # Console
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    log.addHandler(ch)
    return log


# ---------------------------------------------------------------------------
# Métricas
# ---------------------------------------------------------------------------

@dataclass
class EndpointStats:
    name: str
    requests: int = 0
    success: int = 0
    errors_429: int = 0
    errors_5xx: int = 0
    errors_other: int = 0
    total_latency: float = 0.0
    min_latency: float = float("inf")
    max_latency: float = 0.0

    @property
    def avg_latency(self) -> float:
        return self.total_latency / self.requests if self.requests else 0

    def record(self, status: int, latency: float) -> None:
        self.requests += 1
        self.total_latency += latency
        self.min_latency = min(self.min_latency, latency)
        self.max_latency = max(self.max_latency, latency)
        if 200 <= status < 300:
            self.success += 1
        elif status == 429:
            self.errors_429 += 1
        elif status >= 500:
            self.errors_5xx += 1
        else:
            self.errors_other += 1

    def summary(self) -> str:
        rate_429 = (self.errors_429 / self.requests * 100) if self.requests else 0
        return (
            f"{self.name:25s} | "
            f"req={self.requests:6d} | "
            f"ok={self.success:6d} | "
            f"429={self.errors_429:4d} ({rate_429:5.1f}%) | "
            f"5xx={self.errors_5xx:4d} | "
            f"lat avg={self.avg_latency:.2f}s "
            f"min={self.min_latency:.2f}s "
            f"max={self.max_latency:.2f}s"
        )


@dataclass
class GlobalStats:
    endpoints: dict[str, EndpointStats] = field(default_factory=dict)
    start_time: float = 0.0
    pool_snapshots: list = field(default_factory=list)

    def get(self, name: str) -> EndpointStats:
        if name not in self.endpoints:
            self.endpoints[name] = EndpointStats(name=name)
        return self.endpoints[name]

    @property
    def total_requests(self) -> int:
        return sum(e.requests for e in self.endpoints.values())

    @property
    def total_429(self) -> int:
        return sum(e.errors_429 for e in self.endpoints.values())

    @property
    def rps(self) -> float:
        elapsed = time.time() - self.start_time
        return self.total_requests / elapsed if elapsed > 0 else 0


# ---------------------------------------------------------------------------
# Cenários de teste
# ---------------------------------------------------------------------------

# Coordenadas de teste — pontos dentro do bioma Cerrado (Brasil)
TEST_POINTS = [
    (-15.7801, -47.9292),   # Brasília
    (-16.6869, -49.2648),   # Goiânia
    (-14.2350, -51.9253),   # Centro do Brasil
    (-12.9714, -38.5124),   # Salvador
    (-19.9167, -43.9345),   # Belo Horizonte
    (-10.9091, -37.0677),   # Aracaju
    (-15.5989, -56.0949),   # Cuiabá
    (-20.4697, -54.6201),   # Campo Grande
]

# Tiles XYZ de teste (zoom 10-12, região do Cerrado)
TEST_TILES = [
    (10, 363, 402), (10, 364, 402), (10, 365, 403),
    (11, 726, 805), (11, 727, 805), (11, 728, 806),
    (12, 1452, 1610), (12, 1453, 1611), (12, 1454, 1610),
    (10, 366, 404), (11, 729, 807), (12, 1455, 1612),
]


def random_tile_url(base: str) -> tuple[str, str]:
    """Retorna URL de tile e nome do endpoint."""
    x, y, z = random.choice(TEST_TILES)
    year = random.choice([2022, 2023, 2024])
    month = random.randint(1, 12)

    endpoint_type = random.choices(
        ["landsat", "s2_harmonized"],
        weights=[60, 40],
    )[0]

    if endpoint_type == "landsat":
        url = (
            f"{base}/api/layers/landsat/{x}/{y}/{z}"
            f"?period=MONTH&year={year}&month={month}"
            f"&visparam=landsat-tvi-false&compositeMode=BEST_IMAGE"
        )
    else:
        url = (
            f"{base}/api/layers/s2_harmonized/{x}/{y}/{z}"
            f"?period=WET&year={year}&visparam=tvi-red"
        )

    return url, f"tile-{endpoint_type}"


def random_timeseries_url(base: str) -> tuple[str, str]:
    """Retorna URL de timeseries e nome do endpoint."""
    lat, lon = random.choice(TEST_POINTS)
    # Pequena variação para evitar cache
    lat += random.uniform(-0.01, 0.01)
    lon += random.uniform(-0.01, 0.01)

    endpoint_type = random.choices(
        ["landsat", "modis", "sentinel2"],
        weights=[40, 30, 30],
    )[0]

    if endpoint_type == "landsat":
        url = (
            f"{base}/api/timeseries/landsat/{lat:.6f}/{lon:.6f}"
            f"?data_inicio=2020-01-01&data_fim=2024-12-31"
        )
    elif endpoint_type == "modis":
        url = (
            f"{base}/api/timeseries/modis/{lat:.6f}/{lon:.6f}"
            f"?data_inicio=2015-01-01&data_fim=2024-12-31"
        )
    else:
        url = (
            f"{base}/api/timeseries/sentinel2/{lat:.6f}/{lon:.6f}"
            f"?data_inicio=2022-01-01&data_fim=2024-12-31"
        )

    return url, f"ts-{endpoint_type}"


def random_catalog_url(base: str) -> tuple[str, str]:
    """Retorna URL de catálogo e nome do endpoint."""
    lat, lon = random.choice(TEST_POINTS)
    layer = random.choice(["s2_harmonized", "landsat"])
    url = (
        f"{base}/api/imagery/{layer}/catalog"
        f"?lat={lat}&lon={lon}&bufferMeters=10000"
        f"&start=2023-01-01&end=2023-06-30"
        f"&maxCloud=30&limit=10&offset=0"
    )
    return url, f"catalog-{layer}"


# ---------------------------------------------------------------------------
# Worker de requisições
# ---------------------------------------------------------------------------

async def request_worker(
    session: aiohttp.ClientSession,
    base_url: str,
    stats: GlobalStats,
    log: logging.Logger,
    stop_event: asyncio.Event,
    worker_id: int,
):
    """Worker que envia requisições continuamente até o stop_event."""
    while not stop_event.is_set():
        # Distribuição de carga: 60% tiles, 25% timeseries, 15% catalog
        roll = random.random()
        if roll < 0.60:
            url, endpoint = random_tile_url(base_url)
        elif roll < 0.85:
            url, endpoint = random_timeseries_url(base_url)
        else:
            url, endpoint = random_catalog_url(base_url)

        ep_stats = stats.get(endpoint)
        t0 = time.time()

        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=120)) as resp:
                latency = time.time() - t0
                status = resp.status
                # Consumir body para liberar a conexão
                await resp.read()

                ep_stats.record(status, latency)

                if status == 429:
                    log.warning(f"[W{worker_id:02d}] 429 em {endpoint} ({latency:.2f}s)")
                    # Backoff antes de continuar
                    await asyncio.sleep(random.uniform(1, 3))
                elif status >= 500:
                    log.error(f"[W{worker_id:02d}] {status} em {endpoint} ({latency:.2f}s)")
                    await asyncio.sleep(0.5)

        except asyncio.TimeoutError:
            latency = time.time() - t0
            ep_stats.record(504, latency)
            log.warning(f"[W{worker_id:02d}] Timeout em {endpoint} ({latency:.2f}s)")
        except aiohttp.ClientError as exc:
            latency = time.time() - t0
            ep_stats.record(503, latency)
            log.error(f"[W{worker_id:02d}] Erro de conexão em {endpoint}: {exc}")
            await asyncio.sleep(1)
        except Exception as exc:
            log.error(f"[W{worker_id:02d}] Erro inesperado: {exc}")
            await asyncio.sleep(1)

        # Pequeno delay entre requisições para não saturar
        await asyncio.sleep(random.uniform(0.05, 0.2))


# ---------------------------------------------------------------------------
# Coletor de métricas do pool GEE
# ---------------------------------------------------------------------------

async def pool_monitor(
    session: aiohttp.ClientSession,
    base_url: str,
    stats: GlobalStats,
    log: logging.Logger,
    stop_event: asyncio.Event,
    interval: int = 30,
):
    """Coleta métricas do pool GEE a cada N segundos."""
    while not stop_event.is_set():
        try:
            async with session.get(
                f"{base_url}/admin/gee/pool",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    stats.pool_snapshots.append({
                        "timestamp": datetime.now().isoformat(),
                        "data": data,
                    })

                    # Log resumido
                    accounts = data.get("accounts", {})
                    total_429 = sum(
                        a.get("errors_429", 0) for a in accounts.values()
                    )
                    cooldowns = sum(
                        1 for a in accounts.values() if a.get("in_cooldown")
                    )
                    workers = sum(
                        a.get("active_workers", 0) for a in accounts.values()
                    )

                    log.info(
                        f"[POOL] SAs={len(accounts)} | "
                        f"workers_ativos={workers} | "
                        f"429_total={total_429} | "
                        f"em_cooldown={cooldowns}"
                    )

                    # Log por SA
                    for sa_name, sa_data in sorted(accounts.items()):
                        short_name = sa_name.split("@")[0] if "@" in sa_name else sa_name[:30]
                        log.info(
                            f"  [{short_name}] "
                            f"workers={sa_data.get('active_workers', 0)} "
                            f"reqs={sa_data.get('total_requests', 0)} "
                            f"429={sa_data.get('errors_429', 0)} "
                            f"cooldown={'SIM' if sa_data.get('in_cooldown') else 'nao'}"
                        )
        except Exception as exc:
            log.warning(f"[POOL] Erro ao coletar métricas: {exc}")

        # Aguardar intervalo ou stop
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


# ---------------------------------------------------------------------------
# Reporter periódico
# ---------------------------------------------------------------------------

async def periodic_reporter(
    stats: GlobalStats,
    log: logging.Logger,
    stop_event: asyncio.Event,
    interval: int = 15,
):
    """Imprime resumo de métricas a cada N segundos."""
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass

        elapsed = time.time() - stats.start_time
        log.info("=" * 100)
        log.info(
            f"[RESUMO] tempo={elapsed:.0f}s | "
            f"req_total={stats.total_requests} | "
            f"rps={stats.rps:.1f} | "
            f"429_total={stats.total_429}"
        )
        for ep in sorted(stats.endpoints.values(), key=lambda e: e.name):
            log.info(f"  {ep.summary()}")
        log.info("=" * 100)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run_loadtest(args):
    log = setup_logging(args.log_file)
    stats = GlobalStats(start_time=time.time())
    stop_event = asyncio.Event()

    # Capturar SIGINT/SIGTERM para encerramento graceful
    for sig in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_event_loop().add_signal_handler(sig, stop_event.set)

    log.info("=" * 100)
    log.info(f"TILES LOAD TEST — Validação do Pool de SAs GEE")
    log.info(f"  Base URL:     {args.base_url}")
    log.info(f"  Concorrência: {args.concurrency} workers")
    log.info(f"  Duração:      {args.duration}s")
    log.info(f"  Log:          {args.log_file}")
    log.info("=" * 100)

    # Verificar conectividade
    connector = aiohttp.TCPConnector(limit=args.concurrency + 10)
    session = aiohttp.ClientSession(connector=connector)

    try:
        async with session.get(
            f"{args.base_url}/health/light",
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                log.error(f"Health check falhou: HTTP {resp.status}")
                return
            log.info(f"Health check OK — iniciando load test")
    except Exception as exc:
        log.error(f"Não foi possível conectar em {args.base_url}: {exc}")
        await session.close()
        return

    # Iniciar workers, monitor e reporter
    tasks = []

    # Workers de requisição
    for i in range(args.concurrency):
        tasks.append(
            asyncio.create_task(
                request_worker(session, args.base_url, stats, log, stop_event, i)
            )
        )

    # Monitor do pool GEE
    tasks.append(
        asyncio.create_task(
            pool_monitor(session, args.base_url, stats, log, stop_event, interval=30)
        )
    )

    # Reporter periódico
    tasks.append(
        asyncio.create_task(
            periodic_reporter(stats, log, stop_event, interval=15)
        )
    )

    # Timer de duração
    async def duration_timer():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=args.duration)
        except asyncio.TimeoutError:
            log.info(f"Duração de {args.duration}s atingida — encerrando...")
            stop_event.set()

    tasks.append(asyncio.create_task(duration_timer()))

    # Aguardar encerramento
    await asyncio.gather(*tasks, return_exceptions=True)
    await session.close()

    # Relatório final
    elapsed = time.time() - stats.start_time
    log.info("")
    log.info("#" * 100)
    log.info(f"RELATÓRIO FINAL — Duração: {elapsed:.0f}s")
    log.info("#" * 100)
    log.info(f"Total de requisições: {stats.total_requests}")
    log.info(f"Throughput médio:     {stats.rps:.1f} req/s")
    log.info(f"Erros 429 totais:     {stats.total_429}")
    log.info("")
    log.info(f"{'Endpoint':25s} | {'Reqs':>6s} | {'OK':>6s} | {'429':>4s} | {'5xx':>4s} | {'Lat Avg':>8s} | {'Lat Max':>8s}")
    log.info("-" * 80)
    for ep in sorted(stats.endpoints.values(), key=lambda e: e.name):
        log.info(
            f"{ep.name:25s} | {ep.requests:6d} | {ep.success:6d} | "
            f"{ep.errors_429:4d} | {ep.errors_5xx:4d} | "
            f"{ep.avg_latency:7.2f}s | {ep.max_latency:7.2f}s"
        )
    log.info("")

    # Último snapshot do pool
    if stats.pool_snapshots:
        last = stats.pool_snapshots[-1]
        log.info(f"Último snapshot do pool GEE ({last['timestamp']}):")
        for sa_name, sa_data in sorted(last["data"].get("accounts", {}).items()):
            short = sa_name.split("@")[0] if "@" in sa_name else sa_name[:40]
            log.info(
                f"  {short:40s} | "
                f"workers={sa_data.get('active_workers', 0):2d} | "
                f"reqs={sa_data.get('total_requests', 0):6d} | "
                f"429={sa_data.get('errors_429', 0):4d} | "
                f"cooldown={'SIM' if sa_data.get('in_cooldown') else 'nao'}"
            )

    # Salvar dados brutos para análise
    report_file = args.log_file.replace(".log", "-report.json")
    report = {
        "config": {
            "base_url": args.base_url,
            "concurrency": args.concurrency,
            "duration_seconds": args.duration,
            "actual_duration": elapsed,
        },
        "summary": {
            "total_requests": stats.total_requests,
            "throughput_rps": stats.rps,
            "total_429": stats.total_429,
        },
        "endpoints": {
            name: {
                "requests": ep.requests,
                "success": ep.success,
                "errors_429": ep.errors_429,
                "errors_5xx": ep.errors_5xx,
                "avg_latency": ep.avg_latency,
                "min_latency": ep.min_latency if ep.min_latency != float("inf") else 0,
                "max_latency": ep.max_latency,
            }
            for name, ep in stats.endpoints.items()
        },
        "pool_snapshots": stats.pool_snapshots,
    }

    with open(report_file, "w") as f:
        json.dump(report, f, indent=2, default=str)
    log.info(f"Relatório JSON salvo em: {report_file}")

    log.info("#" * 100)


def main():
    parser = argparse.ArgumentParser(description="Load test para Tiles API + GEE SA Pool")
    parser.add_argument(
        "--base-url",
        default="http://localhost:8083",
        help="URL base da API (default: http://localhost:8083)",
    )
    parser.add_argument(
        "--concurrency", "-c",
        type=int,
        default=50,
        help="Número de workers concorrentes (default: 50)",
    )
    parser.add_argument(
        "--duration", "-d",
        type=int,
        default=300,
        help="Duração do teste em segundos (default: 300)",
    )
    parser.add_argument(
        "--log-file", "-l",
        default="/tmp/tiles-loadtest.log",
        help="Arquivo de log (default: /tmp/tiles-loadtest.log)",
    )
    args = parser.parse_args()

    asyncio.run(run_loadtest(args))


if __name__ == "__main__":
    main()
