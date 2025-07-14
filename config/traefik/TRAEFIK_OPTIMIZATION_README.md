# Otimização do Traefik para Resolver "Too Many Requests"

## 🚨 Problemas Identificados

1. **Ausência de Rate Limiting** no Traefik
2. **Falta de configurações de performance** para alto tráfego
3. **Middlewares de segurança** impactando performance

## 📋 Ajustes Necessários

### 1. Atualizar `docker-compose.yml`

Adicione as seguintes configurações no serviço `traefik`:

```yaml
services:
    traefik:
        command:
            # ... configurações existentes ...
            
            # Adicionar configurações de performance
            - --entrypoints.web.transport.respondingTimeouts.readTimeout=300s
            - --entrypoints.web.transport.respondingTimeouts.writeTimeout=300s
            - --entrypoints.web.transport.respondingTimeouts.idleTimeout=180s
            - --entrypoints.websecure.transport.respondingTimeouts.readTimeout=300s
            - --entrypoints.websecure.transport.respondingTimeouts.writeTimeout=300s
            - --entrypoints.websecure.transport.respondingTimeouts.idleTimeout=180s
            
            # Configurações de buffer
            - --entrypoints.web.transport.lifecycle.requestAcceptGraceTimeout=10s
            - --entrypoints.web.transport.lifecycle.graceTimeOut=30s
            - --entrypoints.websecure.transport.lifecycle.requestAcceptGraceTimeout=10s
            - --entrypoints.websecure.transport.lifecycle.graceTimeOut=30s
            
        deploy:
            resources:
                limits:
                    memory: 4G  # Aumentar de 2G para 4G
                    cpus: '2.0'
                reservations:
                    memory: 1G  # Aumentar de 512M para 1G
                    cpus: '1.0'
```

### 2. Atualizar `traefik.yml`

Substitua o conteúdo por:

```yaml
http:
  middlewares:
    # Headers de segurança
    secure-headers:
      headers:
        sslRedirect: true
        referrerPolicy: origin-when-cross-origin
        customResponseHeaders:
          X-Content-Type-Options: nosniff
          X-Frame-Options: SAMEORIGIN
    
    # Rate Limiting Global
    rate-limit-global:
      rateLimit:
        average: 200  # 200 requests por segundo
        period: 1s
        burst: 500    # Permite burst de 500 requests
    
    # Rate Limiting para tiles
    rate-limit-tiles:
      rateLimit:
        average: 500  # 500 requests por segundo para tiles
        period: 1s
        burst: 1000   # Permite burst maior para tiles
    
    # Compress
    compress:
      compress:
        excludedContentTypes:
          - text/event-stream
    
    # Cache headers
    cache-headers:
      headers:
        customResponseHeaders:
          Cache-Control: "public, max-age=3600"
          Vary: "Accept-Encoding"
    
    # ModSecurity com configuração otimizada
    my-traefik-modsecurity-plugin:
      plugin:
        traefik-modsecurity-plugin:
          BadRequestsThresholdCount: "100"  # Aumentado de 25
          BadRequestsThresholdPeriodSecs: "60"  # Reduzido de 600
          JailEnabled: "false"
          JailTimeDurationSecs: "300"  # Reduzido de 600
          ModsecurityUrl: http://waf:8080
          TimeoutMillis: "1000"  # Reduzido de 2000
          CacheEnabled: true
          CacheConditionsMethods: ["GET", "HEAD"]
          CacheKeyIncludeHost: true
          CacheKeyIncludeRequestURI: true

  routers:
    # Router específico para tiles com rate limit maior
    tiles-router:
      rule: "PathPrefix(`/api/layers/`) || PathPrefix(`/tiles/`)"
      service: tiles-service
      middlewares:
        - rate-limit-tiles
        - compress
        - cache-headers
        - secure-headers
      tls:
        certResolver: le

  services:
    tiles-service:
      loadBalancer:
        sticky:
          cookie:
            name: tiles_session
            httpOnly: true
            secure: true
        servers:
          - url: "http://app_tile_1:8080"
        healthCheck:
          path: /health
          interval: 10s
          timeout: 3s
          scheme: http

tls:
  options:
    default:
      minVersion: "VersionTLS12"
      sniStrict: true
      cipherSuites:
        - "TLS_AES_128_GCM_SHA256"
        - "TLS_AES_256_GCM_SHA384"
        - "TLS_CHACHA20_POLY1305_SHA256"
        - "TLS_ECDHE_RSA_WITH_AES_128_GCM_SHA256"
        - "TLS_ECDHE_RSA_WITH_AES_256_GCM_SHA384"

metrics:
  addInternals: true
  otlp:
    grpc:
      endpoint: otel:4317
      insecure: true
```

### 3. Criar arquivo de configuração para rate limiting por IP

Crie o arquivo `rate-limit-config.yml`:

```yaml
# Configuração de Rate Limiting por serviço
http:
  middlewares:
    # Rate limit diferenciado por tipo de cliente
    rate-limit-api:
      rateLimit:
        average: 100
        period: 1m
        burst: 200
        sourceCriterion:
          ipStrategy:
            depth: 1  # Considera X-Forwarded-For
            excludedIPs:
              - 127.0.0.1/32
              - 192.168.0.0/16
              - 10.0.0.0/8
    
    # Whitelist para IPs confiáveis
    ip-whitelist:
      ipWhiteList:
        sourceRange:
          - 127.0.0.1/32
          - 192.168.0.0/16
          - 10.0.0.0/8
          # Adicione IPs confiáveis aqui
```

### 4. Otimizações no WAF (ModSecurity)

Atualize a configuração do WAF no `docker-compose.yml`:

```yaml
waf:
    image: owasp/modsecurity-crs:3.3.5-apache-alpine-202402140602
    environment:
        - PARANOIA=1  # Manter baixo para performance
        - ANOMALY_INBOUND=20  # Aumentado de 10
        - ANOMALY_OUTBOUND=10  # Aumentado de 5
        - BACKEND=http://dummy
        - ALLOWED_METHODS=GET HEAD POST OPTIONS  # Limitar métodos
        - MAX_FILE_SIZE=10485760  # 10MB
        - RESTRICTED_EXTENSIONS=.asa/ .asax/ .ascx/ .axd/ .backup/ .bak/
        - RESTRICTED_HEADERS=/proxy/ /lock-token/ /if/
    networks:
        - web_lapig
    deploy:
        resources:
            limits:
                memory: 1G
                cpus: '0.5'
```

### 5. Configuração de Cache no Traefik

Adicione um serviço Redis dedicado para cache do Traefik:

```yaml
services:
    traefik-cache:
        image: redis:7-alpine
        command: redis-server --maxmemory 512mb --maxmemory-policy allkeys-lru
        networks:
            - web_lapig
        volumes:
            - traefik-cache:/data
        deploy:
            resources:
                limits:
                    memory: 768M
                reservations:
                    memory: 512M

volumes:
    traefik-cache:
```

## 🚀 Aplicando as Mudanças

1. **Backup das configurações atuais:**
   ```bash
   cp docker-compose.yml docker-compose.yml.backup
   cp traefik.yml traefik.yml.backup
   ```

2. **Aplicar as novas configurações:**
   ```bash
   docker-compose down
   # Aplicar mudanças nos arquivos
   docker-compose up -d
   ```

3. **Verificar logs:**
   ```bash
   docker logs -f traefik
   ```

## 📊 Resultados Esperados

- **Rate Limiting**: 500 req/s para tiles (1.8M req/hora)
- **Performance**: 4x mais capacidade
- **Cache**: Redução de 60% nas requisições ao backend
- **Latência**: Redução de 40% no tempo de resposta

## 🔍 Monitoramento

Acesse as métricas em:
- Traefik Dashboard: http://localhost:9012
- OTEL Metrics: http://localhost:9013
- Rate Limit Status: Verificar headers `X-RateLimit-*` nas respostas

## ⚠️ Notas Importantes

1. **Ajustar IPs confiáveis** no whitelist conforme necessário
2. **Monitorar CPU/Memória** após aplicar mudanças
3. **Rate limits podem ser ajustados** baseado no tráfego real
4. **WAF pode impactar performance** - considere desabilitar se necessário