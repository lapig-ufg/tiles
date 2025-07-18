from datetime import datetime
from app.core.config import logger


def getCacheUrl(cache):
    if cache is None:
        return None
    url, date = cache.decode('utf8').split(', ')
    return {'url':url, 'date':datetime.strptime(date, '%Y-%m-%d %H:%M:%S.%f')}