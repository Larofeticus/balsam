from collections import namedtuple
import os
import random
import sys
import time
from importlib.util import find_spec
from tests.BalsamTestCase import BalsamTestCase, cmdline

from django.conf import settings

from balsam.schedulers import Scheduler
from balsam.models import BalsamJob, ApplicationDefinition

from balsamlauncher import worker
from balsamlauncher import runners
from balsamlauncher.launcher import get_args, create_new_runners

def poll_until_returns_true(function, *, args=(), period=1.0, timeout=12.0):
    start = time.time()
    while time.time() - start < timeout:
        result = function(*args)
        if result: break
        else: time.sleep(period)
    return result

class TestMPIRunner(BalsamTestCase):
    '''start, update_jobs, finished, error/timeout handling'''
    def setUp(self):
        scheduler = Scheduler.scheduler_main
        self.host_type = scheduler.host_type
        if self.host_type == 'DEFAULT':
            config = get_args('--consume-all --num-workers 1 --max-ranks-per-node 4'.split())
        else:
            config = get_args('--consume-all')

        self.worker_group = worker.WorkerGroup(config, host_type=self.host_type,
                                               workers_str=scheduler.workers_str,
                                               workers_file=scheduler.workers_file)

        app_path = f"{sys.executable}  {find_spec('tests.mock_mpi_app').origin}"
        self.app = ApplicationDefinition()
        self.app.name = "mock_mpi"
        self.app.description = "print and sleep"
        self.app.executable = app_path
        self.app.save()
        
        # Test various worker configurations:
        self.work_configs = []
        WorkerConfig = namedtuple('WorkerConfig', ['workers', 'num_nodes',
                                                   'ranks_per_node'])
        # 2 ranks on one node
        node0 = self.worker_group[0]
        cfg = WorkerConfig([node0], 1, 2)
        self.work_configs.append(cfg)
        
        # max ranks on one node
        cfg = WorkerConfig([node0], 1, node0.max_ranks_per_node)
        self.work_configs.append(cfg)
        
        # max ranks on all nodes
        cfg = WorkerConfig(list(self.worker_group), len(self.worker_group),
                           node0.max_ranks_per_node)
        self.work_configs.append(cfg)

    
    def assert_output_file_contains_n_ranks(self, fp, n):
        '''specific check of mock_mpi_app.py output'''
        found = []
        for line in fp:
            found.append(int(line.split()[1]))
        self.assertSetEqual(set(range(n)), set(found))

    def test_normal(self):
        '''MPI application runs, returns 0, marked RUN_DONE'''
        for i, (workerslist, num_nodes, rpn) in enumerate(self.work_configs):
            job = BalsamJob()
            job.name = f"test{i}"
            job.application = self.app.name
            job.allowed_work_sites = settings.BALSAM_SITE
            job.num_nodes = num_nodes
            job.ranks_per_node = rpn
            job.save()
            self.assertEquals(job.state, 'CREATED')
            job.create_working_path()

            runner = runners.MPIRunner([job], workerslist)

            # Start the job and update state right away
            # If it didn't finish too fast, it should now be RUNNING
            runner.start()
            runner.update_jobs()
            if not runner.finished():
                self.assertEquals(job.state, 'RUNNING')

            # Now wait for the job to finish
            # On sucessful run, it should be RUN_DONE
            poll_until_returns_true(runner.finished, period=0.5)
            self.assertTrue(runner.finished())
            runner.update_jobs()
            self.assertEquals(job.state, 'RUN_DONE')

            # Check that the correct output is really there:
            outpath = runner.outfile.name
            with open(outpath) as fp:
                self.assert_output_file_contains_n_ranks(fp, num_nodes*rpn)

    
    def test_return_nonzero(self):
        '''MPI application runs, return 255, marked RUN_ERROR'''
        for i, (workerslist, num_nodes, rpn) in enumerate(self.work_configs):
            job = BalsamJob()
            job.name = f"test{i}"
            job.application = self.app.name
            job.allowed_work_sites = settings.BALSAM_SITE
            job.num_nodes = num_nodes
            job.ranks_per_node = rpn
            job.application_args = '--retcode 255' # FAIL

            job.save()
            self.assertEquals(job.state, 'CREATED')
            job.create_working_path()
            runner = runners.MPIRunner([job], workerslist)
            runner.start()
            
            poll_until_returns_true(runner.finished, period=0.5)
            runner.update_jobs()
            self.assertEquals(job.state, 'RUN_ERROR')
    
    def test_timeouts(self):
        '''MPI application runs for too long, call timeout, marked RUN_TIMEOUT'''
        for i, (workerslist, num_nodes, rpn) in enumerate(self.work_configs):
            job = BalsamJob()
            job.name = f"test{i}"
            job.application = self.app.name
            job.allowed_work_sites = settings.BALSAM_SITE
            job.num_nodes = num_nodes
            job.ranks_per_node = rpn
            job.application_args = '--sleep 10' # runs for too long

            job.save()
            self.assertEquals(job.state, 'CREATED')
            job.create_working_path()
            runner = runners.MPIRunner([job], workerslist)

            # job starts running; sleeps for 10 seconds
            runner.start()
            runner.update_jobs()
            self.assertEquals(job.state, 'RUNNING')

            # we wait just 2 seconds and the job is still going 
            time.sleep(2)
            self.assertEquals(job.state, 'RUNNING')

            # Timeout the runner
            # Now the job is marked as RUN_TIMEOUT
            runner.timeout()
            self.assertEquals(job.state, 'RUN_TIMEOUT')

            # A moment later, the runner process is indeed terminated
            term = poll_until_returns_true(runner.finished, period=0.1, 
                                           timeout=6.0)
            self.assertTrue(term)
    
class TestMPIEnsemble(BalsamTestCase):
    def setUp(self):
        scheduler = Scheduler.scheduler_main
        self.host_type = scheduler.host_type
        if self.host_type == 'DEFAULT':
            config = get_args('--consume-all --num-workers 1 --max-ranks-per-node 4'.split())
        else:
            config = get_args('--consume-all')

        self.worker_group = worker.WorkerGroup(config, host_type=self.host_type,
                                               workers_str=scheduler.workers_str,
                                               workers_file=scheduler.workers_file)

        app_path = f"{sys.executable}  {find_spec('tests.mock_serial_app').origin}"
        self.app = ApplicationDefinition()
        self.app.name = "mock_serial"
        self.app.description = "square a number"
        self.app.executable = app_path
        self.app.save()

    def testMPIEnsembleRunner(self):
        '''Several non-MPI jobs packaged into one mpi4py wrapper'''
        num_ranks = sum(w.num_nodes*w.max_ranks_per_node for w in
                        self.worker_group)
        num_jobs_per_type = num_ranks // 3

        jobs = {'qsub' : [], # these have no AppDef, will run ok
                'normal':[], # these will succeed as well
                'fail'  :[], # these should be RUN_ERROR
                'timeout':[] # these should be RUN_TIMEOUT
                }
        args = {'normal' : '',
                'fail' : '--retcode 1',
                'timeout' : '--sleep 100'
                }
        for jobtype in jobs:
            for i in range(num_jobs_per_type):
                job = BalsamJob()
                job.allowed_work_sites = settings.BALSAM_SITE
                job.name = f"{jobtype}{i}"
                if jobtype == 'qsub':
                    job.direct_command = f'echo hello world {i}'
                else:
                    job.application = self.app.name
                    job.application_args = f"{i} {args[jobtype]}"
                job.save()
                job.create_working_path()
                jobs[jobtype].append(job)

        shuffled_jobs = [j for joblist in jobs.values() for j in joblist]
        random.shuffle(shuffled_jobs)
        all_workers = list(self.worker_group)
        runner = runners.MPIEnsembleRunner(shuffled_jobs, all_workers)

        for job in shuffled_jobs:
            self.assertEqual(job.state, 'CREATED')

        # start the ensemble
        runner.start()

        # All of the qsub, normal, and fail jobs should be done quickly
        # Let's give it up to 12 seconds, checking once a second
        def check_done():
            runner.update_jobs()
            normal_done = all(j.state=='RUN_DONE' for j in jobs['normal'])
            qsub_done   = all(j.state=='RUN_DONE' for j in jobs['qsub'])
            error_done  = all(j.state=='RUN_ERROR' for j in jobs['fail'])
            return normal_done and qsub_done and error_done

        finished = poll_until_returns_true(check_done, period=1, timeout=12)
        self.assertTrue(finished)

        # And the long-running jobs in the ensemble are still going:
        self.assertTrue(all(j.state=='RUNNING' for j in jobs['timeout']))

        # So we kill the runner. The timed-out jobs are marked accordingly
        runner.timeout()
        self.assertTrue(all(j.state=='RUN_TIMEOUT' for j in jobs['timeout']))

        # Double-check that the rest of the jobs are unaffected
        self.assertTrue(all(j.state=='RUN_DONE' for j in jobs['normal']))
        self.assertTrue(all(j.state=='RUN_DONE' for j in jobs['qsub']))
        self.assertTrue(all(j.state=='RUN_ERROR' for j in jobs['fail']))


class TestRunnerGroup:
    def setUp(self):
        pass

    def test_create_runners(self):
        # Create sets of jobs intended to exercise each code path
        # in a single call to launcher.create_new_runners()
        pass
