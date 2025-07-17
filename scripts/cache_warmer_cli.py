#!/usr/bin/env python3
"""
CLI para gerenciamento de cache warming
"""
import click
import requests
import json
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn
import time

console = Console()

BASE_URL = "http://localhost:8000/api/cache"


@click.group()
def cli():
    """Gerenciador de Cache Warming para Tiles"""
    pass


@cli.command()
@click.option('--layer', '-l', required=True, help='Nome da camada')
@click.option('--max-tiles', '-m', default=500, help='Número máximo de tiles')
@click.option('--batch-size', '-b', default=50, help='Tamanho do lote')
@click.option('--params', '-p', default='{}', help='Parâmetros JSON da camada')
def warmup(layer, max_tiles, batch_size, params):
    """Inicia aquecimento de cache para uma camada"""
    try:
        params_dict = json.loads(params)
    except json.JSONDecodeError:
        console.print("[red]Erro: Parâmetros devem ser JSON válido[/red]")
        return
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Agendando warmup...", total=None)
        
        response = requests.post(
            f"{BASE_URL}/warmup",
            json={
                "layer": layer,
                "params": params_dict,
                "max_tiles": max_tiles,
                "batch_size": batch_size
            }
        )
        
        if response.status_code == 200:
            data = response.json()
            console.print(f"[green]✓ Warmup agendado com sucesso![/green]")
            console.print(f"Task ID: {data['task_id']}")
            console.print(f"Total de tiles: {data['total_tiles']}")
            console.print(f"Tempo estimado: {data['estimated_time_minutes']:.1f} minutos")
        else:
            console.print(f"[red]✗ Erro: {response.text}[/red]")


@cli.command()
@click.option('--lat', '-la', required=True, type=float, help='Latitude inicial')
@click.option('--lon', '-lo', required=True, type=float, help='Longitude inicial')
@click.option('--zoom', '-z', multiple=True, type=int, help='Níveis de zoom')
@click.option('--duration', '-d', default=60, help='Duração em segundos')
@click.option('--pattern', '-p', default='random', 
              type=click.Choice(['random', 'linear', 'circular']))
def simulate(lat, lon, zoom, duration, pattern):
    """Simula navegação de usuário"""
    zoom_levels = list(zoom) if zoom else [10, 11, 12]
    
    console.print(f"Iniciando simulação de navegação...")
    console.print(f"Posição inicial: {lat}, {lon}")
    console.print(f"Zooms: {zoom_levels}")
    console.print(f"Padrão: {pattern}")
    
    response = requests.post(
        f"{BASE_URL}/simulate-navigation",
        json={
            "start_lat": lat,
            "start_lon": lon,
            "zoom_levels": zoom_levels,
            "movement_pattern": pattern,
            "duration_seconds": duration
        }
    )
    
    if response.status_code == 200:
        data = response.json()
        console.print(f"[green]✓ {data['message']}[/green]")
        console.print(f"Tiles estimados: {data['estimated_tiles']}")
    else:
        console.print(f"[red]✗ Erro: {response.text}[/red]")


@cli.command()
def status():
    """Mostra status atual do cache"""
    response = requests.get(f"{BASE_URL}/status")
    
    if response.status_code == 200:
        data = response.json()
        
        # Tabela de status
        table = Table(title="Status do Cache")
        table.add_column("Métrica", style="cyan")
        table.add_column("Valor", style="green")
        
        table.add_row("Total de tiles em cache", str(data['total_cached_tiles']))
        table.add_row("Taxa de acerto", f"{data['cache_hit_rate']*100:.1f}%")
        table.add_row("Tasks ativas", str(data['active_tasks']))
        
        console.print(table)
        
        # Tiles populares
        if data['popular_tiles']:
            popular_table = Table(title="Tiles Mais Populares")
            popular_table.add_column("Tile", style="cyan")
            popular_table.add_column("Hits", style="yellow")
            
            for tile in data['popular_tiles']:
                popular_table.add_row(tile['tile'], str(tile['hits']))
            
            console.print(popular_table)
    else:
        console.print(f"[red]✗ Erro ao obter status[/red]")


@cli.command()
@click.argument('task_id')
def check(task_id):
    """Verifica status de uma task"""
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Verificando task...", total=None)
        
        response = requests.get(f"{BASE_URL}/task/{task_id}")
        
        if response.status_code == 200:
            data = response.json()
            
            table = Table(title=f"Status da Task {task_id}")
            table.add_column("Campo", style="cyan")
            table.add_column("Valor", style="green")
            
            table.add_row("Status", data['status'])
            table.add_row("Pronta", "Sim" if data['ready'] else "Não")
            table.add_row("Sucesso", "Sim" if data['successful'] else "Não" if data['successful'] is not None else "N/A")
            
            console.print(table)
            
            if data['result']:
                console.print("\nResultado:", data['result'])
        else:
            console.print(f"[red]✗ Erro ao verificar task[/red]")


@cli.command()
def recommendations():
    """Mostra recomendações de otimização"""
    response = requests.get(f"{BASE_URL}/recommendations")
    
    if response.status_code == 200:
        data = response.json()
        
        console.print(f"[bold]Recomendações de Otimização[/bold]\n")
        
        for rec in data['recommendations']:
            if rec['type'] == 'popular_region':
                console.print(f"[yellow]• Região Popular #{rec['region_id']}[/yellow]")
                console.print(f"  Prioridade: [red]{rec['priority']}[/red]")
                console.print(f"  Bounds: {rec['bounds']}")
                console.print(f"  Zooms recomendados: {rec['recommended_zoom_levels']}")
                console.print(f"  Tiles estimados: {rec['estimated_tiles']}\n")
            
            elif rec['type'] == 'zoom_optimization':
                console.print(f"[yellow]• Otimização de Zoom[/yellow]")
                console.print(f"  Prioridade: [orange]{rec['priority']}[/orange]")
                console.print(f"  Zooms recomendados: {rec['recommended_zooms']}")
                console.print(f"  Razão: {rec['reason']}\n")
    else:
        console.print(f"[red]✗ Erro ao obter recomendações[/red]")


@cli.command()
@click.option('--days', '-d', default=7, help='Dias para análise')
def analyze(days):
    """Analisa padrões de uso"""
    console.print(f"Analisando padrões dos últimos {days} dias...")
    
    response = requests.post(f"{BASE_URL}/analyze-patterns?days={days}")
    
    if response.status_code == 200:
        data = response.json()
        console.print(f"[green]✓ Análise iniciada![/green]")
        console.print(f"Task ID: {data['task_id']}")
        console.print(f"Use 'check {data['task_id']}' para verificar o progresso")
    else:
        console.print(f"[red]✗ Erro ao iniciar análise[/red]")


@cli.command()
@click.option('--layer', '-l', help='Limpar apenas uma camada específica')
@click.option('--zoom', '-z', type=int, help='Limpar apenas um zoom específico')
@click.option('--confirm', is_flag=True, help='Confirmar limpeza')
def clear(layer, zoom, confirm):
    """Limpa o cache (use com cuidado!)"""
    if not confirm:
        console.print("[yellow]⚠ Adicione --confirm para confirmar a limpeza do cache[/yellow]")
        return
    
    params = {"confirm": True}
    if layer:
        params["layer"] = layer
    if zoom:
        params["zoom"] = zoom
    
    response = requests.delete(f"{BASE_URL}/clear", params=params)
    
    if response.status_code == 200:
        data = response.json()
        console.print(f"[green]✓ {data['message']}[/green]")
        if data['filters']:
            console.print(f"Filtros aplicados: {', '.join(data['filters'])}")
    else:
        console.print(f"[red]✗ Erro: {response.text}[/red]")


if __name__ == "__main__":
    cli()