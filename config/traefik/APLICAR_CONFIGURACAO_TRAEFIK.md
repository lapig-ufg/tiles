# 🚀 Guia de Aplicação da Configuração Otimizada do Traefik

## 📁 Arquivo Criado
- **Localização**: `/home/tharles/projects_lapig/tiles/traefik-optimized.yml`
- **Propósito**: Configuração otimizada para 5 instâncias de tiles com rate limiting aumentado

## 📋 Passos para Aplicar

### 1. Fazer Backup da Configuração Atual
```bash
cd /path/to/traefik
cp traefik.yml traefik.yml.backup-$(date +%Y%m%d-%H%M%S)
```

### 2. Substituir a Configuração
```bash
# Copiar o novo arquivo para o diretório do Traefik
cp /home/tharles/projects_lapig/tiles/traefik-optimized.yml ./traefik.yml
```

### 3. Validar a Configuração
```bash
# Verificar sintaxe YAML
docker run --rm -v $(pwd)/traefik.yml:/traefik.yml alpine/yq eval '.' /traefik.yml
```

### 4. Reiniciar o Traefik
```bash
# Reiniciar o container
docker restart traefik

# Ou usando docker-compose
docker-compose restart traefik
```

### 5. Verificar os Logs
```bash
# Acompanhar logs em tempo real
docker logs -f traefik --tail 100
```

## 🔍 Verificação Pós-Aplicação

### 1. Testar Rate Limiting
```bash
# Testar limite de requisições
for i in {1..100}; do
  curl -s -o /dev/null -w "%{http_code}\n" https://tiles.lapig.iesa.ufg.br/health
done | sort | uniq -c
```

### 2. Verificar Balanceamento
```bash
# Verificar distribuição entre instâncias
curl -I https://tiles.lapig.iesa.ufg.br/api/layers/s2_harmonized/10/10/10
# Observar header X-Served-By ou similar
```

### 3. Monitorar Métricas
- Dashboard Traefik: `http://localhost:9012`
- Verificar rate limit headers nas respostas:
  - `X-RateLimit-Limit`
  - `X-RateLimit-Remaining`
  - `X-RateLimit-Reset`

## ⚙️ Configurações Aplicadas

### Rate Limiting
- **Global**: 1000 req/s (burst: 2500)
- **Tiles**: 2500 req/s (burst: 5000)
- **Por Instância**: ~500 req/s

### Load Balancing
- **5 instâncias** balanceadas
- **Sticky sessions** habilitadas
- **Health checks** a cada 10s

### Cache
- **TTL**: 30 dias para tiles
- **Compressão**: Habilitada
- **Headers otimizados**

## 🚨 Troubleshooting

### Se houver erros:
1. Verificar sintaxe YAML
2. Confirmar que todas as 5 instâncias estão rodando
3. Verificar conectividade entre Traefik e instâncias
4. Revisar logs: `docker logs traefik`

### Rollback se necessário:
```bash
cp traefik.yml.backup-* traefik.yml
docker restart traefik
```

## 📊 Capacidade Esperada
- **Requests/segundo**: 2500
- **Requests/minuto**: 150.000
- **Requests/hora**: 9.000.000

Com essa configuração, o erro "Too Many Requests" deve ser eliminado!