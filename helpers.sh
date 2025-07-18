#!/bin/bash
# Helper script para comandos comuns após reorganização

# Cores para output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Função para docker-compose
dc() {
    if [ -L "docker-compose.yml" ]; then
        # Se o link simbólico existe, usar normalmente
        docker-compose "$@"
    else
        # Caso contrário, usar o arquivo movido
        docker-compose -f config/docker/docker-compose.yml "$@"
    fi
}

# Função para docker-compose services
dcs() {
    if [ -L "docker-compose.services.yml" ]; then
        docker-compose -f docker-compose.services.yml "$@"
    else
        docker-compose -f config/docker/docker-compose.services.yml "$@"
    fi
}

# Função para copiar configurações de desenvolvimento
dev-setup() {
    echo -e "${YELLOW}Configurando ambiente de desenvolvimento...${NC}"
    if [ ! -f "settings.development.toml" ]; then
        cp config/environments/settings.development.toml .
        echo -e "${GREEN}✓ settings.development.toml copiado${NC}"
    fi
    
    if [ ! -f ".env" ] && [ -f "config/environments/tiles.env" ]; then
        cp config/environments/tiles.env .env
        echo -e "${GREEN}✓ .env criado${NC}"
    fi
}

# Função para rodar testes
test-api() {
    echo -e "${YELLOW}Executando testes da API...${NC}"
    if command -v httpie &> /dev/null; then
        http --session=test < tests/test_main.http
    else
        echo -e "${RED}HTTPie não instalado. Instale com: pip install httpie${NC}"
    fi
}

# Função para iniciar serviços
start-services() {
    echo -e "${YELLOW}Iniciando serviços...${NC}"
    if [ -f "start_services.sh" ]; then
        ./start_services.sh
    else
        echo -e "${RED}start_services.sh não encontrado${NC}"
    fi
}

# Função para iniciar aplicação
start-app() {
    echo -e "${YELLOW}Iniciando aplicação Tiles...${NC}"
    if [ -f "venv/bin/activate" ]; then
        source venv/bin/activate
        python main.py
    else
        echo -e "${RED}Ambiente virtual não encontrado. Execute: python -m venv venv${NC}"
    fi
}

# Função para migrar vis params para MongoDB
migrate-visparams() {
    echo -e "${YELLOW}Migrando VISPARAMS para MongoDB...${NC}"
    if [ -f "scripts/migrate_vis_params.py" ]; then
        python scripts/migrate_vis_params.py
        echo -e "${GREEN}✓ Migração concluída${NC}"
    else
        echo -e "${RED}Script de migração não encontrado${NC}"
    fi
}

# Função para limpar cache
clear-cache() {
    echo -e "${YELLOW}Limpando cache...${NC}"
    echo "Escolha o tipo de limpeza:"
    echo "1) Cache Redis"
    echo "2) Cache S3"
    echo "3) Ambos"
    read -p "Opção: " choice
    
    case $choice in
        1)
            redis-cli FLUSHALL
            echo -e "${GREEN}✓ Cache Redis limpo${NC}"
            ;;
        2)
            echo -e "${YELLOW}Função de limpeza S3 ainda não implementada${NC}"
            ;;
        3)
            redis-cli FLUSHALL
            echo -e "${GREEN}✓ Cache Redis limpo${NC}"
            echo -e "${YELLOW}Função de limpeza S3 ainda não implementada${NC}"
            ;;
        *)
            echo -e "${RED}Opção inválida${NC}"
            ;;
    esac
}

# Função para verificar status dos serviços
check-status() {
    echo -e "${YELLOW}Verificando status dos serviços...${NC}"
    
    # Verifica Redis
    if redis-cli ping > /dev/null 2>&1; then
        echo -e "${GREEN}✓ Redis está rodando${NC}"
    else
        echo -e "${RED}✗ Redis não está rodando${NC}"
    fi
    
    # Verifica MongoDB
    if mongo --eval "db.version()" > /dev/null 2>&1; then
        echo -e "${GREEN}✓ MongoDB está rodando${NC}"
    else
        echo -e "${RED}✗ MongoDB não está rodando${NC}"
    fi
    
    # Verifica aplicação
    if curl -s http://localhost:8000/health > /dev/null 2>&1; then
        echo -e "${GREEN}✓ Aplicação está rodando${NC}"
    else
        echo -e "${RED}✗ Aplicação não está rodando${NC}"
    fi
}

# Função para mostrar logs
show-logs() {
    echo "Escolha o log:"
    echo "1) Aplicação"
    echo "2) Celery"
    echo "3) Docker Compose"
    read -p "Opção: " choice
    
    case $choice in
        1)
            tail -f logs/app.log
            ;;
        2)
            tail -f logs/celery.log
            ;;
        3)
            dc logs -f
            ;;
        *)
            echo -e "${RED}Opção inválida${NC}"
            ;;
    esac
}

# Informações de ajuda
help() {
    echo -e "${GREEN}=== Tiles Helper Script ===${NC}"
    echo ""
    echo "Comandos disponíveis:"
    echo -e "  ${YELLOW}dc [args]${NC}         - Executa docker-compose"
    echo -e "  ${YELLOW}dcs [args]${NC}        - Executa docker-compose services"
    echo -e "  ${YELLOW}dev-setup${NC}         - Configura ambiente de desenvolvimento"
    echo -e "  ${YELLOW}test-api${NC}          - Executa testes da API"
    echo -e "  ${YELLOW}start-services${NC}    - Inicia serviços externos"
    echo -e "  ${YELLOW}start-app${NC}         - Inicia aplicação Tiles"
    echo -e "  ${YELLOW}migrate-visparams${NC} - Migra VISPARAMS para MongoDB"
    echo -e "  ${YELLOW}clear-cache${NC}       - Limpa cache Redis/S3"
    echo -e "  ${YELLOW}check-status${NC}      - Verifica status dos serviços"
    echo -e "  ${YELLOW}show-logs${NC}         - Mostra logs da aplicação"
    echo ""
    echo "Estrutura reorganizada:"
    echo "  config/docker/          - Arquivos Docker"
    echo "  config/environments/    - Configurações de ambiente"
    echo "  tests/                  - Arquivos de teste"
    echo "  data/                   - Banco de dados e arquivos de dados"
    echo "  app/                    - Código organizado em módulos:"
    echo "    ├── cache/            - Sistema de cache"
    echo "    ├── core/             - Funcionalidades principais"
    echo "    ├── middleware/       - Rate limiting, etc"
    echo "    ├── models/           - Modelos Pydantic"
    echo "    ├── services/         - Serviços (tiles, monitoring)"
    echo "    ├── tasks/            - Tarefas Celery"
    echo "    └── visualization/    - Parâmetros de visualização"
    echo ""
    echo -e "${YELLOW}Para usar os comandos, execute: source helpers.sh${NC}"
}

# Se executado com source, não fazer nada
# Se executado diretamente, mostrar ajuda
if [ "${BASH_SOURCE[0]}" == "${0}" ]; then
    help
fi