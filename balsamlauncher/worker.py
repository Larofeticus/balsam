'''Worker: Abstraction for the compute unit running a job.
Cray: 1 worker = 1 node
BG/Q: 1 worker = 1 subblock
Default: 1 worker = local host machine

Workers contain any identifying information needed to assign jobs to specific
workers (e.g. via "mpirun") and the WorkerGroup keeps track of all busy and idle
workers available in the current launcher instance'''

import logging
logger = logging.getLogger(__name__)
class Worker:
    def __init__(self, id, *, shape=None, block=None, corner=None,
                 num_nodes=None, max_ranks_per_node=None, host_type=None):
        self.id = id
        self.shape = shape
        self.block = block
        self.corner = corner
        self.num_nodes = num_nodes
        self.max_ranks_per_node = max_ranks_per_node
        self.host_type = host_type
        self.idle = True

class WorkerGroup:
    def __init__(self, config, *, host_type=None, workers_str=None):
        self.host_type = host_type
        self.workers_str = workers_str
        self.workers = []
        self.setup = getattr(self, f"setup_{self.host_type}")
        self.setup(config)
        logger.debug(f"Built {len(self.workers)} {self.host_type} workers")

    def __iter__(self):
        return iter(self.workers)

    def __getitem__(self, i):
        return self.workers[i]

    def setup_CRAY(self, config):
        # workers_str is string like: 1001-1005,1030,1034-1200
        node_ids = []
        ranges = self.workers_str.split(',')
        for node_range in ranges:
            lo, *hi = node_range.split('-')
            lo = int(lo)
            if hi:
                hi = int(hi[0])
                node_ids.extend(list(range(lo, hi+1)))
            else:
                node_ids.append(lo)
        for id in node_ids:
            self.workers.append(Worker(id, host_type='CRAY',
                                num_nodes=1, max_ranks_per_node=16))

    def setup_BGQ(self, config):
        # Boot blocks
        # Get (block, corner, shape) args for each sub-block
        # For each worker, set num_nodes and max_ranks_per_node attributes
        pass

    def setup_DEFAULT(self, config):
        # Use command line config: num_workers, nodes_per_worker,
        # max_ranks_per_node
        for i in range(config.num_workers):
            w = Worker(i, host_type='DEFAULT',
                       num_nodes=config.nodes_per_worker,
                       max_ranks_per_node=config.max_ranks_per_node)
            self.workers.append(w)
