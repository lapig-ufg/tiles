#!/bin/bash
# Script para configurar MinIO com políticas de acesso

set -e

# Cores para output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Configurações
MINIO_ENDPOINT=${MINIO_ENDPOINT:-http://localhost:9000}
MINIO_ACCESS_KEY=${MINIO_ACCESS_KEY:-minioadmin}
MINIO_SECRET_KEY=${MINIO_SECRET_KEY:-minioadmin}
BUCKET_NAME=${BUCKET_NAME:-tiles-cache}

echo -e "${GREEN}Configurando MinIO...${NC}"

# Verifica se MinIO está rodando
if ! curl -s "${MINIO_ENDPOINT}/minio/health/live" > /dev/null; then
    echo -e "${RED}❌ MinIO não está acessível em ${MINIO_ENDPOINT}${NC}"
    echo "Execute: docker compose -f docker-compose.services.yml up -d"
    exit 1
fi

# Configura alias do MinIO Client
echo -e "${YELLOW}Configurando MinIO Client...${NC}"
docker run --rm \
    --network host \
    -v $(dirname "$0")/../config:/config \
    minio/mc:latest \
    alias set myminio ${MINIO_ENDPOINT} ${MINIO_ACCESS_KEY} ${MINIO_SECRET_KEY}

# Cria bucket se não existir
echo -e "${YELLOW}Criando bucket ${BUCKET_NAME}...${NC}"
docker run --rm \
    --network host \
    minio/mc:latest \
    mb --ignore-existing myminio/${BUCKET_NAME}

# Aplica política de bucket
echo -e "${YELLOW}Aplicando política de acesso...${NC}"
docker run --rm \
    --network host \
    -v $(dirname "$0")/../config:/config \
    minio/mc:latest \
    admin policy create myminio bucket-policy /config/minio/minio-bucket-policy.json

# Aplica política anônima para leitura pública
docker run --rm \
    --network host \
    minio/mc:latest \
    anonymous set download myminio/${BUCKET_NAME}/tiles/

# Configura lifecycle para limpeza automática (opcional)
echo -e "${YELLOW}Configurando lifecycle...${NC}"
cat > /tmp/lifecycle.json << EOF
{
    "Rules": [
        {
            "ID": "DeleteOldTiles",
            "Status": "Enabled",
            "Expiration": {
                "Days": 90
            },
            "Filter": {
                "Prefix": "tiles/"
            }
        },
        {
            "ID": "TransitionToInfrequentAccess",
            "Status": "Enabled",
            "Transitions": [
                {
                    "Days": 30,
                    "StorageClass": "STANDARD_IA"
                }
            ],
            "Filter": {
                "Prefix": "tiles/"
            }
        }
    ]
}
EOF

docker run --rm \
    --network host \
    -v /tmp:/config \
    minio/mc:latest \
    ilm import myminio/${BUCKET_NAME} < /config/lifecycle.json || true

# Cria usuário específico para aplicação (produção)
if [ "$1" = "production" ]; then
    echo -e "${YELLOW}Criando usuário de aplicação...${NC}"
    
    # Gera credenciais aleatórias
    APP_ACCESS_KEY=$(openssl rand -hex 16)
    APP_SECRET_KEY=$(openssl rand -hex 32)
    
    # Cria usuário
    docker run --rm \
        --network host \
        minio/mc:latest \
        admin user add myminio tiles-app ${APP_ACCESS_KEY} ${APP_SECRET_KEY}
    
    # Cria política específica
    cat > /tmp/app-policy.json << EOF
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "s3:GetObject",
                "s3:PutObject",
                "s3:DeleteObject",
                "s3:ListBucket"
            ],
            "Resource": [
                "arn:aws:s3:::${BUCKET_NAME}/*",
                "arn:aws:s3:::${BUCKET_NAME}"
            ]
        }
    ]
}
EOF
    
    docker run --rm \
        --network host \
        -v /tmp:/config \
        minio/mc:latest \
        admin policy create myminio tiles-app-policy /config/app-policy.json
    
    # Aplica política ao usuário
    docker run --rm \
        --network host \
        minio/mc:latest \
        admin policy attach myminio tiles-app-policy --user tiles-app
    
    echo -e "${GREEN}✅ Usuário de aplicação criado!${NC}"
    echo -e "${YELLOW}Credenciais (salve em local seguro):${NC}"
    echo "S3_ACCESS_KEY=${APP_ACCESS_KEY}"
    echo "S3_SECRET_KEY=${APP_SECRET_KEY}"
    
    # Salva credenciais em arquivo
    cat > .env.production << EOF
# Credenciais MinIO para produção
S3_ACCESS_KEY=${APP_ACCESS_KEY}
S3_SECRET_KEY=${APP_SECRET_KEY}
S3_ENDPOINT=${MINIO_ENDPOINT}
S3_BUCKET=${BUCKET_NAME}
EOF
    chmod 600 .env.production
    echo -e "${GREEN}Credenciais salvas em .env.production${NC}"
fi

# Verifica configuração
echo -e "\n${GREEN}✅ MinIO configurado com sucesso!${NC}"
echo -e "${YELLOW}Status do bucket:${NC}"
docker run --rm \
    --network host \
    minio/mc:latest \
    ls myminio/${BUCKET_NAME}

echo -e "\n${YELLOW}Política aplicada:${NC}"
docker run --rm \
    --network host \
    minio/mc:latest \
    anonymous get myminio/${BUCKET_NAME}

echo -e "\n${GREEN}Para acessar o console MinIO:${NC}"
echo "URL: ${MINIO_ENDPOINT/9000/9001}"
echo "Login: ${MINIO_ACCESS_KEY} / ${MINIO_SECRET_KEY}"