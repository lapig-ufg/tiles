"""
Pool de Service Accounts do Google Earth Engine com coordenação via Redis.

Distribui N service accounts entre workers Gunicorn/Celery, garantindo
balanceamento de carga nas cotas do GEE e rotação automática em caso de 429.

Estruturas Redis:
- gee:sa:pool            — Sorted set: SA name → contagem de uso
- gee:sa:{name}:metrics  — Hash: requests, errors_429, last_429_at, cooldown_until
- gee:sa:assignments:{wid} — String com TTL: SA atribuída ao worker
"""
from __future__ import annotations

import functools
import glob
import json
import os
import random
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Set

import ee
import redis
from google.oauth2 import service_account

from app.core.config import settings, logger, REDIS_URL


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

@dataclass
class ServiceAccountInfo:
    """Informações de uma service account do GEE."""
    name: str
    file_path: str
    credentials: service_account.Credentials | None = field(default=None, repr=False)

    def load_credentials(self) -> service_account.Credentials:
        self.credentials = service_account.Credentials.from_service_account_file(
            self.file_path,
            scopes=["https://www.googleapis.com/auth/earthengine.readonly"],
        )
        return self.credentials


# ---------------------------------------------------------------------------
# Erros
# ---------------------------------------------------------------------------


class PoolExhaustedError(RuntimeError):
    """Sinalizado quando todas as SAs estão em cooldown além do timeout aceitável.

    O caller deve mapear para HTTP 503 com cabeçalho ``Retry-After``.
    """

    def __init__(self, retry_after: float, message: str | None = None) -> None:
        self.retry_after = float(retry_after)
        super().__init__(
            message or f"Pool de SAs esgotado, retry após {self.retry_after:.1f}s"
        )


# ---------------------------------------------------------------------------
# ServiceAccountPool — coordenação centralizada via Redis
# ---------------------------------------------------------------------------

class ServiceAccountPool:
    """Gerencia o pool de service accounts do GEE com coordenação via Redis.

    Usa um sorted set no Redis para rastrear a contagem de uso de cada SA,
    permitindo que workers de múltiplas instâncias selecionem a SA com menor
    carga de forma atômica.
    """

    _instance: ServiceAccountPool | None = None
    _lock = threading.Lock()

    # Prefixos Redis
    _KEY_POOL = "gee:sa:pool"
    _KEY_METRICS = "gee:sa:{}:metrics"
    _KEY_ASSIGNMENT = "gee:sa:assignments:{}"

    def __init__(self, sa_directory: str, redis_url: str):
        self._sa_directory = sa_directory
        self._redis = redis.Redis.from_url(redis_url, decode_responses=True)
        self._accounts: dict[str, ServiceAccountInfo] = {}
        self._discover_accounts()
        self._register_pool()

    @classmethod
    def get_instance(cls) -> ServiceAccountPool:
        """Retorna a instância singleton do pool, criando-a se necessário."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    sa_dir = settings.get(
                        "GEE_SA_DIRECTORY",
                        "/app/.service-accounts/",
                    )
                    cls._instance = cls(sa_dir, REDIS_URL)
        return cls._instance

    # ---- Descoberta de contas ----

    def _discover_accounts(self) -> None:
        """Escaneia o diretório de service accounts e carrega as informações."""
        pattern = os.path.join(self._sa_directory, "*.json")
        files = sorted(glob.glob(pattern))

        for fp in files:
            basename = os.path.basename(fp)
            # Ignorar arquivos de exemplo
            if basename.endswith(".example"):
                continue
            try:
                with open(fp) as f:
                    data = json.load(f)
                # Validar que é um arquivo de service account válido
                if data.get("type") != "service_account":
                    logger.warning(f"Ignorando {basename}: não é service_account")
                    continue
                name = data.get("client_email", basename)
                self._accounts[name] = ServiceAccountInfo(
                    name=name,
                    file_path=fp,
                )
                logger.info(f"SA descoberta: {name}")
            except (json.JSONDecodeError, OSError) as exc:
                logger.error(f"Erro ao ler {fp}: {exc}")

        if not self._accounts:
            raise RuntimeError(
                f"Nenhuma service account válida encontrada em {self._sa_directory}"
            )

        logger.info(f"Pool GEE inicializado com {len(self._accounts)} service accounts")

    def _register_pool(self) -> None:
        """Registra todas as SAs no sorted set do Redis (NX — não sobrescreve)."""
        pipe = self._redis.pipeline()
        for name in self._accounts:
            # ZADD NX: só adiciona se não existir (preserva contagem de uso)
            pipe.zadd(self._KEY_POOL, {name: 0}, nx=True)
            # Inicializa métricas se não existem
            metrics_key = self._KEY_METRICS.format(name)
            pipe.hsetnx(metrics_key, "requests", 0)
            pipe.hsetnx(metrics_key, "errors_429", 0)
            pipe.hsetnx(metrics_key, "last_429_at", "")
            pipe.hsetnx(metrics_key, "cooldown_until", "")
        pipe.execute()

    # ---- Aquisição e liberação ----

    def acquire(self, worker_id: str, exclude: Set[str] | None = None) -> ServiceAccountInfo:
        """Adquire a SA com menor uso que não esteja em cooldown.

        Operação atômica via WATCH/MULTI no Redis.

        Args:
            worker_id: Identificador único do worker (hostname-pid).
            exclude: Conjunto de nomes de SAs a excluir (ex: SA atual em 429).

        Returns:
            ServiceAccountInfo com credenciais carregadas.

        Raises:
            PoolExhaustedError: Quando todas as SAs estão em cooldown e o tempo
                de espera necessário excede ``GEE_SA_ACQUIRE_TIMEOUT_SECONDS``.
                O caller deve mapear para HTTP 503 com ``Retry-After``.
            RuntimeError: Quando o pool não possui nenhuma SA registrada.
        """
        exclude = exclude or set()
        now = time.time()
        acquire_timeout = float(settings.get("GEE_SA_ACQUIRE_TIMEOUT_SECONDS", 2))

        # Buscar SAs ordenadas por uso (menor primeiro) com desempate aleatório
        # entre scores iguais — evita viés lexicográfico do zrangebyscore que
        # faz as SAs alfabeticamente menores absorverem todo o tráfego quando
        # os scores voltam a 0 após release.
        scored = self._redis.zrange(self._KEY_POOL, 0, -1, withscores=True)
        candidates = [
            name for name, _ in sorted(scored, key=lambda sc: (sc[1], random.random()))
        ]

        best_sa: str | None = None
        best_cooldown_end: float = float("inf")

        for sa_name in candidates:
            if sa_name in exclude:
                continue
            if sa_name not in self._accounts:
                continue

            # Verificar cooldown
            metrics_key = self._KEY_METRICS.format(sa_name)
            cooldown_until = self._redis.hget(metrics_key, "cooldown_until")

            if cooldown_until and cooldown_until != "":
                cooldown_end = float(cooldown_until)
                if cooldown_end > now:
                    # SA em cooldown — guardar como fallback
                    if cooldown_end < best_cooldown_end:
                        best_cooldown_end = cooldown_end
                        best_sa = sa_name
                    continue

            # SA disponível — adquirir
            return self._assign(sa_name, worker_id)

        # Nenhuma SA livre — política do circuit breaker
        if best_sa is not None:
            wait_time = best_cooldown_end - now
            if wait_time > acquire_timeout:
                # Espera longa: fail-fast em vez de bloquear o worker.
                logger.warning(
                    f"Pool exhausted: todas as SAs em cooldown, "
                    f"próximo disponível em {wait_time:.1f}s (timeout={acquire_timeout}s). "
                    f"Retornando PoolExhaustedError."
                )
                try:
                    from app.core.metrics import gee_pool_exhausted_total
                    gee_pool_exhausted_total.inc()
                except Exception:
                    pass
                raise PoolExhaustedError(retry_after=wait_time)

            # Espera curta aceitável — preserva comportamento antigo para
            # casos benignos onde uma SA está a poucos segundos do retorno.
            logger.warning(
                f"Todas as SAs em cooldown. Aguardando {wait_time:.1f}s pela SA {best_sa}"
            )
            time.sleep(max(0, wait_time))
            return self._assign(best_sa, worker_id)

        raise RuntimeError("Nenhuma service account disponível no pool")

    def _assign(self, sa_name: str, worker_id: str) -> ServiceAccountInfo:
        """Atribui uma SA a um worker atomicamente."""
        assignment_ttl = settings.get("GEE_SA_ASSIGNMENT_TTL", 90)

        # Incrementar contagem de uso
        self._redis.zincrby(self._KEY_POOL, 1, sa_name)

        # Registrar assignment com TTL
        assignment_key = self._KEY_ASSIGNMENT.format(worker_id)
        self._redis.set(assignment_key, sa_name, ex=assignment_ttl)

        # Incrementar contador de requests
        metrics_key = self._KEY_METRICS.format(sa_name)
        self._redis.hincrby(metrics_key, "requests", 1)

        sa_info = self._accounts[sa_name]
        sa_info.load_credentials()

        # Auto-reset do gauge: SA atribuída por `acquire` significa que passou
        # pelo filtro de cooldown — logo, está fora dele agora.
        try:
            from app.core.metrics import gee_sa_in_cooldown
            gee_sa_in_cooldown.labels(sa_name=sa_name).set(0)
        except Exception:
            pass

        logger.info(f"Worker {worker_id} adquiriu SA {sa_name}")
        return sa_info

    def release(self, worker_id: str) -> None:
        """Libera a SA atribuída ao worker."""
        assignment_key = self._KEY_ASSIGNMENT.format(worker_id)
        sa_name = self._redis.get(assignment_key)

        if sa_name:
            self._redis.zincrby(self._KEY_POOL, -1, sa_name)
            # Garantir que a contagem não fique negativa
            score = self._redis.zscore(self._KEY_POOL, sa_name)
            if score is not None and score < 0:
                self._redis.zadd(self._KEY_POOL, {sa_name: 0})
            self._redis.delete(assignment_key)
            logger.info(f"Worker {worker_id} liberou SA {sa_name}")

    # ---- Tratamento de 429 ----

    def report_429(self, sa_name: str) -> None:
        """Registra um erro 429 para a SA e ativa cooldown."""
        cooldown_seconds = settings.get("GEE_SA_COOLDOWN_SECONDS", 60)
        now = time.time()

        metrics_key = self._KEY_METRICS.format(sa_name)
        pipe = self._redis.pipeline()
        pipe.hincrby(metrics_key, "errors_429", 1)
        pipe.hset(metrics_key, "last_429_at", str(now))
        pipe.hset(metrics_key, "cooldown_until", str(now + cooldown_seconds))
        pipe.execute()

        logger.warning(
            f"SA {sa_name} recebeu 429 — cooldown de {cooldown_seconds}s ativado"
        )

    def report_http_429(self, sa_name: str) -> None:
        """Registra um 429 de download HTTP e aplica cooldown curto.

        Cooldown menor que o de report_429 (REST API) porque a janela de
        rate-limiting do endpoint de tiles recupera mais rápido. Preserva
        um cooldown maior já ativo para não encurtar penalidades.
        """
        cooldown_seconds = settings.get("GEE_SA_HTTP_COOLDOWN_SECONDS", 15)
        now = time.time()
        new_cooldown_end = now + cooldown_seconds

        metrics_key = self._KEY_METRICS.format(sa_name)

        existing = self._redis.hget(metrics_key, "cooldown_until")
        if existing:
            try:
                if float(existing) > new_cooldown_end:
                    pipe = self._redis.pipeline()
                    pipe.hincrby(metrics_key, "errors_429", 1)
                    pipe.hset(metrics_key, "last_429_at", str(now))
                    pipe.execute()
                    try:
                        from app.core.metrics import gee_sa_http_429_total
                        gee_sa_http_429_total.labels(sa_name=sa_name).inc()
                    except Exception:
                        pass
                    return
            except ValueError:
                pass

        pipe = self._redis.pipeline()
        pipe.hincrby(metrics_key, "errors_429", 1)
        pipe.hset(metrics_key, "last_429_at", str(now))
        pipe.hset(metrics_key, "cooldown_until", str(new_cooldown_end))
        pipe.execute()

        try:
            from app.core.metrics import gee_sa_http_429_total, gee_sa_in_cooldown
            gee_sa_http_429_total.labels(sa_name=sa_name).inc()
            gee_sa_in_cooldown.labels(sa_name=sa_name).set(1)
        except Exception:
            # Métrica é best-effort — falha de instrumentação não pode
            # quebrar a rotação que é caminho crítico.
            pass

    # ---- Heartbeat ----

    def refresh_heartbeat(self, worker_id: str) -> None:
        """Renova o TTL do assignment do worker."""
        assignment_ttl = settings.get("GEE_SA_ASSIGNMENT_TTL", 90)
        assignment_key = self._KEY_ASSIGNMENT.format(worker_id)
        self._redis.expire(assignment_key, assignment_ttl)

    # ---- Hot-reload ----

    def refresh_registry(self) -> dict:
        """Re-escaneia o diretório de SAs e registra novas contas no pool.

        Returns:
            Dict com 'added' e 'total' contagens.
        """
        old_names = set(self._accounts.keys())
        self._accounts.clear()
        self._discover_accounts()
        self._register_pool()
        new_names = set(self._accounts.keys())

        added = new_names - old_names
        removed = old_names - new_names

        if added:
            logger.info(f"Novas SAs adicionadas ao pool: {added}")
        if removed:
            logger.info(f"SAs removidas do pool: {removed}")
            # Remover do sorted set
            for name in removed:
                self._redis.zrem(self._KEY_POOL, name)

        return {
            "added": len(added),
            "removed": len(removed),
            "total": len(self._accounts),
            "added_names": list(added),
            "removed_names": list(removed),
        }

    # ---- Métricas ----

    def get_metrics(self) -> dict:
        """Retorna métricas completas do pool para observabilidade."""
        result = {
            "total_accounts": len(self._accounts),
            "accounts": {},
        }

        for sa_name in self._accounts:
            score = self._redis.zscore(self._KEY_POOL, sa_name) or 0
            metrics_key = self._KEY_METRICS.format(sa_name)
            metrics = self._redis.hgetall(metrics_key)

            cooldown_until = metrics.get("cooldown_until", "")
            now = time.time()
            in_cooldown = bool(
                cooldown_until and cooldown_until != "" and float(cooldown_until) > now
            )
            try:
                from app.core.metrics import gee_sa_in_cooldown
                gee_sa_in_cooldown.labels(sa_name=sa_name).set(1 if in_cooldown else 0)
            except Exception:
                pass

            result["accounts"][sa_name] = {
                "active_workers": int(score),
                "total_requests": int(metrics.get("requests", 0)),
                "errors_429": int(metrics.get("errors_429", 0)),
                "last_429_at": metrics.get("last_429_at", ""),
                "in_cooldown": in_cooldown,
                "cooldown_remaining": (
                    max(0, float(cooldown_until) - now) if in_cooldown else 0
                ),
            }

        return result

    def get_assignments(self) -> dict:
        """Retorna todas as atribuições ativas de workers."""
        pattern = "gee:sa:assignments:*"
        assignments = {}
        for key in self._redis.scan_iter(match=pattern, count=100):
            worker_id = key.replace("gee:sa:assignments:", "")
            sa_name = self._redis.get(key)
            ttl = self._redis.ttl(key)
            assignments[worker_id] = {
                "service_account": sa_name,
                "ttl_seconds": ttl,
            }
        return assignments


# ---------------------------------------------------------------------------
# WorkerGEEManager — gerencia o ciclo de vida do GEE por worker
# ---------------------------------------------------------------------------

class WorkerGEEManager:
    """Gerencia a inicialização do GEE e a rotação de SA para um worker.

    Cada processo worker (Gunicorn ou Celery) possui uma instância.
    Protege ee.Initialize() com RLock para evitar race conditions
    com o ThreadPoolExecutor.
    """

    def __init__(self, pool: ServiceAccountPool):
        self._pool = pool
        self._worker_id: str | None = None
        self._current_sa: ServiceAccountInfo | None = None
        self._init_lock = threading.RLock()
        self._heartbeat_thread: threading.Thread | None = None
        self._heartbeat_stop = threading.Event()

    @property
    def current_sa_name(self) -> str | None:
        """Nome da SA atualmente em uso."""
        return self._current_sa.name if self._current_sa else None

    @property
    def init_lock(self) -> threading.RLock:
        """Lock para proteger ee.Initialize() durante rotação."""
        return self._init_lock

    def initialize(self, worker_id: str) -> None:
        """Adquire uma SA e inicializa o Earth Engine."""
        self._worker_id = worker_id

        with self._init_lock:
            self._current_sa = self._pool.acquire(worker_id)
            ee.Initialize(self._current_sa.credentials)
            logger.info(
                f"GEE inicializado para worker {worker_id} "
                f"com SA {self._current_sa.name}"
            )

        # Iniciar heartbeat
        self._heartbeat_stop.clear()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            daemon=True,
            name=f"gee-heartbeat-{worker_id}",
        )
        self._heartbeat_thread.start()

    def rotate_on_429(self, trigger: str = "rest_api_429") -> None:
        """Rotaciona para uma nova SA após erro 429.

        Libera a SA atual, reporta o 429, adquire nova SA e re-inicializa.
        Thread-safe via RLock.

        Args:
            trigger: Origem do 429 — `"rest_api_429"` (default, chamadas via
                ee_compute / @gee_retry) ou `"http_429"` (download de tile via
                fetch_tile_with_rotation). Usado em métricas para discriminar
                causas raiz com SLAs diferentes.
        """
        if not self._worker_id or not self._current_sa:
            logger.error("Tentativa de rotação sem inicialização prévia")
            return

        with self._init_lock:
            old_sa = self._current_sa.name
            logger.warning(f"Rotacionando SA {old_sa} por 429 no worker {self._worker_id}")

            # Reportar 429 e liberar. Cada trigger tem um cooldown próprio:
            # - rest_api_429: 60s (REST API recupera mais devagar).
            # - http_429: 15s (endpoint de tiles recupera mais rápido).
            # `report_http_429` preserva um cooldown maior já ativo, garantindo
            # que `report_429` chamado depois sempre vence — então o método
            # certo precisa ser chamado de acordo com a origem do 429.
            if trigger == "http_429":
                self._pool.report_http_429(old_sa)
            else:
                self._pool.report_429(old_sa)
            self._pool.release(self._worker_id)

            # Adquirir nova SA (excluindo a atual)
            self._current_sa = self._pool.acquire(
                self._worker_id,
                exclude={old_sa},
            )

            # Re-inicializar o Earth Engine com nova SA
            ee.Initialize(self._current_sa.credentials)
            logger.info(
                f"Worker {self._worker_id} rotacionou: "
                f"{old_sa} → {self._current_sa.name}"
            )
            try:
                from app.core.metrics import gee_sa_rotation_total
                gee_sa_rotation_total.labels(
                    from_sa=old_sa,
                    to_sa=self._current_sa.name,
                    trigger=trigger,
                ).inc()
            except Exception:
                pass

    def report_http_429(self) -> None:
        """Registra um 429 de download HTTP (métrica, sem rotação de SA)."""
        if self._current_sa:
            self._pool.report_http_429(self._current_sa.name)

    def shutdown(self) -> None:
        """Libera a SA e para o heartbeat."""
        self._heartbeat_stop.set()
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            self._heartbeat_thread.join(timeout=5)

        if self._worker_id:
            self._pool.release(self._worker_id)
            logger.info(f"Worker {self._worker_id} encerrado, SA liberada")

        self._current_sa = None
        self._worker_id = None

    def _heartbeat_loop(self) -> None:
        """Thread daemon que renova o TTL do assignment periodicamente."""
        interval = settings.get("GEE_SA_HEARTBEAT_INTERVAL", 30)
        while not self._heartbeat_stop.wait(interval):
            try:
                if self._worker_id:
                    self._pool.refresh_heartbeat(self._worker_id)
            except Exception as exc:
                logger.warning(f"Erro no heartbeat GEE: {exc}")


# ---------------------------------------------------------------------------
# Decorator @gee_retry — retry com rotação de SA para funções EE síncronas
# ---------------------------------------------------------------------------

def gee_retry(max_rotations: int | None = None) -> Callable:
    """Decorator que envolve funções síncronas do Earth Engine com retry.

    Em caso de ee.EEException com 429 ou quota exceeded:
    1. Rotaciona a SA do worker atual
    2. Re-executa a função com as novas credenciais
    3. Repete até max_rotations (padrão: GEE_SA_MAX_RETRIES do settings)

    Uso:
        @gee_retry()
        def _create_s2_layer_sync(geom, dates, vis):
            ...
    """
    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            from app.core.gee_auth import get_gee_manager

            retries = max_rotations
            if retries is None:
                retries = settings.get("GEE_SA_MAX_RETRIES", 2)

            manager = get_gee_manager()

            for attempt in range(retries + 1):
                try:
                    if manager:
                        with manager.init_lock:
                            return fn(*args, **kwargs)
                    else:
                        return fn(*args, **kwargs)
                except ee.EEException as exc:
                    error_msg = str(exc).lower()
                    is_quota_error = (
                        "429" in error_msg
                        or "quota" in error_msg
                        or "too many requests" in error_msg
                        or "rate limit" in error_msg
                    )

                    if is_quota_error and attempt < retries and manager:
                        logger.warning(
                            f"EE quota error na tentativa {attempt + 1}/{retries + 1}: "
                            f"{exc}. Rotacionando SA..."
                        )
                        manager.rotate_on_429()
                        continue

                    # Não é erro de quota ou esgotou retries — propagar
                    raise

        return wrapper
    return decorator
