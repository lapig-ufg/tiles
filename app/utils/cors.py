
from os import getenv

# Obtém o caminho do diretório dos arquivos estáticos via variável de ambiente
origin_regex = r"^https:\/\/(?:\w+\.)?lapig\.iesa\.ufg\.br$|^https:\/\/lapig-ufg\.github\.io$"

# Função para obter as origens separadas por vírgula da variável de ambiente
def get_origins_from_env():
    origins = getenv('ALLOW_ORIGINS', '')
    if not origins:
        return []  # Retorna lista vazia se a variável de ambiente não estiver definida ou estiver vazia
    return [origin.strip() for origin in origins.split(',') if origin]


# Obtém as origens permitidas da variável de ambiente
allow_origins = get_origins_from_env()