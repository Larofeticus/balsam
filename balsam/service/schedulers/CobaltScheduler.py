from getpass import getuser
from datetime import datetime

from django.conf import settings
from balsam.service.schedulers.exceptions import * 
from balsam.service.schedulers import Scheduler

import logging
logger = logging.getLogger(__name__)

def new_scheduler():
    return CobaltScheduler()

class CobaltScheduler(Scheduler.Scheduler):
    SCHEDULER_VARIABLES = {
        'current_scheduler_id' : 'COBALT_JOBID',
        'num_workers'  : 'COBALT_PARTSIZE',
        'workers_str'  : 'COBALT_PARTNAME',
        'workers_file' : 'COBALT_NODEFILE',
    }
    JOBSTATUS_VARIABLES = {
        'id' : 'JobID',
        'time_remaining' : 'TimeRemaining',
        'state' : 'State',
        'queue' : 'Queue',
    }
    QSTAT_EXE = settings.SCHEDULER_STATUS_EXE

    def _make_submit_cmd(self, script_path):
        exe = settings.SCHEDULER_SUBMIT_EXE # qsub
        cwd = settings.SERVICE_PATH
        return f"{exe} --cwd {cwd} {script_path}"

    def _parse_submit_output(self, submit_output):
        try: scheduler_id = int(submit_output)
        except ValueError: scheduler_id = int(submit_output.split()[-1])
        return scheduler_id

    def _make_status_cmd(self):
        fields = self.JOBSTATUS_VARIABLES.values()
        cmd = "QSTAT_HEADER=" + ':'.join(fields)
        cmd += f" {self.QSTAT_EXE} -u {getuser()}"
        return cmd

    def _parse_status_output(self, raw_output):
        status_dict = {}
        logger.debug('parsing qstat ouput: \n' + raw_output)
        job_lines = raw_output.split('\n')[2:]
        for line in job_lines:
            job_stat = self._parse_job_line(line)
            if job_stat:
                id = int(job_stat['id'])
                status_dict[id] = job_stat
        return status_dict

    def _parse_job_line(self, line):
        fields = line.split()
        num_expected = len(self.JOBSTATUS_VARIABLES)
        if len(fields) != num_expected: return {}
        stat = {}
        for i, field_name in enumerate(self.JOBSTATUS_VARIABLES.keys()):
            stat[field_name] = fields[i]
            if 'time' in field_name:
                try:
                    t = datetime.strptime(fields[i], '%H:%M:%S')
                except:
                    pass
                else:
                    t = t.hour*3600 + t.minute*60 + t.second
                    stat[field_name+"_sec"] = t
        logger.debug(str(stat))
        return stat
