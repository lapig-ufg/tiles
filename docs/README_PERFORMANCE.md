# Tiles API - Guia de Alta Performance

## 🚀 Otimizações Implementadas

### 1. **Cache Híbrido de Alta Performance**
- **Redis/Valkey**: Metadados e controle (rápido, pequeno)
- **S3/MinIO**: Armazenamento de PNGs (escalável, barato)
- **Cache Local**: Hot tiles em memória (ultra-rápido)
- **TTL Otimizado**: 30 dias para tiles, 7 dias para metadados

### 2. **Arquitetura de Cache Multi-Camada**
```
[CDN] → [Nginx Cache] → [App] → [Cache Híbrido]
                                    ↓
                            [Redis] + [S3/MinIO]
```

### 3. **Rate Limiting e Proteções**
- Rate limiting por IP: 1000 req/min
- Proteção contra DDoS
- Métricas customizáveis (temporariamente desabilitadas)

### 4. **Pre-warming de Tiles**
- Aquecimento automático de regiões populares
- Reduz latência inicial para usuários

## 📊 Configurações para Milhões de Requisições/Segundo

### Docker Compose Otimizado
```bash
# Iniciar todos os serviços
docker-compose up -d

# Monitoramento pode ser adicionado posteriormente
```

### Variáveis de Ambiente Importantes
```bash
# Performance
WORKERS=64                    # Ajustar baseado em CPUs
WORKER_CONNECTIONS=2000       # Conexões por worker
MAX_REQUESTS=10000           # Requisições antes de restart
MAX_REQUESTS_JITTER=1000     # Variação aleatória

# Cache
REDIS_URL=redis://valkey:6379
S3_ENDPOINT=http://minio:9000
S3_ACCESS_KEY=minioadmin
S3_SECRET_KEY=minioadmin

# Rate Limiting
RATE_LIMIT_PER_MINUTE=1000
RATE_LIMIT_BURST=100
```

## 🔧 Tunning do Sistema Operacional

### 1. Limites do Sistema
```bash
# /etc/security/limits.conf
* soft nofile 65535
* hard nofile 65535
* soft nproc 32768
* hard nproc 32768
```

### 2. Kernel Parameters
```bash
# /etc/sysctl.conf
net.core.somaxconn = 65535
net.ipv4.tcp_max_syn_backlog = 65535
net.ipv4.ip_local_port_range = 1024 65535
net.ipv4.tcp_tw_reuse = 1
net.ipv4.tcp_fin_timeout = 15
net.core.netdev_max_backlog = 65535
```

## 📈 Monitoramento

### Métricas Importantes (via headers HTTP)
- **Cache Hit Rate**: Deve ser > 95% (via X-Cache-Status)
- **Response Time p99**: Deve ser < 100ms (via X-Response-Time)
- **Error Rate**: Deve ser < 0.1% (status codes)

### Análise de Performance
```bash
# Verificar headers de resposta
curl -I http://localhost/api/layers/landsat/1000/2000/12

# Análise com jq
curl -s http://localhost/api/cache/stats | jq .
```

## 🚦 Pre-warming de Tiles

### Executar Pre-warming Manual
```bash
# Aquecer regiões populares
docker exec tile-local python -m app.prewarm popular

# Aquecer região customizada
docker exec tile-local python -m app.prewarm custom landsat -50 -20 -45 -15 10,11,12
```

### Pre-warming Automático
O sistema executa pre-warming periódico a cada 6 horas para regiões populares.

## 🔍 Troubleshooting

### Verificar Status do Cache
```bash
# Status do Redis
docker exec valkey redis-cli info stats

# Status do MinIO
docker exec minio mc admin info minio
```

### Logs
```bash
# Logs da aplicação
docker logs tile-local -f

# Logs do Nginx
docker logs nginx-cache -f
```

## 🎯 Benchmarking

### Teste de Carga com wrk
```bash
# Instalar wrk
apt-get install wrk

# Teste simples
wrk -t12 -c400 -d30s http://localhost/api/layers/landsat/1000/2000/12

# Teste com script Lua para múltiplas URLs
wrk -t12 -c400 -d30s -s benchmark.lua http://localhost
```

### Resultados Esperados
- **Throughput**: > 50.000 req/s por servidor
- **Latência p50**: < 10ms
- **Latência p99**: < 100ms

## 🛡️ Segurança

1. **Configure hosts confiáveis** em produção
2. **Use HTTPS** com certificados válidos
3. **Configure firewall** para portas expostas
4. **Monitore logs** de acesso para anomalias
5. **Atualize dependências** regularmente

## 📋 Checklist de Deploy

- [ ] Ajustar número de workers baseado em CPUs
- [ ] Configurar CDN (CloudFlare/CloudFront)
- [ ] Configurar backups do MinIO
- [ ] Configurar sistema de monitoramento
- [ ] Testar failover e recuperação
- [ ] Executar pre-warming inicial
- [ ] Verificar métricas após deploy
- [ ] Documentar configurações específicas do ambiente