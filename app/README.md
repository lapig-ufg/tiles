# Estrutura Modular do App

Esta é a nova estrutura modular do aplicativo Tiles API, organizada para melhor manutenibilidade e clareza.

## Estrutura de Diretórios

```
app/
├── __init__.py
├── README.md
│
├── api/                    # Endpoints da API
│   ├── cache_endpoints.py  # Endpoints de cache
│   ├── cache_management.py # Gerenciamento de cache
│   ├── cache_unified.py    # Cache unificado
│   ├── layers.py          # Endpoints de layers
│   ├── layers_optimized.py # Layers otimizados
│   ├── tile_aggregation.py # Agregação de tiles
│   ├── timeseries.py      # Séries temporais
│   └── vis_params_management.py # Gerenciamento de parâmetros de visualização
│
├── cache/                 # Módulo de cache
│   ├── __init__.py
│   ├── cache.py          # Cache Redis básico
│   ├── cache_hybrid.py   # Cache híbrido (Redis + S3 + Memory)
│   └── cache_warmer.py   # Aquecedor de cache
│
├── core/                  # Módulo core - configurações e utilidades básicas
│   ├── __init__.py
│   ├── auth.py           # Autenticação e autorização
│   ├── config.py         # Configurações da aplicação
│   ├── database.py       # Conexão com banco de dados SQL
│   ├── errors.py         # Tratamento de erros
│   └── mongodb.py        # Conexão com MongoDB
│
├── middleware/            # Middleware e limitadores
│   ├── __init__.py
│   ├── adaptive_limiter.py # Limitador adaptativo
│   └── rate_limiter.py    # Rate limiter
│
├── models/                # Modelos de dados
│   ├── __init__.py
│   ├── models.py         # Modelos SQL (mantido na raiz por enquanto)
│   └── vis_params.py     # Modelos de parâmetros de visualização
│
├── services/              # Serviços e lógica de negócio
│   ├── __init__.py
│   ├── batch_processor.py # Processamento em lote
│   ├── prewarm.py        # Serviço de pré-aquecimento
│   ├── repository.py     # Repositório de dados
│   ├── request_queue.py  # Fila de requisições
│   └── tile.py          # Geração de tiles
│
├── tasks/                 # Tarefas assíncronas (Celery)
│   ├── __init__.py
│   ├── cache_tasks.py    # Tarefas de cache
│   ├── celery_app.py     # Configuração do Celery
│   └── tasks.py          # Tarefas gerais
│
├── utils/                 # Utilidades diversas
│   ├── cache.py          # Utilidades de cache
│   ├── capabilities.py   # Capacidades da API
│   ├── cors.py          # Configurações CORS
│   └── process_timeseries.py # Processamento de séries temporais
│
├── visualization/         # Parâmetros de visualização
│   ├── __init__.py
│   ├── visParam.py       # Parâmetros hardcoded
│   ├── vis_params_db.py  # Gerenciador de parâmetros do MongoDB
│   └── vis_params_loader.py # Carregador de parâmetros
│
└── router.py             # Roteamento principal

```

## Descrição dos Módulos

### api/
Contém todos os endpoints da API REST. Cada arquivo representa um conjunto de endpoints relacionados.

### cache/
Sistema de cache em camadas com suporte para Redis, S3 e memória local.

### core/
Funcionalidades essenciais como configuração, autenticação, conexões de banco de dados e tratamento de erros.

### middleware/
Middleware para rate limiting e outras funcionalidades de processamento de requisições.

### models/
Definições de modelos de dados tanto para SQL quanto para MongoDB.

### services/
Lógica de negócio principal, incluindo geração de tiles, processamento em lote e gerenciamento de filas.

### tasks/
Tarefas assíncronas executadas pelo Celery para processamento em background.

### utils/
Funções utilitárias diversas usadas em toda a aplicação.

### visualization/
Sistema de parâmetros de visualização com suporte para configurações hardcoded e baseadas em banco de dados.

## Migrando Imports

Ao usar a nova estrutura, atualize seus imports:

```python
# Antes
from app.config import settings, logger
from app.cache_hybrid import HybridCache
from app.mongodb import get_database

# Depois
from app.core import settings, logger
from app.cache import HybridCache
from app.core import get_database
```

## Notas

- A migração foi feita preservando a funcionalidade existente
- Alguns arquivos como `models.py` foram mantidos temporariamente na raiz para evitar quebrar muitas dependências
- O arquivo `router.py` permanece na raiz pois é o ponto central de roteamento