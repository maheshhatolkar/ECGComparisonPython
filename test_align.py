import traceback
import db
from analyzer import align_signals
import numpy as np

try:
    paths = db.StoragePaths.current()
    a = db.ECGDatabase(paths).load_record(38)['analysis']
    b = db.ECGDatabase(paths).load_record(39)['analysis']
    align_signals(np.array(a['signal_mV']), np.array(b['signal_mV']), a['features']['r_peaks'], b['features']['r_peaks'])
    print('SUCCESS')
except Exception as e:
    traceback.print_exc()
