import os
import re

BDC_OFFLINE_PARALLELISM = os.environ.get('BDC_OFFLINE_PARALLELISM', '4')
if not re.fullmatch(r'[1-8]', BDC_OFFLINE_PARALLELISM):
    raise ValueError('BDC_OFFLINE_PARALLELISM deve ser um inteiro entre 1 e 8')
BDC_OFFLINE_PARALLELISM = int(BDC_OFFLINE_PARALLELISM)
