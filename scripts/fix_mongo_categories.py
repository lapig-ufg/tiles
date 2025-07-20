#!/usr/bin/env python3
"""
Script para corrigir categorias no MongoDB
Atualiza 'sentinel2' para 'sentinel' para manter compatibilidade
"""

import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
import os
from datetime import datetime

# Configuração do MongoDB
MONGO_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
DB_NAME = os.getenv("TILES_DB", "tiles")

async def fix_categories():
    """Corrige as categorias no MongoDB"""
    client = AsyncIOMotorClient(MONGO_URI)
    db = client[DB_NAME]
    collection = db.vis_params
    
    print(f"Conectando ao MongoDB: {MONGO_URI}/{DB_NAME}")
    
    try:
        # Contar documentos com categoria 'sentinel2'
        count = await collection.count_documents({"category": "sentinel2"})
        print(f"Encontrados {count} documentos com categoria 'sentinel2'")
        
        if count > 0:
            # Atualizar todos os documentos
            result = await collection.update_many(
                {"category": "sentinel2"},
                {
                    "$set": {
                        "category": "sentinel",
                        "updated_at": datetime.utcnow()
                    }
                }
            )
            
            print(f"Atualizados {result.modified_count} documentos")
            
            # Verificar resultado
            new_count = await collection.count_documents({"category": "sentinel"})
            print(f"Total de documentos com categoria 'sentinel': {new_count}")
        else:
            print("Nenhum documento precisa ser atualizado")
            
        # Listar todas as categorias existentes
        print("\nCategorias existentes:")
        categories = await collection.distinct("category")
        for cat in categories:
            count = await collection.count_documents({"category": cat})
            print(f"  - {cat}: {count} documentos")
            
    except Exception as e:
        print(f"Erro: {e}")
    finally:
        client.close()

if __name__ == "__main__":
    asyncio.run(fix_categories())