# 🚀 Quick Start com UV

## Instalação Rápida

```bash
# 1. Instalar UV (se ainda não tiver)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Clonar o projeto
git clone <seu-repositorio>
cd tiles

# 3. Criar ambiente e instalar dependências
uv venv
source .venv/bin/activate
uv pip install -r requirements.txt

# 4. Iniciar serviços auxiliares
docker compose -f docker-compose.services.yml up -d

# 5. Executar aplicação
./run_local_dev.sh
```

## URLs Importantes

- **API**: http://localhost:8083
- **Docs**: http://localhost:8083/docs
- **MinIO**: http://localhost:9001 (admin/admin)

## Comandos Úteis

### Verificar status dos serviços
```bash
docker ps
curl http://localhost:8083/health
```

### Parar serviços
```bash
# Parar aplicação: Ctrl+C
# Parar Redis/MinIO:
docker compose -f docker-compose.services.yml down
```

### Executar com credenciais GEE
```bash
# Adicione o arquivo de credenciais
cp /caminho/para/suas/credenciais.json .service-accounts/gee.json

# Execute o script normal
./run_local.sh
```

## Estrutura Criada

```
tiles/
├── .venv/                      # Ambiente virtual Python
├── .service-accounts/          # Credenciais GEE (não commitado)
├── app/                        # Código da aplicação
│   ├── cache_hybrid.py        # Sistema de cache otimizado
│   ├── api/
│   │   └── layers_optimized.py # Endpoints otimizados
│   └── prewarm.py             # Sistema de pre-warming
├── docker-compose.services.yml # Apenas Redis e MinIO
├── run_local.sh               # Script para produção local
└── run_local_dev.sh           # Script para desenvolvimento
```

## Benefícios do UV

- ✅ **Instalação rápida**: ~10x mais rápido que pip
- ✅ **Resolução de dependências**: Melhor que pip
- ✅ **Cache eficiente**: Reutiliza pacotes baixados
- ✅ **Compatível com pip**: Usa requirements.txt padrão

## Próximos Passos

1. **Adicionar credenciais GEE** para funcionalidade completa
2. **Configurar CDN** para produção
3. **Ajustar workers** baseado em CPUs disponíveis
4. **Executar pre-warming** para popular cache