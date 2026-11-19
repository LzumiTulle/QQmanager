"""Track the local scan and the analysis jobs belonging to one manual refresh."""
import threading
import time
import uuid


class HistoryReview:
    def __init__(self, store):
        self.store = store
        self.start_lock = threading.Lock()
        self.scanning = None
        saved = store.setting('history_review')
        if saved and saved['phase'] == 'scanning':
            self._save(dict(saved, phase='interrupted'))

    def _save(self, record):
        self.store.set_setting('history_review', record)

    def start(self):
        with self.start_lock:
            current = self.status()
            if current['phase'] in ('scanning', 'analyzing') and not current.get('waiting_only'):
                return current
            record = dict(run=uuid.uuid4().hex, phase='scanning', started=time.time(),
                          total=0, reviewed=0, queued=0, processed=0, failed=0,
                          account=self.store.setting('ws_account'))
            self._save(record)
            self.scanning = record
            threading.Thread(target=self._scan, args=(record,), daemon=True).start()
            return self.public(record)

    @staticmethod
    def public(record):
        return {k:v for k,v in record.items() if k!='account'}

    def _scan(self, record):
        def progress(reviewed, total):
            self.scanning = dict(record, reviewed=reviewed, total=total)
        try:
            with self.store.lock:
                if self.store.setting('ws_account') != record['account']:
                    self._save(dict(record, phase='interrupted'))
                    return
                result = self.store.reanalyze(full=True, review_run=record['run'], progress=progress)
                self._save(dict(record, **result, total=result['reviewed'], phase='analyzing'))
        except Exception:
            self._save(dict(self.scanning, phase='error'))
        finally:
            self.scanning = None

    def status(self):
        scan = self.scanning
        if scan is not None:
            return self.public(scan)
        s = self.store
        with s.lock:
            record = s.setting('history_review')
            if not record or record['account'] != s.setting('ws_account'):
                return {'phase':'idle'}
            if record['phase'] != 'analyzing':
                return self.public(record)
            rows = s.query("""SELECT state,filter_reason,next_at,count(*) n FROM analysis_jobs
                WHERE review_run=? GROUP BY state,filter_reason,next_at""", (record['run'],))
            counts = dict(processed=0, failed=0, running=0, waiting_model=0, waiting_quota=0, retrying=0, skipped=0)
            reasons = {'等待模型启用':'waiting_model','等待模型额度':'waiting_quota'}
            for row in rows:
                state, n = row['state'], row['n']
                if state in ('done','filtered','skipped','failed'):
                    counts['processed'] += n
                    if state in ('failed','skipped'):
                        counts[state] += n
                elif state == 'running':
                    counts['running'] += n
                elif row['filter_reason'] in reasons:
                    counts[reasons[row['filter_reason']]] += n
                elif row['next_at'] > time.time():
                    counts['retrying'] += n
            waiting = counts['waiting_model'] + counts['waiting_quota']
            record = dict(record, **counts, waiting_only=waiting>0 and waiting+counts['processed']==record['queued'])
            if counts['processed'] == record['queued']:
                record.update(phase='finished', finished=time.time())
                self._save(record)
            return self.public(record)
