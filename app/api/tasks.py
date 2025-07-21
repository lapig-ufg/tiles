"""
Endpoints para gerenciamento e monitoramento de tasks do Celery
"""
from datetime import datetime
from typing import Optional, List, Dict, Any

from celery.result import AsyncResult
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.core.auth import SuperAdminRequired
from app.core.config import logger, REDIS_URL
from app.tasks.celery_app import celery_app

router = APIRouter(
    prefix="/api/tasks",
    tags=["Task Management"],
    dependencies=[SuperAdminRequired]
)

class TaskInfo(BaseModel):
    """Informações sobre uma task"""
    task_id: str
    name: str
    state: str
    args: Optional[List[Any]] = None
    kwargs: Optional[Dict[str, Any]] = None
    result: Optional[Any] = None
    error: Optional[str] = None
    traceback: Optional[str] = None
    date_done: Optional[datetime] = None
    worker: Optional[str] = None

class TaskListResponse(BaseModel):
    """Resposta da listagem de tasks"""
    active: List[TaskInfo]
    scheduled: List[TaskInfo]
    reserved: List[TaskInfo]
    stats: Dict[str, Any]

class WorkerStats(BaseModel):
    """Estatísticas dos workers"""
    workers: Dict[str, Any]
    total_workers: int
    active_tasks: int
    scheduled_tasks: int
    reserved_tasks: int

@router.get("/list", response_model=TaskListResponse)
async def list_tasks():
    """
    Lista todas as tasks registradas com seus status e posição na fila
    
    Retorna:
    - active: Tasks sendo executadas atualmente
    - scheduled: Tasks agendadas para execução futura
    - reserved: Tasks reservadas pelos workers mas ainda não iniciadas
    - stats: Estatísticas gerais
    """
    try:
        # Inspeciona o estado dos workers
        inspect = celery_app.control.inspect()
        
        # Tasks ativas (em execução)
        active_tasks = inspect.active() or {}
        active_list = []
        
        for worker, tasks in active_tasks.items():
            for task in tasks:
                active_list.append(TaskInfo(
                    task_id=task['id'],
                    name=task['name'],
                    state='ACTIVE',
                    args=task.get('args'),
                    kwargs=task.get('kwargs'),
                    worker=worker
                ))
        
        # Tasks agendadas
        scheduled_tasks = inspect.scheduled() or {}
        scheduled_list = []
        
        for worker, tasks in scheduled_tasks.items():
            for task in tasks:
                scheduled_list.append(TaskInfo(
                    task_id=task['request']['id'],
                    name=task['request']['name'],
                    state='SCHEDULED',
                    args=task['request'].get('args'),
                    kwargs=task['request'].get('kwargs'),
                    worker=worker
                ))
        
        # Tasks reservadas (na fila mas não iniciadas)
        reserved_tasks = inspect.reserved() or {}
        reserved_list = []
        
        for worker, tasks in reserved_tasks.items():
            for task in tasks:
                reserved_list.append(TaskInfo(
                    task_id=task['id'],
                    name=task['name'],
                    state='RESERVED',
                    args=task.get('args'),
                    kwargs=task.get('kwargs'),
                    worker=worker
                ))
        
        # Estatísticas
        stats = {
            "total_active": len(active_list),
            "total_scheduled": len(scheduled_list),
            "total_reserved": len(reserved_list),
            "total_pending": len(scheduled_list) + len(reserved_list),
            "workers": list(active_tasks.keys())
        }
        
        return TaskListResponse(
            active=active_list,
            scheduled=scheduled_list,
            reserved=reserved_list,
            stats=stats
        )
        
    except Exception as e:
        logger.exception(f"Error listing tasks: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/status/{task_id}")
async def get_task_status(task_id: str):
    """
    Obtém o status detalhado de uma task específica
    """
    try:
        result = AsyncResult(task_id, app=celery_app)
        
        response = {
            "task_id": task_id,
            "state": result.state,
            "ready": result.ready(),
            "successful": result.successful() if result.ready() else None,
            "failed": result.failed() if result.ready() else None,
            "info": None,
            "result": None,
            "error": None,
            "traceback": None
        }
        
        if result.state == 'PENDING':
            response["info"] = "Task not found or not yet submitted"
        elif result.state == 'SUCCESS':
            response["result"] = result.result
        elif result.state == 'FAILURE':
            response["error"] = str(result.info)
            response["traceback"] = result.traceback
        else:
            response["info"] = result.info
        
        return response
        
    except Exception as e:
        logger.exception(f"Error getting task status: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/workers", response_model=WorkerStats)
async def get_worker_stats():
    """
    Obtém estatísticas sobre os workers do Celery
    """
    try:
        inspect = celery_app.control.inspect()
        
        # Estatísticas dos workers
        stats = inspect.stats() or {}
        active_tasks = inspect.active() or {}
        scheduled_tasks = inspect.scheduled() or {}
        reserved_tasks = inspect.reserved() or {}
        
        # Conta tasks por tipo
        total_active = sum(len(tasks) for tasks in active_tasks.values())
        total_scheduled = sum(len(tasks) for tasks in scheduled_tasks.values())
        total_reserved = sum(len(tasks) for tasks in reserved_tasks.values())
        
        # Informações detalhadas por worker
        worker_info = {}
        for worker in stats.keys():
            worker_info[worker] = {
                "stats": stats.get(worker, {}),
                "active_tasks": len(active_tasks.get(worker, [])),
                "scheduled_tasks": len(scheduled_tasks.get(worker, [])),
                "reserved_tasks": len(reserved_tasks.get(worker, [])),
                "pool": stats.get(worker, {}).get('pool', {})
            }
        
        return WorkerStats(
            workers=worker_info,
            total_workers=len(stats),
            active_tasks=total_active,
            scheduled_tasks=total_scheduled,
            reserved_tasks=total_reserved
        )
        
    except Exception as e:
        logger.exception(f"Error getting worker stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/purge")
async def purge_tasks(
    queue_name: Optional[str] = Query(None, description="Nome da fila a ser limpa"),
    state: Optional[str] = Query(None, description="Estado das tasks a serem removidas (scheduled, reserved)")
):
    """
    Remove tasks da fila
    
    CUIDADO: Esta operação é irreversível!
    """
    try:
        if not queue_name and not state:
            # Purga todas as filas
            purged = celery_app.control.purge()
            return {"status": "purged", "message": f"Removed {purged} tasks from all queues"}
        
        # Purga específica ainda não implementada de forma granular
        # Seria necessário implementar lógica customizada
        
        return {"status": "not_implemented", "message": "Selective purge not yet implemented"}
        
    except Exception as e:
        logger.exception(f"Error purging tasks: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/registered")
async def get_registered_tasks():
    """
    Lista todas as tasks registradas no Celery
    """
    try:
        # Tasks registradas
        registered = list(celery_app.tasks.keys())
        
        # Separa por categoria
        categories = {
            "cache": [],
            "tile": [],
            "timeseries": [],
            "warmup": [],
            "other": []
        }
        
        for task_name in registered:
            if "cache" in task_name:
                categories["cache"].append(task_name)
            elif "tile" in task_name:
                categories["tile"].append(task_name)
            elif "timeseries" in task_name:
                categories["timeseries"].append(task_name)
            elif "warm" in task_name:
                categories["warmup"].append(task_name)
            else:
                categories["other"].append(task_name)
        
        return {
            "total": len(registered),
            "tasks": registered,
            "categories": categories
        }
        
    except Exception as e:
        logger.exception(f"Error listing registered tasks: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/queue-length")
async def get_queue_length():
    """
    Obtém o comprimento das filas
    """
    try:
        # Conecta ao Redis para verificar o tamanho das filas
        from app.core.config import settings
        import redis
        
        r = redis.from_url(REDIS_URL)
        
        # Busca todas as filas do Celery
        queues = {}
        for key in r.keys('celery*'):
            key_str = key.decode('utf-8')
            if 'celery' in key_str:
                length = r.llen(key)
                if length > 0:
                    queues[key_str] = length
        
        return {
            "queues": queues,
            "total_tasks": sum(queues.values())
        }
        
    except Exception as e:
        logger.exception(f"Error getting queue length: {e}")
        raise HTTPException(status_code=500, detail=str(e))