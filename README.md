<p align="center">
<a href="https://balsam.readthedocs.io">
<img align="center" src="docs/_static/logo/small3.png" style="border: 0;">
</a>
</p>

![GitHub tag (latest by date)](https://img.shields.io/github/tag-date/balsam-alcf/balsam.svg?label=version)
[![Documentation Status](https://readthedocs.org/projects/balsam/badge/?version=latest)](https://balsam.readthedocs.io/en/latest/?badge=latest)
![PyPI - License](https://img.shields.io/pypi/l/balsam-flow.svg)

# Balsam: HPC Workflows & Edge Service


Balsam makes it easy to manage large computational campaigns on a
supercomputer. Instead of writing and submitting job scripts to the batch
scheduler, you pass application run commands to Balsam. The **service** takes
care of reserving compute resources in response to changing workload.  The
**launcher** components fetch and execute the workflows on the allocated
resources.

Balsam is designed to have minimal buy-in or cognitive overhead: it is arguably 
faster and easier to run a simple app 10 times by using Balsam than by writing
an "ensemble" shell script. 

```console
$ balsam app --name SayHello --executable "echo hello,"
$ for i in {1..10}
> do
>  balsam job --name hi$i --workflow test --application SayHello --args "world $i"
> done
$ balsam submit-launch -A Project -q Queue -t 5 -n 2 --job-mode=serial
```

Because Balsam stands in for the "script job, applications run with
**absolutely no modification**, whether or not they use MPI, run on bare metal,
or [inside a Singularity container](https://www.alcf.anl.gov/user-guides/singularity).

It is Balsam supports 
- Many independent application runs (i.e. classic ensemble jobs)
- Many instances of workflows, with inter-task dependencies forming graphs
- Dynamic workflows, where some tasks spawn other tasks with the Python API
- Remotely submit workflows and track their progress
- Multi-user workflow management

Use a command-line interface or Python API to fill a database with a few dozen
or million tasks.  The Balsam components will automatically bundle your work
and talk to the system scheduler to allocate resources.  On the inside, a pilot
*launcher* process executes your workflows and keeps you informed of what's
going on.

**Read the Balsam Documentation online at** [balsam.readthedocs.io](https://balsam.readthedocs.io/en/latest/)!


## Installation

#### Prerequisites
Balsam requires Python 3.6 or later.  You will need setuptools 39.2 or newer:

```console
$ pip install --upgrade pip setuptools
```

Some Balsam components require [mpi4py](https://github.com/mpi4py/mpi4py),  so
it is best to install Balsam in an environment with `mpi4py` already in place
and configured for your platform.  At the very least, a working MPI
implementation and `mpicc` compiler wrapper should be in the search path so
that the dependency can be automatically installed.

[cython](https://github.com/cython/cython) is also used to compile some
CPU-intensive portions of the Balsam service.  While the Cython dependency will
also be installed if it's absent, it is preferable to have an existing version
built with your platform-tuned compiler wrappers.

Finally, Balsam requires PostgreSQL version 9.6.4 or newer to be installed. You can verify
that PostgreSQL is in the search `PATH` and the version is up-to-date with

```console
$ pg_ctl --version
```

It's very easy to [get the PostgreSQL binaries](https://www.enterprisedb.com/download-postgresql-binaries) if you
don't already have them.  Simply adding the PostgreSQL `bin/` to your search
PATH should be enough to use Balsam without having to bother a system
administrator.

#### Quick setup

```console
$ pip install balsam-flow
$ balsam init ~/myWorkflow
$ source balsamactivate myWorkflow
```

Once a Balsam DB is activated, you can use the command line to manage your workflows:

```console
$ balsam app --name SayHello --executable "echo hello,"
$ balsam job --name hi --workflow test --application SayHello --args "World!"
$ balsam ls
```

## Citing Balsam
If you are referencing Balsam in a publication, please cite the following paper:

-  M. Salim, T. Uram, J.T. Childers, P. Balaprakash, V. Vishwanath, M. Papka. *Balsam: Automated Scheduling and Execution of Dynamic, Data-Intensive HPC Workflows*. In Proceedings of the 8th Workshop on Python for High-Performance and Scientific Computing. ACM Press, 2018.
