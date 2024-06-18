from datetime import datetime


def getCacheUrl(cache):
    if cache is None:
        return None
    url, date = cache.split(', ')
    return {'url':url, 'date':datetime.strptime(date, '%Y-%m-%d %H:%M:%S.%f')}