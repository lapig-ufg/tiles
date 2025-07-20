#!/usr/bin/env python3
"""
Script para padronizar categorias no banco TVI
Atualiza 'sentinel2' para 'sentinel' para manter compatibilidade com o sistema
"""

import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
import os
from datetime import datetime

# Configuração do MongoDB - usa mesma configuração do sistema
MONGO_URI = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
DB_NAME = os.getenv("MONGODB_DB", "tvi")

async def fix_tvi_categories():
    """Padroniza as categorias no banco TVI"""
    client = AsyncIOMotorClient(MONGO_URI)
    db = client[DB_NAME]
    collection = db.vis_params
    
    print(f"Conectando ao MongoDB: {MONGO_URI}/{DB_NAME}")
    
    try:
        # Verificar situação atual
        print("\n=== Situação Atual ===")
        total_docs = await collection.count_documents({})
        active_docs = await collection.count_documents({"active": True})
        sentinel2_count = await collection.count_documents({"category": "sentinel2"})
        sentinel_count = await collection.count_documents({"category": "sentinel"})
        landsat_count = await collection.count_documents({"category": "landsat"})
        
        print(f"Total de documentos: {total_docs}")
        print(f"Documentos ativos: {active_docs}")
        print(f"Categoria 'sentinel2': {sentinel2_count}")
        print(f"Categoria 'sentinel': {sentinel_count}")
        print(f"Categoria 'landsat': {landsat_count}")
        
        # Mostrar documentos sentinel2
        if sentinel2_count > 0:
            print(f"\n=== Documentos com categoria 'sentinel2' ===")
            cursor = collection.find({"category": "sentinel2"}, {"name": 1, "display_name": 1, "active": 1})
            async for doc in cursor:
                status = "ATIVO" if doc.get("active", False) else "INATIVO"
                print(f"  - {doc['name']} ({doc.get('display_name', 'N/A')}) - {status}")
        
        # Perguntar confirmação
        print(f"\n=== Ação Proposta ===")
        print(f"Atualizar {sentinel2_count} documentos de 'sentinel2' para 'sentinel'")
        
        if sentinel2_count > 0:
            # Executar atualização
            print(f"\nExecutando atualização...")
            result = await collection.update_many(
                {"category": "sentinel2"},
                {
                    "$set": {
                        "category": "sentinel",
                        "updated_at": datetime.utcnow()
                    }
                }
            )
            
            print(f"✅ Atualizados {result.modified_count} documentos")
            
            # Verificar resultado
            print(f"\n=== Situação Final ===")
            new_sentinel_count = await collection.count_documents({"category": "sentinel"})
            new_sentinel2_count = await collection.count_documents({"category": "sentinel2"})
            
            print(f"Categoria 'sentinel': {new_sentinel_count} (+{new_sentinel_count - sentinel_count})")
            print(f"Categoria 'sentinel2': {new_sentinel2_count}")
            
            if new_sentinel2_count == 0:
                print("✅ Padronização concluída com sucesso!")
            else:
                print("⚠️  Ainda existem documentos com categoria 'sentinel2'")
        else:
            print("ℹ️  Nenhuma atualização necessária")
            
        # Mostrar resumo final por categoria
        print(f"\n=== Resumo Final ===")
        categories = await collection.distinct("category")
        for cat in categories:
            count = await collection.count_documents({"category": cat})
            active_count = await collection.count_documents({"category": cat, "active": True})
            print(f"  - {cat}: {count} documentos ({active_count} ativos)")
            
    except Exception as e:
        print(f"❌ Erro: {e}")
        import traceback
        traceback.print_exc()
    finally:
        client.close()

if __name__ == "__main__":
    asyncio.run(fix_tvi_categories())