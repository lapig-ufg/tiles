#!/bin/bash
# ==========================================================================
# Copia dados do MongoDB de produção (porta 27018) para o MongoDB de teste
# no k3s. Copia apenas as coleções necessárias para o tile server.
#
# ATENÇÃO: Este script apenas LÊ do MongoDB de produção (dump).
#          Nunca escreve ou altera dados em produção.
#
# Uso: ./seed-mongodb.sh
# ==========================================================================

set -euo pipefail

PROD_HOST="localhost"
PROD_PORT="27018"
PROD_DB="tvi"
NAMESPACE="tiles-loadtest"
DUMP_DIR="/tmp/tiles-loadtest-mongodump"
KUBECTL="sudo k3s kubectl"

# Coleções necessárias para o tile server funcionar
COLLECTIONS=(
    "vis_params"
    "mosaics"
    "cacheConfig"
)

echo "============================================================"
echo " Seed MongoDB — Produção (porta $PROD_PORT) → Teste (k3s)"
echo "============================================================"
echo ""
echo " ATENÇÃO: Operação somente leitura na produção."
echo " Origem:  $PROD_HOST:$PROD_PORT/$PROD_DB"
echo " Destino: mongodb.$NAMESPACE.svc.cluster.local:27017/$PROD_DB"
echo ""

# ------------------------------------------------------------------
# 1. Verificar que o MongoDB de produção está acessível
# ------------------------------------------------------------------
echo "[1/4] Verificando MongoDB de produção..."

if ! mongosh --host "$PROD_HOST" --port "$PROD_PORT" --eval "db.adminCommand('ping')" --quiet &>/dev/null; then
    echo "ERRO: MongoDB de produção não acessível em $PROD_HOST:$PROD_PORT"
    exit 1
fi

echo "   Produção OK"

# ------------------------------------------------------------------
# 2. Verificar que o MongoDB de teste está rodando no k3s
# ------------------------------------------------------------------
echo "[2/4] Verificando MongoDB de teste no k3s..."

$KUBECTL wait --for=condition=ready pod -l app=mongodb-test \
    -n "$NAMESPACE" --timeout=60s 2>/dev/null || {
    echo "ERRO: Pod do MongoDB de teste não está ready"
    echo "   Execute primeiro: $KUBECTL apply -f k3s/mongodb.yaml"
    exit 1
}

TEST_POD=$($KUBECTL get pods -n "$NAMESPACE" -l app=mongodb-test \
    -o jsonpath='{.items[0].metadata.name}')
echo "   Pod de teste: $TEST_POD"

# ------------------------------------------------------------------
# 3. Dump das coleções de produção (somente leitura)
# ------------------------------------------------------------------
echo "[3/4] Fazendo dump das coleções de produção..."

rm -rf "$DUMP_DIR"
mkdir -p "$DUMP_DIR"

for col in "${COLLECTIONS[@]}"; do
    echo "   Dump: $PROD_DB.$col"
    mongodump \
        --host "$PROD_HOST" \
        --port "$PROD_PORT" \
        --db "$PROD_DB" \
        --collection "$col" \
        --out "$DUMP_DIR" \
        --quiet 2>/dev/null || {
        echo "   AVISO: Coleção $col não encontrada ou vazia — pulando"
        continue
    }

    count=$(find "$DUMP_DIR/$PROD_DB" -name "$col.bson" -exec stat --format="%s" {} \; 2>/dev/null || echo "0")
    echo "   OK ($count bytes)"
done

# Verificar que pelo menos vis_params foi exportado
if [ ! -f "$DUMP_DIR/$PROD_DB/vis_params.bson" ]; then
    echo "ERRO: Coleção vis_params não foi exportada — essencial para o tile server"
    exit 1
fi

echo "   Dump completo em $DUMP_DIR"

# ------------------------------------------------------------------
# 4. Restore no MongoDB de teste (k3s)
# ------------------------------------------------------------------
echo "[4/4] Restaurando no MongoDB de teste..."

# Copiar dump para o pod
$KUBECTL cp "$DUMP_DIR" "$NAMESPACE/$TEST_POD:/tmp/mongodump"

# Executar mongorestore dentro do pod
$KUBECTL exec -n "$NAMESPACE" "$TEST_POD" -- \
    mongorestore \
        --db "$PROD_DB" \
        --drop \
        "/tmp/mongodump/$PROD_DB" \
        --quiet 2>/dev/null

# Verificar
echo ""
echo "   Verificando dados no MongoDB de teste:"
for col in "${COLLECTIONS[@]}"; do
    count=$($KUBECTL exec -n "$NAMESPACE" "$TEST_POD" -- \
        mongosh --eval "db.getSiblingDB('$PROD_DB').$col.countDocuments()" --quiet 2>/dev/null || echo "0")
    echo "   $PROD_DB.$col: $count documentos"
done

# Limpar dump local
rm -rf "$DUMP_DIR"

echo ""
echo "============================================================"
echo " Seed concluído. MongoDB de teste pronto."
echo "============================================================"
