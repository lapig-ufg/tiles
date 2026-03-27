#!/bin/bash
# Cria um Kubernetes Secret com todas as service accounts do GEE.
# Cada arquivo .json no diretório .service-accounts/ é adicionado como uma chave.
#
# Uso: ./create-sa-secret.sh [diretório_de_SAs]

set -euo pipefail

# Detectar kubectl para k3s
if [ -f /etc/rancher/k3s/k3s.yaml ]; then
    export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
fi
KUBECTL="sudo k3s kubectl"

SA_DIR="${1:-$(dirname "$0")/../.service-accounts}"
NAMESPACE="tiles-loadtest"
SECRET_NAME="gee-service-accounts"

if [ ! -d "$SA_DIR" ]; then
    echo "ERRO: Diretório de service accounts não encontrado: $SA_DIR"
    exit 1
fi

# Contar arquivos JSON válidos (excluindo .example)
JSON_FILES=$(find "$SA_DIR" -name '*.json' ! -name '*.example' -type f | sort)
COUNT=$(echo "$JSON_FILES" | grep -c . || true)

if [ "$COUNT" -eq 0 ]; then
    echo "ERRO: Nenhum arquivo .json encontrado em $SA_DIR"
    exit 1
fi

echo "Encontradas $COUNT service accounts em $SA_DIR"

# Montar argumentos --from-file
FROM_FILE_ARGS=""
while IFS= read -r f; do
    basename=$(basename "$f")
    FROM_FILE_ARGS="$FROM_FILE_ARGS --from-file=$basename=$f"
    echo "  + $basename"
done <<< "$JSON_FILES"

# Criar namespace se não existir
$KUBECTL create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -

# Deletar secret existente (se houver) e recriar
$KUBECTL delete secret "$SECRET_NAME" -n "$NAMESPACE" 2>/dev/null || true

eval $KUBECTL create secret generic "$SECRET_NAME" \
    -n "$NAMESPACE" \
    $FROM_FILE_ARGS

echo ""
echo "Secret '$SECRET_NAME' criado no namespace '$NAMESPACE' com $COUNT service accounts."
echo "Verificação:"
$KUBECTL get secret "$SECRET_NAME" -n "$NAMESPACE" -o jsonpath='{.data}' | python3 -c "
import json, sys
data = json.load(sys.stdin)
for key in sorted(data.keys()):
    print(f'  - {key}')
"
