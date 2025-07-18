#!/usr/bin/env python3
"""
Script para migrar imports antigos para a nova estrutura modular
"""
import os
import re
from pathlib import Path

# Mapeamento de imports antigos para novos
IMPORT_MAPPING = {
    # Core
    r'from app\.config import': 'from app.core.config import',
    r'from app\.database import': 'from app.core.database import',
    r'from app\.mongodb import': 'from app.core.mongodb import',
    r'from app\.errors import': 'from app.core.errors import',
    r'from app\.auth import': 'from app.core.auth import',
    
    # Cache
    r'from app\.cache import': 'from app.cache.cache import',
    r'from app\.cache_hybrid import': 'from app.cache.cache_hybrid import',
    r'from app\.cache_warmer import': 'from app.cache.cache_warmer import',
    
    # Services
    r'from app\.tile import': 'from app.services.tile import',
    r'from app\.batch_processor import': 'from app.services.batch_processor import',
    r'from app\.repository import': 'from app.services.repository import',
    r'from app\.request_queue import': 'from app.services.request_queue import',
    r'from app\.prewarm import': 'from app.services.prewarm import',
    
    # Tasks
    r'from app\.tasks import': 'from app.tasks.tasks import',
    r'from app\.cache_tasks import': 'from app.tasks.cache_tasks import',
    r'from app\.celery_app import': 'from app.tasks.celery_app import',
    
    # Middleware
    r'from app\.rate_limiter import': 'from app.middleware.rate_limiter import',
    r'from app\.adaptive_limiter import': 'from app.middleware.adaptive_limiter import',
    
    # Visualization
    r'from app\.visParam import': 'from app.visualization.visParam import',
    r'from app\.vis_params_db import': 'from app.visualization.vis_params_db import',
    r'from app\.vis_params_loader import': 'from app.visualization.vis_params_loader import',
}

def update_imports_in_file(filepath):
    """Atualiza imports em um arquivo Python"""
    try:
        with open(filepath, 'r') as f:
            content = f.read()
        
        original_content = content
        
        # Aplica cada mapeamento
        for old_pattern, new_import in IMPORT_MAPPING.items():
            content = re.sub(old_pattern, new_import, content)
        
        # Salva apenas se houve mudanças
        if content != original_content:
            with open(filepath, 'w') as f:
                f.write(content)
            print(f"✓ Atualizado: {filepath}")
            return True
        return False
    except Exception as e:
        print(f"✗ Erro ao processar {filepath}: {e}")
        return False

def main():
    """Executa a migração de imports"""
    app_dir = Path(__file__).parent
    updated_count = 0
    
    print("Iniciando migração de imports...")
    print(f"Diretório: {app_dir}")
    print("-" * 50)
    
    # Busca todos os arquivos Python
    for py_file in app_dir.rglob("*.py"):
        # Pula este próprio script
        if py_file.name == "migrate_imports.py":
            continue
            
        if update_imports_in_file(py_file):
            updated_count += 1
    
    print("-" * 50)
    print(f"Migração concluída! {updated_count} arquivos atualizados.")

if __name__ == "__main__":
    main()