#!/bin/bash
# ==========================================================================
# Monitor centralizado de pods — coleta logs de todos os 10 pods + métricas
# do pool GEE em um arquivo de log consolidado.
#
# Uso:
#   ./monitor.sh [log_file] [interval_seconds]
#
# Default:
#   ./monitor.sh /tmp/tiles-pods-monitor.log 15
# ==========================================================================

set -euo pipefail

NAMESPACE="tiles-loadtest"
LOG_FILE="${1:-/tmp/tiles-pods-monitor.log}"
INTERVAL="${2:-15}"
DEPLOYMENT="tiles-api"
# Detectar kubectl correto para k3s
if [ -f /etc/rancher/k3s/k3s.yaml ]; then
    export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
fi
KUBECTL="sudo k3s kubectl"

# Cores para terminal (não afetam arquivo)
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

log() {
    local msg="$(date '+%Y-%m-%d %H:%M:%S') $1"
    echo "$msg" >> "$LOG_FILE"
    echo -e "$msg"
}

log_section() {
    local line="$(printf '=%.0s' {1..100})"
    log "$line"
    log "$1"
    log "$line"
}

# Verificar kubectl
if ! command -v $KUBECTL &> /dev/null; then
    echo "ERRO: kubectl não encontrado"
    exit 1
fi

# Inicializar log
> "$LOG_FILE"
log_section "TILES POD MONITOR — Namespace: $NAMESPACE | Intervalo: ${INTERVAL}s"
log "Log: $LOG_FILE"
log ""

# Trap para encerramento graceful
RUNNING=true
trap 'RUNNING=false; log "Monitor encerrado pelo usuário."' INT TERM

# ===========================================================================
# Função: coletar status dos pods
# ===========================================================================
collect_pod_status() {
    log "--- STATUS DOS PODS ---"

    local pods_json
    pods_json=$($KUBECTL get pods -n "$NAMESPACE" -l app=$DEPLOYMENT \
        -o json 2>/dev/null) || {
        log "ERRO: Falha ao obter pods"
        return
    }

    local total ready not_ready restarts_total
    total=$(echo "$pods_json" | python3 -c "
import json, sys
data = json.load(sys.stdin)
items = data.get('items', [])
print(len(items))
")
    ready=0
    not_ready=0
    restarts_total=0

    echo "$pods_json" | python3 -c "
import json, sys
data = json.load(sys.stdin)
items = data.get('items', [])
ready = 0
not_ready = 0
restarts = 0
for pod in items:
    name = pod['metadata']['name']
    phase = pod['status'].get('phase', 'Unknown')
    conditions = pod['status'].get('conditions', [])
    is_ready = any(c['type'] == 'Ready' and c['status'] == 'True' for c in conditions)
    containers = pod['status'].get('containerStatuses', [])
    pod_restarts = sum(c.get('restartCount', 0) for c in containers)
    restarts += pod_restarts

    # Worker hostname (do nome do pod)
    node = pod['spec'].get('nodeName', 'N/A')
    ip = pod['status'].get('podIP', 'N/A')

    status_str = 'Ready' if is_ready else phase
    if is_ready:
        ready += 1
    else:
        not_ready += 1

    print(f'  {name:50s} | {status_str:10s} | IP={ip:15s} | restarts={pod_restarts}')

print(f'')
print(f'  Total={len(items)} | Ready={ready} | NotReady={not_ready} | Restarts={restarts}')
" >> "$LOG_FILE" 2>&1

    # Também imprimir no console
    echo "$pods_json" | python3 -c "
import json, sys
data = json.load(sys.stdin)
items = data.get('items', [])
ready = sum(1 for p in items if any(c['type'] == 'Ready' and c['status'] == 'True' for c in p['status'].get('conditions', [])))
not_ready = len(items) - ready
restarts = sum(sum(c.get('restartCount', 0) for c in p['status'].get('containerStatuses', [])) for p in items)
print(f'  Pods: {len(items)} total | {ready} ready | {not_ready} not-ready | {restarts} restarts')
"
}

# ===========================================================================
# Função: coletar métricas de recurso dos pods
# ===========================================================================
collect_resource_usage() {
    log "--- USO DE RECURSOS ---"

    $KUBECTL top pods -n "$NAMESPACE" -l app=$DEPLOYMENT 2>/dev/null \
        | tee -a "$LOG_FILE" || log "  (métricas de recurso indisponíveis — metrics-server pode não estar instalado)"
}

# ===========================================================================
# Função: coletar métricas do pool GEE via endpoint admin
# ===========================================================================
collect_gee_pool_metrics() {
    log "--- POOL GEE ---"

    # Obter IP de um pod para consulta direta
    local pod_ip
    pod_ip=$($KUBECTL get pods -n "$NAMESPACE" -l app=$DEPLOYMENT \
        -o jsonpath='{.items[0].status.podIP}' 2>/dev/null) || {
        log "  Nenhum pod disponível"
        return
    }

    # Consultar endpoint /admin/gee/pool
    local pool_data
    pool_data=$($KUBECTL exec -n "$NAMESPACE" deploy/$DEPLOYMENT -- \
        curl -s "http://localhost:8083/admin/gee/pool" 2>/dev/null) || {
        log "  Endpoint /admin/gee/pool indisponível"
        return
    }

    echo "$pool_data" | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    total = data.get('total_accounts', 0)
    accounts = data.get('accounts', {})
    total_429 = sum(a.get('errors_429', 0) for a in accounts.values())
    cooldowns = sum(1 for a in accounts.values() if a.get('in_cooldown'))
    workers = sum(a.get('active_workers', 0) for a in accounts.values())

    print(f'  SAs={total} | Workers ativos={workers} | 429 total={total_429} | Em cooldown={cooldowns}')
    print()
    for name, sa in sorted(accounts.items()):
        short = name.split('@')[0] if '@' in name else name[:35]
        cd = 'COOLDOWN' if sa.get('in_cooldown') else 'ok'
        remaining = f\"({sa.get('cooldown_remaining', 0):.0f}s)\" if sa.get('in_cooldown') else ''
        print(f'  {short:35s} | workers={sa.get(\"active_workers\", 0):3d} | reqs={sa.get(\"total_requests\", 0):6d} | 429={sa.get(\"errors_429\", 0):4d} | {cd} {remaining}')
except:
    print('  Erro ao processar dados do pool')
" >> "$LOG_FILE" 2>&1

    echo "$pool_data" | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    accounts = data.get('accounts', {})
    total_429 = sum(a.get('errors_429', 0) for a in accounts.values())
    cooldowns = sum(1 for a in accounts.values() if a.get('in_cooldown'))
    workers = sum(a.get('active_workers', 0) for a in accounts.values())
    print(f'  Pool: {len(accounts)} SAs | {workers} workers | {total_429} erros 429 | {cooldowns} em cooldown')
except:
    print('  Pool: dados indisponiveis')
"
}

# ===========================================================================
# Função: coletar worker assignments
# ===========================================================================
collect_worker_assignments() {
    log "--- WORKER ASSIGNMENTS ---"

    local assignments
    assignments=$($KUBECTL exec -n "$NAMESPACE" deploy/$DEPLOYMENT -- \
        curl -s "http://localhost:8083/admin/gee/workers" 2>/dev/null) || {
        log "  Endpoint /admin/gee/workers indisponível"
        return
    }

    echo "$assignments" | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    total = len(data)
    # Contar workers por SA
    sa_count = {}
    for wid, info in data.items():
        sa = info.get('service_account', 'unknown')
        short_sa = sa.split('@')[0] if '@' in sa else sa[:30]
        sa_count[short_sa] = sa_count.get(short_sa, 0) + 1

    print(f'  Total workers registrados: {total}')
    print(f'  Distribuição por SA:')
    for sa, count in sorted(sa_count.items()):
        bar = '#' * count
        print(f'    {sa:35s} | {count:3d} workers | {bar}')
except:
    print('  Erro ao processar assignments')
" >> "$LOG_FILE" 2>&1

    echo "$assignments" | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    print(f'  Workers: {len(data)} registrados no pool')
except:
    print('  Workers: dados indisponiveis')
"
}

# ===========================================================================
# Função: coletar logs recentes com erros
# ===========================================================================
collect_error_logs() {
    log "--- ERROS RECENTES (últimos ${INTERVAL}s) ---"

    local error_count=0
    for pod in $($KUBECTL get pods -n "$NAMESPACE" -l app=$DEPLOYMENT \
        -o jsonpath='{.items[*].metadata.name}' 2>/dev/null); do

        local errors
        errors=$($KUBECTL logs "$pod" -n "$NAMESPACE" --since="${INTERVAL}s" 2>/dev/null \
            | grep -iE '(error|429|quota|rate.limit|exception|traceback)' \
            | tail -5) || true

        if [ -n "$errors" ]; then
            log "  [$pod]:"
            echo "$errors" | while IFS= read -r line; do
                log "    $line"
            done
            error_count=$((error_count + $(echo "$errors" | wc -l)))
        fi
    done

    if [ "$error_count" -eq 0 ]; then
        log "  Nenhum erro encontrado"
    else
        log "  Total de linhas de erro: $error_count"
    fi
}

# ===========================================================================
# Loop principal
# ===========================================================================
log ""
log "Iniciando monitoramento contínuo (Ctrl+C para encerrar)..."
log ""

iteration=0
while $RUNNING; do
    iteration=$((iteration + 1))

    log_section "SNAPSHOT #$iteration — $(date '+%Y-%m-%d %H:%M:%S')"

    collect_pod_status
    echo ""
    collect_resource_usage
    echo ""
    collect_gee_pool_metrics
    echo ""
    collect_worker_assignments
    echo ""
    collect_error_logs

    log ""
    log "Próximo snapshot em ${INTERVAL}s..."
    log ""

    # Aguardar intervalo (interrompível por Ctrl+C)
    sleep "$INTERVAL" || true
done

log_section "MONITORAMENTO ENCERRADO — $iteration snapshots coletados"
log "Log completo em: $LOG_FILE"
