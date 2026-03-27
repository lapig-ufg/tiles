#!/bin/bash
# ==========================================================================
# Orquestrador do ambiente de load test no k3s.
#
# Etapas:
#   1. Build da imagem Docker
#   2. Import da imagem no k3s
#   3. Criação do namespace e recursos (Redis, MinIO, Secret)
#   4. Deploy da aplicação (10 pods)
#   5. Aguarda pods ready
#   6. Inicia monitoramento em background
#   7. Executa load test
#   8. Coleta relatório final
#
# Uso:
#   ./run-loadtest.sh [--skip-build] [--duration 300] [--concurrency 50]
#
# Pré-requisitos:
#   - k3s instalado e rodando
#   - kubectl configurado
#   - Docker ou buildah disponível
#   - Service accounts em .service-accounts/
# ==========================================================================

set -euo pipefail

# Diretório base do projeto
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
K3S_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Configurações
NAMESPACE="tiles-loadtest"
IMAGE_NAME="tiles-loadtest"
IMAGE_TAG="latest"
# Detectar kubectl correto para k3s
if [ -f /etc/rancher/k3s/k3s.yaml ]; then
    export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
fi
KUBECTL="sudo k3s kubectl"

# Parâmetros de loadtest
DURATION=300
CONCURRENCY=50
SKIP_BUILD=false
LOG_DIR="/tmp/tiles-loadtest-$(date +%Y%m%d-%H%M%S)"

# Parse de argumentos
while [[ $# -gt 0 ]]; do
    case $1 in
        --skip-build) SKIP_BUILD=true; shift ;;
        --duration) DURATION="$2"; shift 2 ;;
        --concurrency) CONCURRENCY="$2"; shift 2 ;;
        --log-dir) LOG_DIR="$2"; shift 2 ;;
        *) echo "Argumento desconhecido: $1"; exit 1 ;;
    esac
done

# Criar diretório de logs
mkdir -p "$LOG_DIR"

echo "============================================================"
echo " TILES LOAD TEST — k3s com 10 pods"
echo "============================================================"
echo " Projeto:      $PROJECT_DIR"
echo " Namespace:    $NAMESPACE"
echo " Duração:      ${DURATION}s"
echo " Concorrência: $CONCURRENCY workers"
echo " Logs:         $LOG_DIR"
echo "============================================================"
echo ""

# ===========================================================================
# 1. Build da imagem Docker
# ===========================================================================
if [ "$SKIP_BUILD" = false ]; then
    echo "[1/7] Construindo imagem Docker..."

    # Usar docker se disponível, senão buildah/nerdctl
    if command -v docker &> /dev/null; then
        BUILD_CMD="docker build"
    elif command -v nerdctl &> /dev/null; then
        BUILD_CMD="nerdctl build"
    else
        echo "ERRO: Nenhum container builder encontrado (docker/nerdctl)"
        exit 1
    fi

    $BUILD_CMD -t "$IMAGE_NAME:$IMAGE_TAG" \
        -f "$PROJECT_DIR/docker/prod/Dockerfile" \
        "$PROJECT_DIR" 2>&1 | tee "$LOG_DIR/build.log"

    echo "   Imagem construída: $IMAGE_NAME:$IMAGE_TAG"
else
    echo "[1/7] Build ignorado (--skip-build)"
fi

# ===========================================================================
# 2. Importar imagem no k3s
# ===========================================================================
echo ""
echo "[2/7] Importando imagem no k3s..."

if command -v docker &> /dev/null; then
    docker save "$IMAGE_NAME:$IMAGE_TAG" | sudo k3s ctr images import - 2>&1
elif command -v nerdctl &> /dev/null; then
    nerdctl save "$IMAGE_NAME:$IMAGE_TAG" | sudo k3s ctr images import - 2>&1
fi

echo "   Imagem importada no k3s"

# ===========================================================================
# 3. Criar namespace e recursos
# ===========================================================================
echo ""
echo "[3/7] Criando namespace e infraestrutura..."

# Namespace
$KUBECTL apply -f "$K3S_DIR/namespace.yaml"

# Secret com service accounts
echo "   Criando secret com service accounts..."
bash "$K3S_DIR/create-sa-secret.sh" "$PROJECT_DIR/.service-accounts" 2>&1 | \
    tee "$LOG_DIR/secret-creation.log"

# Redis + MinIO + MongoDB + ConfigMap
$KUBECTL apply -f "$K3S_DIR/redis.yaml"
$KUBECTL apply -f "$K3S_DIR/minio.yaml"
$KUBECTL apply -f "$K3S_DIR/mongodb.yaml"
$KUBECTL apply -f "$K3S_DIR/configmap.yaml"

echo "   Aguardando Redis, MinIO e MongoDB ficarem ready..."
$KUBECTL wait --for=condition=ready pod -l app=valkey -n "$NAMESPACE" --timeout=60s
$KUBECTL wait --for=condition=ready pod -l app=minio -n "$NAMESPACE" --timeout=60s
$KUBECTL wait --for=condition=ready pod -l app=mongodb-test -n "$NAMESPACE" --timeout=90s

# Copiar dados de produção para MongoDB de teste
echo "   Copiando dados do MongoDB de produção para teste..."
bash "$SCRIPT_DIR/seed-mongodb.sh" 2>&1 | tee "$LOG_DIR/seed-mongodb.log"

echo "   Infraestrutura pronta"

# ===========================================================================
# 4. Deploy da aplicação
# ===========================================================================
echo ""
echo "[4/7] Fazendo deploy da aplicação (10 pods)..."

$KUBECTL apply -f "$K3S_DIR/deployment.yaml"

# ===========================================================================
# 5. Aguardar pods ready
# ===========================================================================
echo ""
echo "[5/7] Aguardando pods ficarem ready (pode levar até 3 min)..."

# Aguardar que pelo menos 8 de 10 pods estejam ready (tolerância para startup lento)
MAX_WAIT=180
WAIT=0
while [ $WAIT -lt $MAX_WAIT ]; do
    READY=$($KUBECTL get pods -n "$NAMESPACE" -l app=tiles-api \
        -o jsonpath='{range .items[*]}{.status.conditions[?(@.type=="Ready")].status}{"\n"}{end}' \
        2>/dev/null | grep -c "True" || echo "0")

    echo "   Pods ready: $READY / 10 (${WAIT}s / ${MAX_WAIT}s)"

    if [ "$READY" -ge 8 ]; then
        echo "   Mínimo de pods ready atingido ($READY/10)"
        break
    fi

    sleep 10
    WAIT=$((WAIT + 10))
done

if [ "$READY" -lt 5 ]; then
    echo "ERRO: Menos de 5 pods ficaram ready. Verificando logs..."
    $KUBECTL get pods -n "$NAMESPACE" -l app=tiles-api
    echo ""
    echo "Logs do primeiro pod com problema:"
    PROBLEM_POD=$($KUBECTL get pods -n "$NAMESPACE" -l app=tiles-api \
        --field-selector=status.phase!=Running \
        -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")
    if [ -n "$PROBLEM_POD" ]; then
        $KUBECTL logs "$PROBLEM_POD" -n "$NAMESPACE" --tail=30
    fi
    exit 1
fi

# Mostrar estado dos pods
echo ""
$KUBECTL get pods -n "$NAMESPACE" -l app=tiles-api -o wide

# ===========================================================================
# 6. Port-forward + Iniciar monitoramento em background
# ===========================================================================
echo ""
echo "[6/7] Configurando acesso e iniciando monitoramento..."

# Port-forward para acesso local
$KUBECTL port-forward -n "$NAMESPACE" svc/tiles-api 8083:80 &
PF_PID=$!
sleep 2

# Verificar que o port-forward está funcionando
if ! curl -sf http://localhost:8083/health/light > /dev/null 2>&1; then
    echo "AVISO: Port-forward pode não estar pronto. Aguardando mais 5s..."
    sleep 5
fi

# Iniciar monitor em background
bash "$SCRIPT_DIR/monitor.sh" "$LOG_DIR/pods-monitor.log" 15 &
MONITOR_PID=$!

echo "   Port-forward PID: $PF_PID"
echo "   Monitor PID: $MONITOR_PID"

# Cleanup no exit
cleanup() {
    echo ""
    echo "Encerrando processos auxiliares..."
    kill "$PF_PID" 2>/dev/null || true
    kill "$MONITOR_PID" 2>/dev/null || true
    wait "$PF_PID" 2>/dev/null || true
    wait "$MONITOR_PID" 2>/dev/null || true
}
trap cleanup EXIT

# ===========================================================================
# 7. Executar load test
# ===========================================================================
echo ""
echo "[7/7] Iniciando load test..."
echo "      Duração: ${DURATION}s | Concorrência: ${CONCURRENCY} workers"
echo "      Log: $LOG_DIR/loadtest.log"
echo ""

python3 "$SCRIPT_DIR/loadtest.py" \
    --base-url http://localhost:8083 \
    --concurrency "$CONCURRENCY" \
    --duration "$DURATION" \
    --log-file "$LOG_DIR/loadtest.log"

# ===========================================================================
# Relatório final
# ===========================================================================
echo ""
echo "============================================================"
echo " LOAD TEST CONCLUÍDO"
echo "============================================================"
echo ""
echo " Arquivos gerados em: $LOG_DIR"
echo ""
ls -lh "$LOG_DIR"
echo ""
echo " Para visualizar:"
echo "   Logs do loadtest:    less $LOG_DIR/loadtest.log"
echo "   Relatório JSON:      cat $LOG_DIR/loadtest-report.json | python3 -m json.tool"
echo "   Logs dos pods:       less $LOG_DIR/pods-monitor.log"
echo ""
echo " Para manter o ambiente rodando e inspecionar:"
echo "   kubectl get pods -n $NAMESPACE"
echo "   kubectl logs -f <pod-name> -n $NAMESPACE"
echo "   curl http://localhost:8083/admin/gee/pool | python3 -m json.tool"
echo "   curl http://localhost:8083/admin/gee/workers | python3 -m json.tool"
echo ""
echo " Para destruir o ambiente:"
echo "   kubectl delete namespace $NAMESPACE"
echo "============================================================"
