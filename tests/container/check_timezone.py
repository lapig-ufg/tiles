from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


assert ZoneInfo('UTC').utcoffset(datetime.now()) == timedelta(0), (
    'A montagem de /etc/localtime não pode substituir a definição de UTC'
)

from app.tasks.celery_app import celery_app

before = datetime.now(timezone.utc)
message = celery_app.amqp.as_task_v2(
    'timezone-check', 'app.tasks.offline_campaign.run_offline_campaign',
    args=['timezone-check'], countdown=5,
)
after = datetime.now(timezone.utc)
eta = datetime.fromisoformat(message.headers['eta'])
assert before + timedelta(seconds=5) <= eta <= after + timedelta(seconds=5), (
    'O agendamento relativo deve preservar o instante independentemente do fuso do host'
)
print('UTC e agendamento relativo validados com o fuso horário montado pelo host.')
