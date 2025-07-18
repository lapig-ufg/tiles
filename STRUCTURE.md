# Estrutura do Projeto Tiles

## Organização dos Arquivos

Os arquivos foram reorganizados para melhor manutenibilidade:

### Arquivos movidos:

1. **Configurações Docker**
   - `docker-compose.yml` → `config/docker/docker-compose.yml`
   - `docker-compose.services.yml` → `config/docker/docker-compose.services.yml`
   - `traefik-labels-compatible.yml` → `config/docker/traefik-labels-compatible.yml`
   - `tile-container-minimal.json` → `config/docker/tile-container-minimal.json`

2. **Configurações de Ambiente**
   - `settings.development.toml` → `config/environments/settings.development.toml`
   - `tiles.env` → `config/environments/tiles.env`

3. **Arquivos de Teste**
   - `test_main.http` → `tests/test_main.http`

4. **Banco de Dados**
   - `db.sqlite3` → `data/db.sqlite3`

### Arquivos mantidos na raiz (essenciais):

- `main.py` - Arquivo principal da aplicação
- `requirements.txt` - Dependências Python
- `settings.toml` - Configurações principais
- `start_services.sh` - Script de inicialização
- `README.md` - Documentação principal
- `app/` - Código da aplicação
- `scripts/` - Scripts utilitários
- `docs/` - Documentação
- `data/` - Arquivos de dados
- `logs/` - Logs da aplicação
- `venv/` - Ambiente virtual Python
- `tiles-client/` - Cliente frontend

## Como usar após a reorganização:

### Docker Compose:
```bash
# Usar o arquivo movido
docker-compose -f config/docker/docker-compose.yml up

# Ou criar um alias
alias dc='docker-compose -f config/docker/docker-compose.yml'
```

### Configurações de desenvolvimento:
```bash
# Copiar configurações de desenvolvimento
cp config/environments/settings.development.toml settings.development.toml

# Ou usar variável de ambiente
export TILES_SETTINGS=config/environments/settings.development.toml
```

### Banco de dados:
O caminho do banco SQLite precisa ser atualizado em `app/core/database.py` se necessário.