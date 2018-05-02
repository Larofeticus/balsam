import os
import json
import logging
import re
import sys
from datetime import datetime
import uuid

from django.core.exceptions import ValidationError,ObjectDoesNotExist
from django.conf import settings
from django.db import models
from django.db.models import Value as V
from django.db.models.functions import Concat
from concurrency.fields import IntegerVersionField
from concurrency.exceptions import RecordModifiedError

logger = logging.getLogger('balsam.service.models')

class InvalidStateError(ValidationError): pass
class InvalidParentsError(ValidationError): pass
class NoApplication(Exception): pass

TIME_FMT = '%m-%d-%Y %H:%M:%S.%f'

STATES = '''
CREATED
LAUNCHER_QUEUED
AWAITING_PARENTS
READY

STAGED_IN
PREPROCESSED

RUNNING
RUN_DONE

POSTPROCESSED
JOB_FINISHED

RUN_TIMEOUT
RUN_ERROR
RESTART_READY

FAILED
USER_KILLED
PARENT_KILLED'''.split()

ACTIVE_STATES = '''
RUNNING
'''.split()

PROCESSABLE_STATES = '''
CREATED
LAUNCHER_QUEUED
AWAITING_PARENTS
READY
STAGED_IN
RUN_DONE
POSTPROCESSED
RUN_TIMEOUT
RUN_ERROR
'''.split()

RUNNABLE_STATES = '''
PREPROCESSED
RESTART_READY
'''.split()

END_STATES = '''
JOB_FINISHED
FAILED
USER_KILLED
PARENT_KILLED'''.split()
        
STATE_TIME_PATTERN = re.compile(r'''
^                  # start of line
\[                 # opening square bracket
(\d+-\d+-\d\d\d\d  # date MM-DD-YYYY
\s+                # one or more space
\d+:\d+:\d+\.\d+)  # time HH:MM:SS.MICROSEC
\s+                # one or more space
(\w+)              # state
\s*                # 0 or more space
\]                 # closing square bracket
''', re.VERBOSE | re.MULTILINE)

_app_cache = {}

def process_job_times(time0=None, state0=None):
    '''Returns {state : [elapsed_seconds_for_each_job_to_reach_state]}
    Useful for tracking job performance/throughput'''
    from collections import defaultdict

    if state0 is None: state0 = 'READY'
    data = BalsamJob.objects.values_list('state_history', flat=True)
    data = '\n'.join(data)
    matches = STATE_TIME_PATTERN.finditer(data)
    result = ( m.groups() for m in matches )
    result = ( (state, datetime.strptime(time_str, TIME_FMT))
              for (time_str, state) in result )
    
    time_data = defaultdict(list)
    for state, time in result:
        time_data[state].append(time)

    if time0 is None: 
        if state0 not in time_data:
            raise ValueError(f"Requested time-zero at first instance of {state0}, "
                "but there are no jobs in the DB with this state!")
        time0 = min(time_data[state0])

    for state in time_data.keys():
        time_data[state] = [(t - time0).total_seconds() for t in sorted(time_data[state])]

    return time_data


def assert_disjoint():
    groups = [ACTIVE_STATES, PROCESSABLE_STATES, RUNNABLE_STATES, END_STATES]
    joined = [state for g in groups for state in g]
    assert len(joined) == len(set(joined)) == len(STATES)
    assert set(joined) == set(STATES) 
    from itertools import combinations
    for g1,g2 in combinations(groups, 2):
        s1,s2 = set(g1), set(g2)
        assert s1.intersection(s2) == set()
assert_disjoint()

def validate_state(value):
    if value not in STATES:
        raise InvalidStateError(f"{value} is not a valid state in balsam.models")

def get_time_string():
    return datetime.now().strftime(TIME_FMT)

def from_time_string(s):
    return datetime.strptime(s, TIME_FMT)

def history_line(state='CREATED', message=''):
    return f"\n[{get_time_string()} {state}] ".rjust(46) + message


class BalsamJob(models.Model):
    ''' A DB representation of a Balsam Job '''

    version = IntegerVersionField() # optimistic lock

    job_id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False)
    allowed_work_sites = models.TextField(
        'Allowed Work Sites',
        help_text='Name of the Balsam instance(s) where this job can run.',
        default='')
    work_site = models.TextField(
        'Actual work site',
        help_text='Name of the Balsam instance that handled this job.',
        default='')

    workflow = models.TextField(
        'Workflow Name',
        help_text='Name of the workflow to which this job belongs',
        default='')
    name = models.TextField(
        'Job Name',
        help_text='A name for the job given by the user.',
        default='')
    description = models.TextField(
        'Job Description',
        help_text='A description of the job.',
        default='')

    parents = models.TextField(
        'IDs of the parent jobs which must complete prior to the start of this job.',
        default='[]')

    input_files = models.TextField(
        'Input File Patterns',
        help_text="Space-delimited filename patterns that will be searched in the parents'"\
        "working directories. Every matching file will be made available in this"\
        "job's working directory (symlinks for local Balsam jobs, file transfer for"\
        "remote Balsam jobs). Default: all files from parent jobs are made available.",
        default='*')
    stage_in_url = models.TextField(
        'External stage in files or folders', help_text="A list of URLs for external data to be staged in prior to job processing. Job dataflow from parents to children is NOT handled here; see `input_files` field instead.",
        default='')
    stage_out_files = models.TextField(
        'External stage out files or folders',
        help_text="A string of filename patterns. Matches will be transferred to the stage_out_url. Default: no files are staged out",
        default='')
    stage_out_url = models.TextField(
        'Stage Out URL',
        help_text='The URLs to which designated stage out files are sent.',
        default='')

    wall_time_minutes = models.IntegerField(
        'Job Wall Time in Minutes',
        help_text='The number of minutes the job is expected to take',
        default=1)
    num_nodes = models.IntegerField(
        'Number of Compute Nodes',
        help_text='The number of compute nodes requested for this job.',
        default=1)
    ranks_per_node = models.IntegerField(
        'Number of ranks per node',
        help_text='The number of MPI ranks per node to schedule for this job.',
        default=1)
    threads_per_rank = models.IntegerField(
        'Number of threads per MPI rank',
        help_text='The number of OpenMP threads per MPI rank (if applicable)',
        default=1)
    threads_per_core = models.IntegerField(
        'Number of hyperthreads per physical core (if applicable)',
        help_text='Number of hyperthreads per physical core.',
        default=1)
    serial_node_packing_count = models.IntegerField(
        'For serial (non-MPI) jobs only. How many to run concurrently on a node.',
        help_text='Setting this field at 2 means two serial jobs will run at a '
        'time on a node. This field is ignored for MPI jobs.',
        default=16)
    environ_vars = models.TextField(
        'Environment variables specific to this job',
        help_text="Colon-separated list of envs like VAR1=value1:VAR2=value2",
        default='')
    
    scheduler_id = models.TextField(
        'Scheduler ID',
        help_text='Scheduler ID (if job assigned by metascheduler)',
        default='')

    application = models.TextField(
        'Application to Run',
        help_text='The application to run; located in Applications database',
        default='')
    application_args = models.TextField(
        'Command-line args to the application exe',
        help_text='Command line arguments used by the Balsam job runner',
        default='')

    direct_command = models.TextField(
        'Command line to execute (specified with balsam qsub <args> <command>)',
        help_text="Instead of creating BalsamJobs that point to a pre-defined "
        "application, users can directly add jobs consisting of a single command "
        "line with `balsam qsub`.  This direct command is then invoked by the  "
        "Balsam job launcher.",
        default='')

    preprocess = models.TextField(
        'Preprocessing Script',
        help_text='A script that is run in a job working directory prior to submitting the job to the queue.'
        ' If blank, will default to the default_preprocess script defined for the application.',
        default='')
    postprocess = models.TextField(
        'Postprocessing Script',
        help_text='A script that is run in a job working directory after the job has completed.'
        ' If blank, will default to the default_postprocess script defined for the application.',
        default='')
    wait_for_parents = models.BooleanField(
            'If True, do not process this job until parents are FINISHED',
            default=True)
    post_error_handler = models.BooleanField(
        'Let postprocesser try to handle RUN_ERROR',
        help_text='If true, the postprocessor will be invoked for RUN_ERROR jobs'
        ' and it is up to the script to handle error and update job state.',
        default=False)
    post_timeout_handler = models.BooleanField(
        'Let postprocesser try to handle RUN_TIMEOUT',
        help_text='If true, the postprocessor will be invoked for RUN_TIMEOUT jobs'
        ' and it is up to the script to handle timeout and update job state.',
        default=False)
    auto_timeout_retry = models.BooleanField(
        'Automatically restart jobs that have timed out',
        help_text="If True and post_timeout_handler is False, then jobs will "
        "simply be marked RESTART_READY upon timing out.",
        default=True)

    state = models.TextField(
        'Job State',
        help_text='The current state of the job.',
        default='CREATED',
        validators=[validate_state])
    state_history = models.TextField(
        'Job State History',
        help_text="Chronological record of the job's states",
        default=history_line)

    def _save_direct(self, force_insert=False, force_update=False, using=None, 
             update_fields=None):
        '''Override default Django save to ensure version always updated'''
        if update_fields is not None: 
            update_fields.append('version')
        if self._state.adding:
            update_fields = None
        models.Model.save(self, force_insert, force_update, using, update_fields)

    def save(self, force_insert=False, force_update=False, using=None, update_fields=None):
        if settings.SAVE_CLIENT is None:
            logger.info(f"direct save of {self.cute_id}")
            self._save_direct(force_insert, force_update, using, update_fields)
        else:
            settings.SAVE_CLIENT.save(self, force_insert, force_update, using, update_fields)
            self.refresh_from_db()

    @staticmethod
    def from_dict(d):
        job = BalsamJob()
        SERIAL_FIELDS = [f for f in job.__dict__ if f not in
                '_state force_insert force_update using update_fields'.split()
                ]

        if type(d['job_id']) is str:
            d['job_id'] = uuid.UUID(d['job_id'])
        else:
            assert d['job_id'] is None
            d['job_id'] = job.job_id

        for field in SERIAL_FIELDS:
            job.__dict__[field] = d[field]

        assert type(job.job_id) == uuid.UUID
        return job


    def __str__(self):
        return f'''
Balsam Job
----------
ID:                     {self.pk}
name:                   {self.name} 
workflow:               {self.workflow}
latest state:           {self.get_recent_state_str()}
description:            {self.description[:80]}
work site:              {self.work_site} 
allowed work sites:     {self.allowed_work_sites}
working_directory:      {self.working_directory}
parents:                {self.parents}
input_files:            {self.input_files}
stage_in_url:           {self.stage_in_url}
stage_out_url:          {self.stage_out_url}
stage_out_files:        {self.stage_out_files}
wall_time_minutes:      {self.wall_time_minutes}
num_nodes:              {self.num_nodes}
threads per rank:       {self.threads_per_rank}
threads per core:       {self.threads_per_core}
ranks_per_node:         {self.ranks_per_node}
scheduler_id:           {self.scheduler_id}
application:            {self.application if self.application else 
                            self.direct_command}
args:                   {self.application_args}
envs:                   {self.environ_vars}
created with qsub:      {bool(self.direct_command)}
preprocess override:    {self.preprocess}
postprocess override:   {self.postprocess}
post handles error:     {self.post_error_handler}
post handles timeout:   {self.post_timeout_handler}
auto timeout retry:     {self.auto_timeout_retry}
'''.strip() + '\n'
    

    def get_parents_by_id(self):
        return json.loads(self.parents)

    def get_parents(self):
        parent_ids = self.get_parents_by_id()
        return BalsamJob.objects.filter(job_id__in=parent_ids)

    @property
    def num_ranks(self):
        return self.num_nodes * self.ranks_per_node

    @property
    def cute_id(self):
        if self.name:
            return f"[{self.name} | { str(self.pk)[:8] }]"
        else:
            return f"[{ str(self.pk)[:8] }]"
    
    @property
    def app_cmd(self):
        if self.application:
            if self.application in _app_cache:
                app = _app_cache[self.application]
            else:
                app = ApplicationDefinition.objects.get(name=self.application)
                _app_cache[self.application] = app
            line = f"{app.executable} {self.application_args}"
        else:
            line = f"{self.direct_command} {self.application_args}"
        return ' '.join(os.path.expanduser(w) for w in line.split())

    def get_children(self):
        return BalsamJob.objects.filter(parents__icontains=str(self.pk))

    def get_children_by_id(self):
        children = self.get_children()
        return [c.pk for c in children]

    def get_child_by_name(self, name):
        children = self.get_children().filter(name=name)
        if children.count() == 0:
            raise ValueError(f"No child named {name}")
        elif children.count() > 1:
            raise ValueError(f"More than one child named {name}")
        else:
            return children.first()

    def set_parents(self, parents):
        try:
            parents_list = list(parents)
        except:
            raise InvalidParentsError("Cannot convert input to list")
        for i, parent in enumerate(parents_list):
            pk = parent.pk if isinstance(parent,BalsamJob) else parent
            if not BalsamJob.objects.filter(pk=pk).exists():
                raise InvalidParentsError(f"Job PK {pk} is not in the BalsamJob DB")
            parents_list[i] = str(pk)
        self.parents = json.dumps(parents_list)
        self.save(update_fields=['parents'])

    def get_application(self):
        if self.application:
            return ApplicationDefinition.objects.get(name=self.application)
        else:
            raise NoApplication

    @staticmethod
    def parse_envstring(s):
        result = {}
        entries = s.split(':')
        entries = [e.split('=') for e in entries]
        return {variable:value for (variable,value) in entries}

    def get_envs(self, *, timeout=False, error=False):
        keywords = 'BALSAM DJANGO PYTHON'.split()
        envs = {var:value for var,value in os.environ.items() 
                if any(keyword in var for keyword in keywords)}
        
        if self.environ_vars:
            job_vars = self.parse_envstring(self.environ_vars)
            envs.update(job_vars)
    
        balsam_envs = dict(
            BALSAM_JOB_ID=str(self.pk),
            BALSAM_PARENT_IDS=str(self.parents),
        )

        if self.threads_per_rank > 1:
            balsam_envs['OMP_NUM_THREADS'] = str(self.threads_per_rank)

        if timeout: balsam_envs['BALSAM_JOB_TIMEOUT']="TRUE"
        if error: balsam_envs['BALSAM_JOB_ERROR']="TRUE"
        envs.update(balsam_envs)
        return envs

    @classmethod
    def batch_update_state(cls, pk_list, new_state, message=''):
        if new_state not in STATES:
            raise InvalidStateError(f"{new_state} is not a job state in balsam.models")

        states = cls.objects.filter(job_id__in=pk_list).values_list('job_id', 'state')
        assert len(states) == len(pk_list)
        update_ids = [jid for (jid,state) in states if state != 'USER_KILLED']

        update_jobs = cls.objects.filter(job_id__in=update_ids)
        msg = history_line(new_state, message)

        update_jobs.update(state=new_state,
                           state_history=Concat('state_history', V(msg))
                          )

    def update_state(self, new_state, message='',using=None):
        if new_state not in STATES:
            raise InvalidStateError(f"{new_state} is not a job state in balsam.models")

        # If already exists
        if not self._state.adding:
            self.refresh_from_db()
        if self.state == 'USER_KILLED': return

        self.state_history += history_line(new_state, message)
        self.state = new_state
        try:
            self.save(update_fields=['state', 'state_history'],using=using)
        except RecordModifiedError:
            self.refresh_from_db()
            if self.state == 'USER_KILLED' and new_state != 'USER_KILLED':
                return
            elif new_state == 'USER_KILLED':
                self.state_history += history_line(new_state, message)
                self.state = new_state
                self.save(update_fields=['state', 'state_history'],using=using)
            else:
                raise

    def get_recent_state_str(self):
        return self.state_history.split("\n")[-1].strip()

    def read_file_in_workdir(self, fname):
        work_dir = self.working_directory
        path = os.path.join(work_dir, fname)
        if not os.path.exists(path):
            raise ValueError(f"{fname} not found in working directory of {self.cute_id}")
        else:
            return open(path).read()

    def get_state_times(self):
        matches = STATE_TIME_PATTERN.findall(self.state_history)
        return {state: datetime.strptime(timestr, TIME_FMT)
                for timestr, state in matches
               }

    @property
    def runtime_seconds(self):
        times = self.get_state_times()
        t0 = times.get('RUNNING', None) 
        t1 = times.get('RUN_DONE', None) 
        if t0 and t1:
            return (t1-t0).total_seconds()
        else:
            return None

    @property
    def working_directory(self):
        top = settings.BALSAM_WORK_DIRECTORY
        if self.workflow:
            top = os.path.join(top, self.workflow)
        name = self.name.strip().replace(' ', '_')
        name += '_' + str(self.pk)
        path = os.path.join(top, name)
        return path

    def to_dict(self):
        SERIAL_FIELDS = [f for f in self.__dict__ if f not in ['_state']]
        d = {field : self.__dict__[field] for field in SERIAL_FIELDS}
        return d

    def serialize(self, **kwargs):
        d = self.to_dict()
        d.update(kwargs)
        if type(self.job_id) == uuid.UUID:
            d['job_id'] = str(self.job_id)
        else:
            assert self.job_id == d['job_id'] == None

        serial_data = json.dumps(d)
        return serial_data

    @classmethod
    def deserialize(cls, serial_data):
        if type(serial_data) is bytes:
            serial_data = serial_data.decode('utf-8')
        if type(serial_data) is str:
            serial_data = json.loads(serial_data)
        job = BalsamJob.from_dict(serial_data)
        return job

class ApplicationDefinition(models.Model):
    ''' application definition, each DB entry is a task that can be run
        on the local resource. '''
    name = models.TextField(
        'Application Name',
        help_text='The name of an application that can be run locally.',
        default='')
    description = models.TextField(
        'Application Description',
        help_text='A description of the application.',
        default='')
    executable = models.TextField(
        'Executable',
        help_text='The executable and path need to run this application on the local system.',
        default='')
    default_preprocess = models.TextField(
        'Preprocessing Script',
        help_text='A script that is run in a job working directory prior to submitting the job to the queue.',
        default='')
    default_postprocess = models.TextField(
        'Postprocessing Script',
        help_text='A script that is run in a job working directory after the job has completed.',
        default='')

    def __str__(self):
        return f'''
Application:
------------
PK:             {self.pk}
Name:           {self.name}
Description:    {self.description}
Executable:     {self.executable}
Preprocess:     {self.default_preprocess}
Postprocess:    {self.default_postprocess}
'''.strip() + '\n'

    @property
    def cute_id(self):
        return f"[{self.name} | { str(self.pk)[:8] }]"
