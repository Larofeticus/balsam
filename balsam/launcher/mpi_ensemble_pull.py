'''mpi4py wrapper that allows an ensemble of serial applications to run in
parallel across ranks on the computing resource'''
import argparse
from collections import namedtuple
import os
import sys
import logging
import django
import random
import signal
import time
from socket import gethostname

os.environ['DJANGO_SETTINGS_MODULE'] = 'balsam.django_config.settings'
django.setup()
logger = logging.getLogger('balsam.launcher.mpi_ensemble')

from subprocess import Popen, STDOUT, TimeoutExpired

from mpi4py import MPI

from balsam.launcher import jobreader
from balsam.launcher.util import cd, get_tail, parse_real_time, remaining_time_minutes
from balsam.launcher.exceptions import *
from balsam.service.models import BalsamJob

comm = MPI.COMM_WORLD
RANK = comm.Get_rank()
HANDLE_EXIT = False
django.db.connections.close_all()


class Tags:
    EXIT = 0  # master --> worker: exit now
    NEW = 1 # master --> worker: new job spec
    KILL = 2 # master --> worker: stop current job
    CONTINUE = 3 # master --> worker: keep running current job
    ASK = 4  # worker --> master: ask keep going?
    DONE = 5 # worker --> master: job success
    ERROR = 6 # worker --> master: job error


class ResourceManager:

    FETCH_PERIOD = 5.0
    KILLED_REFRESH_PERIOD = 10.0

    def __init__(self, host_names, job_source):
        self.host_names = host_names
        self.job_source = job_source
        self.node_occupancy = {name : 0.0 for name in set(host_names)}
        self.job_assignments = [None for i in range(comm.size)]
        self.job_assignments[0] = ('master', 1.0)

        self.last_job_fetch = -10.0
        self.last_killed_refresh = -10.0
        self.job_cache = []

        self.host_rank_map = {}
        for name in set(host_names):
            self.host_rank_map[name] = [i for i,hostname in enumerate(host_names) if hostname == name]
            logger.debug(f"Hostname {name}: Ranks {self.host_rank_map[name]}")

        self.recv_requests = {}

    def refresh_job_cache(self, time_limit_min):
        now = time.time()
        if now - self.last_job_fetch > self.FETCH_PERIOD:
            jobquery = self.job_source.get_runnable(time_limit_min, serial_only=True)
            jobs = jobquery.order_by('-serial_node_packing_count') # descending order
            self.job_cache = list(jobs)
            self.last_job_fetch = now
            job_str = '  '.join(j.cute_id for j in self.job_cache)
            logger.debug(f"Refreshed job cache: {len(self.job_cache)} runnable\n{job_str}")

    def refresh_killed_jobs(self):
        now = time.time()
        if now - self.last_killed_refresh > self.KILLED_REFRESH_PERIOD:
            self.killed_pks = list(
                BalsamJob.objects.filter(state='USER_KILLED').values_list('job_id', flat=True)
            )
            self.last_killed_refresh = now
            if self.killed_pks: logger.debug(f"Killed jobs: {self.killed_pks}")
        
    @django.db.transaction.atomic
    def allocate_next_jobs(self, time_limit_min):
        '''Generator: yield (job,rank) pairs and mark the nodes/ranks as busy'''
        self.refresh_job_cache(time_limit_min)
        submitted_indices = []
        send_requests = []

        for cache_idx, job in enumerate(self.job_cache):
            job_occ = 1.0 / job.serial_node_packing_count
            
            free_nodes = (name for name,occ in self.node_occupancy.items() if job_occ+occ < 1.001)
            free_nodes = sorted(free_nodes, key = lambda n: self.node_occupancy[n])
            free_ranks = ([i,node] for node in free_nodes for i in self.host_rank_map[node] if self.job_assignments[i] is None)
            assignment = next(free_ranks, None)

            if assignment is None:
                logger.debug(f'no free ranks to assign {job.cute_id}')
                break

            rank, free_node = assignment
            self.node_occupancy[free_node] += job_occ
            self.job_assignments[rank] = (job.pk, job_occ)

            req = self._send_job(job, rank)
            send_requests.append(req)

            submitted_indices.append(cache_idx)
            logger.debug(f"Sent {job.cute_id} to rank {rank} on {free_node}: occupancy is now {self.node_occupancy[free_node]}")

        self.job_cache = [job for i, job in enumerate(self.job_cache)
                          if i not in submitted_indices]
        
        MPI.Request.waitall(send_requests)
        return len(submitted_indices) > 0

    
    def _send_job(self, job, rank):
        '''Send message to compute rank'''
        start = time.time()
        job_spec = {}
        if not job.working_directory:
            job.create_working_path()

        job_spec['workdir'] = job.working_directory
        job_spec['name'] = job.name
        job_spec['cuteid'] = job.cute_id
        job_spec['cmd'] = job.app_cmd
        start_envs = time.time()
        job_spec['envs'] = job.get_envs()
        end_envs = time.time()

        req = comm.isend(job_spec, dest=rank, tag=Tags.NEW)
        self.recv_requests[rank] = comm.irecv(source=rank)
        requests_posted = time.time()
        job.update_state('RUNNING', f'MPI Ensemble rank {rank}')
        end = time.time()

        prep_time = requests_posted - start
        update_state_time = end - requests_posted
        envs_time = end_envs - start_envs
        fraction_envs = envs_time / prep_time
        logger.debug(f"time to prep job; post send/recv: {prep_time:.3f} ({fraction_envs:.2f} spent just in get_envs)")
        logger.debug(f"time to call update_state to RUNNING: {update_state_time:.3f}")
        return req

    def serve_request(self):
        request = self._get_request()
        if request is not None:
            msg, source, tag = request
            self._handle_request(msg, source, tag)
            return True
        else:
            return False

    def _get_request(self):
        if not self.recv_requests: return None
        stat = MPI.Status()
        requests = list(self.recv_requests.values())
        _, got_msg, msg = MPI.Request.testany(requests, status=stat)
        if not got_msg:
            return None
        else:
            source, tag = stat.source, stat.tag
            del self.recv_requests[source]
            return msg, source, tag

    def _handle_request(self, msg, source, tag):
        if tag == Tags.ASK:
            self._handle_ask(msg, source, tag)
        elif tag == Tags.DONE:
            self._handle_done(msg, source, tag)
        elif tag == Tags.ERROR:
            self._handle_error(msg, source, tag)
        else:
            raise RuntimeError(f"Unexpected tag from worker: {tag}")

    def _handle_ask(self, msg, source, tag):
        self.refresh_killed_jobs()
        pk, occ = self.job_assignments[source]

        if pk in self.killed_pks:
            req = comm.isend("kill", dest=source, tag=Tags.KILL)
            self.job_assignments[source] = None
            hostname = self.host_names[source]
            self.node_occupancy[hostname] -= occ
            logger.debug(f"Sent KILL to rank {source} on {hostname}: occupancy is now {self.node_occupancy[hostname]}")
        else:
            req = comm.isend("continue", dest=source, tag=Tags.CONTINUE)
            self.recv_requests[source] = comm.irecv(source=source)
        req.wait()
    
    def _handle_done(self, msg, source, tag):
        TIMEstart = time.time()
        pk, occ = self.job_assignments[source]

        job = BalsamJob.objects.get(pk=pk)
        TIMEend_get = time.time()
        elapsed_seconds = msg['elapsed_seconds']
        job.update_state('RUN_DONE', f"elapsed sec {elapsed_seconds}")
        TIMEupdate = time.time()
        logger.debug(f"{job.cute_id} RUN_DONE from rank {source}")
        TIMEsavetime = None
        if elapsed_seconds is not None:
            job.runtime_seconds = float(elapsed_seconds)
            job.save(update_fields = ['runtime_seconds'])
            TIMEsavetime = time.time()

        self.job_assignments[source] = None
        hostname = self.host_names[source]
        self.node_occupancy[hostname] -= occ
        
        logger.debug(f"_handle_done: time for GET: {TIMEend_get-TIMEstart:.3f}")
        logger.debug(f"_handle_done: time for update_state: {TIMEupdate-TIMEend_get:.3f}")
        if TIMEsavetime is not None:
            logger.debug(f"_handle_done: time for update runtime: {TIMEsavetime-TIMEupdate:.3f}")
        logger.debug(f"_handle_done: total time: {time.time()-TIMEstart:.3f}")
    
    def _handle_error(self, msg, source, tag):
        pk, occ = self.job_assignments[source]

        job = BalsamJob.objects.get(pk=pk)
        retcode, tail = msg['retcode'], msg['tail']
        state_msg = f"nonzero return {retcode}: {tail}"
        job.update_state('RUN_ERROR', state_msg)
        logger.error(f"{job.cute_id} RUN_ERROR from rank {source}")
        logger.error(state_msg)

        self.job_assignments[source] = None
        hostname = self.host_names[source]
        self.node_occupancy[hostname] -= occ
    
    def send_exit(self):
        logger.info(f"send_exit: waiting on all pending recvs")
        requests = list(self.recv_requests.values())
        MPI.Request.waitall(requests)
        reqs = []
        logger.info(f"send_exit: send EXIT tag to all ranks")
        for i in range(1, comm.size):
            req = comm.isend('exit', dest=i, tag=Tags.EXIT)
            reqs.append(req)
        MPI.Request.waitall(reqs)

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('--wf-name')
    parser.add_argument('--job-file')
    parser.add_argument('--time-limit-min', type=int,
                        default=72*60)
    args = parser.parse_args()
    if args.job_file:
        job_source = jobreader.FileJobReader(args.job_file)
    else:
        job_source = jobreader.WFJobReader(args.wf_name)

    return job_source, args.time_limit_min

def master_main(host_names):
    MAX_IDLE_TIME = 10.0
    DELAY_PERIOD = 1.0
    RUN_NEW_JOBS = True
    idle_time = 0.0
    ON_EXIT = False
    
    job_source, time_limit_min = parse_args()
    manager = ResourceManager(host_names, job_source)

    def handle_sigusr1(signum, stack):
        nonlocal RUN_NEW_JOBS
        RUN_NEW_JOBS = False

    def master_exit():
        nonlocal ON_EXIT
        ON_EXIT = True
        manager.send_exit()
        logger.debug("Send_exit: master done")
        outstanding_job_pks = [j[0] for j in manager.job_assignments[1:] if j is not None]
        outstanding_jobs = BalsamJob.objects.filter(job_id__in=outstanding_job_pks)
        num_timeout = outstanding_jobs.count()
        assert num_timeout == len(outstanding_job_pks)
        logger.info(f"Shutting down with {num_timeout} jobs still running")
        
        with django.db.transaction.atomic():
            for job in outstanding_jobs:
                job.update_state('RUN_TIMEOUT', 'timed out in MPI Ensemble')
                logger.info(f"{job.cute_id} RUN_TIMEOUT")
        
        #outstanding_jobs = BalsamJob.objects.filter(job_id__in=outstanding_job_pks)
        #for job in outstanding_jobs:
        #    logger.debug(f"Assert this is RUN_TIMEOUT: {job.state}")

        logger.debug(f"master calling MPI Finalize")
        MPI.Finalize()
        logger.info(f"ensemble master exit gracefully")
        sys.exit(0)

    def handle_term(signum, stack):
        nonlocal ON_EXIT
        if ON_EXIT: return
        ON_EXIT = True
        logger.info(f"ensemble master handling term signal")
        master_exit()
        
    signal.signal(signal.SIGUSR1, handle_sigusr1)
    signal.signal(signal.SIGINT, handle_term)
    signal.signal(signal.SIGTERM, handle_term)

    remaining_timer = remaining_time_minutes(time_limit_min)
    next(remaining_timer)

    for remaining_minutes in remaining_timer:
        ran_anything = False
        got_requests = 0

        if RUN_NEW_JOBS:
            ran_anything = manager.allocate_next_jobs(remaining_minutes)
        start = time.time()
        with django.db.transaction.atomic():
            while manager.serve_request(): got_requests += 1
        elapsed = time.time() - start
        logger.debug(f"Served {got_requests} requests in {elapsed:.3f} seconds")

        if not (ran_anything or got_requests):
            time.sleep(DELAY_PERIOD)
            idle_time += DELAY_PERIOD
        else:
            idle_time = 0.0

        if idle_time > MAX_IDLE_TIME:
            if all(job == None for job in manager.job_assignments[1:]):
                logger.info(f"Nothing to do for {MAX_IDLE_TIME} seconds: quitting")
                break
    master_exit()


class Worker:
    CHECK_PERIOD=10

    def __init__(self):
        self.process = None
        self.outfile = None
        self.cuteid = None
    
    def wait_for_retcode(self):
        try: retcode = self.process.wait(timeout=self.CHECK_PERIOD)
        except TimeoutExpired: 
            return None
        else: 
            self.process = None
            self.cuteid = None
            self.outfile.close()
            return retcode

    def kill(self):
        if self.process is None: return
        self.process.terminate()
        logger.debug(f"rank {RANK} sent TERM to {self.cuteid}...waiting on shutdown")
        try: self.process.wait(timeout=self.CHECK_PERIOD)
        except TimeoutExpired: self.process.kill()
        #logger.debug(f"Term done; closing out")
        self.process = None
        self.cuteid = None
        self.outfile.close()

    def worker_exit(self):
        #logger.debug(f"worker_exit")
        self.kill()
        #logger.debug(f"worker calling MPI Finalize")
        MPI.Finalize()
        #logger.debug(f"rank {RANK} worker_exit graceful")
        sys.exit(0)

    def start_job(self, job_dict):
        workdir = job_dict['workdir']
        name = job_dict['name']
        self.cuteid = job_dict['cuteid']
        cmd = job_dict['cmd']
        envs = job_dict['envs']

        out_name = f'{name}.out'
        logger.debug(f"rank {RANK} {self.cuteid}\nPopen: {cmd}")
        timed_cmd = f"time -p ( {cmd} )"

        os.chdir(workdir)
        self.outfile = open(out_name, 'wb')
        counter = 0
        while counter < 4:
            counter += 1
            try:
                self.process = Popen(timed_cmd, stdout=self.outfile, stderr=STDOUT,
                                     cwd=workdir, env=envs, shell=True, 
                                     executable='/bin/bash')
            except Exception as e:
                self.process = None
                logger.warning(f"rank {RANK} Popen error:\n{str(e)}\nRetrying Popen...")
                sleeptime = 0.5 + 3.5*random.random()
                time.sleep(sleeptime)
            else:
                return True

        if self.process is None:
            logger.error(f"Failed to Popen after 10 attempts; marking job as failed")
            return False

    def main(self):
        tag = None
        stat = MPI.Status()

        signal.signal(signal.SIGUSR1, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)

        while tag != Tags.EXIT:
            msg = comm.recv(source=0, status=stat)
            tag = stat.tag

            if tag == Tags.NEW:
                success = self.start_job(msg)
                if not success:
                    errMsg = {'retcode' : 123 , 'tail' : 'Could not Popen from mpi_ensemble'}
                    comm.send(errMsg, dest=0, tag=Tags.ERROR)
            elif tag == Tags.KILL:
                #logger.debug(f"rank {RANK} received KILL")
                self.kill()
            elif tag == Tags.EXIT:
                #logger.debug(f"rank {RANK} received EXIT")
                self.kill()
                break

            if self.process:
                retcode = self.wait_for_retcode()
                if retcode is None:
                    message, requestTag = 'ask', Tags.ASK
                elif retcode == 0:
                    elapsed = parse_real_time(get_tail(self.outfile.name, indent=''))
                    doneMsg = {'elapsed_seconds' : elapsed}
                    message, requestTag = doneMsg, Tags.DONE
                else:
                    tail = get_tail(self.outfile.name, nlines=10)
                    errMsg = {'retcode' : retcode, 'tail' : tail}
                    message, requestTag = errMsg, Tags.ERROR
                comm.send(message, dest=0, tag=requestTag)

        self.worker_exit()


if __name__ == "__main__":
    myname = MPI.Get_processor_name()
    host_names = comm.gather(myname, root=0)
    if RANK == 0:
        master_main(host_names)
    else:
        worker = Worker()
        worker.main()
