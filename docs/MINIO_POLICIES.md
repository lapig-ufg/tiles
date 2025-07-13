# Políticas de Acesso MinIO/S3

Este documento descreve as políticas de acesso configuradas para o bucket `tiles-cache`.

## 📋 Políticas Disponíveis

### 1. **Desenvolvimento** (`minio-bucket-policy.json`)
- Permite leitura pública de tiles
- Ideal para desenvolvimento local
- Sem restrições de transporte

### 2. **Produção** (`minio-bucket-policy-production.json`)
- Acesso restrito por usuário/aplicação
- Força uso de HTTPS
- Requer encriptação para uploads
- Leitura pública condicional (por tags)

## 🚀 Configuração Rápida

### Desenvolvimento
```bash
# Configurar MinIO com política básica
./setup-minio.sh
```

### Produção
```bash
# Configurar MinIO com política restritiva e criar usuário
./setup-minio.sh production
```

## 🔒 Detalhes das Políticas

### Política de Desenvolvimento

```json
{
  "Effect": "Allow",
  "Principal": {"AWS": ["*"]},
  "Action": ["s3:GetObject"],
  "Resource": ["arn:aws:s3:::tiles-cache/tiles/*"]
}
```

**Características:**
- ✅ Leitura pública de tiles
- ✅ Aplicação pode ler/escrever
- ⚠️ Sem restrições de segurança

### Política de Produção

**1. Acesso da Aplicação:**
```json
{
  "Effect": "Allow",
  "Principal": {"AWS": ["arn:aws:iam::*:user/tiles-app"]},
  "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
  "Resource": ["arn:aws:s3:::tiles-cache/*"]
}
```

**2. Segurança:**
- 🔒 Força HTTPS para todas as operações
- 🔐 Requer encriptação AES256 para uploads
- 👤 Acesso por usuário específico

**3. Leitura Pública Condicional:**
- Tiles marcados com tag `public=true`
- Apenas arquivos `.png`

## 🌐 Configuração CORS

Aplicar CORS para permitir acesso de browsers:

```bash
./apply-cors.sh
```

**Origens permitidas:**
- `http://localhost:*` (desenvolvimento)
- `https://*.lapig.iesa.ufg.br` (produção)
- `https://lapig.iesa.ufg.br` (produção)

## 🔧 Comandos Úteis

### Verificar Política Atual
```bash
docker run --rm --network host minio/mc:latest \
  anonymous get myminio/tiles-cache
```

### Listar Objetos no Bucket
```bash
docker run --rm --network host minio/mc:latest \
  ls myminio/tiles-cache
```

### Verificar CORS
```bash
docker run --rm --network host minio/mc:latest \
  cors get myminio/tiles-cache
```

### Criar Usuário Manual
```bash
# Gerar credenciais
ACCESS_KEY=$(openssl rand -hex 16)
SECRET_KEY=$(openssl rand -hex 32)

# Criar usuário
docker run --rm --network host minio/mc:latest \
  admin user add myminio myuser $ACCESS_KEY $SECRET_KEY
```

## 🏷️ Tags de Objetos

Para marcar um tile como público em produção:

```python
# No código Python
await s3.put_object(
    Bucket=bucket,
    Key=key,
    Body=data,
    Tagging='public=true'
)
```

## 📊 Lifecycle Rules

O script configura regras de ciclo de vida:

1. **Exclusão automática**: Tiles com mais de 90 dias
2. **Transição para IA**: Tiles com mais de 30 dias movidos para Infrequent Access

## 🔐 Segurança em Produção

### Checklist de Segurança
- [ ] Usar HTTPS sempre
- [ ] Credenciais específicas por aplicação
- [ ] Rotação regular de credenciais
- [ ] Monitorar logs de acesso
- [ ] Backup regular dos metadados
- [ ] Encriptação em repouso ativada

### Variáveis de Ambiente
```bash
# .env.production (criado automaticamente)
S3_ACCESS_KEY=<gerado-automaticamente>
S3_SECRET_KEY=<gerado-automaticamente>
S3_ENDPOINT=https://s3.seu-dominio.com
S3_BUCKET=tiles-cache
```

## 📈 Monitoramento

### Métricas Importantes
- Taxa de acerto do cache
- Latência de leitura/escrita
- Uso de armazenamento
- Requisições por segundo

### Alertas Recomendados
- Uso de storage > 80%
- Latência > 100ms
- Erros de acesso > 1%
- Credenciais próximas de expirar

## 🆘 Troubleshooting

### Erro: Access Denied
```bash
# Verificar política
docker run --rm --network host minio/mc:latest \
  anonymous get-json myminio/tiles-cache

# Verificar usuário
docker run --rm --network host minio/mc:latest \
  admin user info myminio tiles-app
```

### Erro: CORS
```bash
# Reaplicar CORS
./apply-cors.sh

# Verificar headers
curl -I -H "Origin: http://localhost:3000" \
  http://localhost:9000/tiles-cache/test.png
```